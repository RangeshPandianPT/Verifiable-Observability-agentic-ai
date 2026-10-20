"""
High-Throughput Streaming Buffer & Micro-Batch Pipeline.

Phase 5/6 Architectural Deliverable:
  "SQLite cannot survive contact with astronomical scale... Ingest via an
   append-only event stream buffer / batcher with backpressure and flush to
   persistent storage, maintaining hash-chain integrity at the stream level."

Architecture:
  1. StreamEvent: Canonical envelope wrapping all trajectory turns, decisions,
     and constraint checks with monotonic sequence numbers.
  2. In-Memory Ring Buffer / WAL: Thread-safe queue supporting configurable
     backpressure strategies (BLOCK, DROP_OLDEST, DROP_NEWEST, RAISE_ERROR).
  3. Micro-Batch Flusher: Background asynchronous worker thread that seals batches
     when buffer reaches batch_size or on flush_interval_seconds timeout.
  4. Merkle Root Chunk Sealing: Each flushed micro-batch calculates a Merkle Root
     chained to the previous batch hash, ensuring tamper evidence survives
     streaming ingestion before writing to permanent storage.
  5. Telemetry & Metrics: Live tracking of event ingestion rates, backpressure events,
     and batch flush latency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    TURN_START = "turn_start"
    DECISION_EVALUATED = "decision_evaluated"
    ACTION_SUBMITTED = "action_submitted"
    CONSTRAINT_CHECKED = "constraint_checked"
    TOOL_DISPATCHED = "tool_dispatched"
    TRAJECTORY_COMPLETED = "trajectory_completed"
    SECURITY_ALERT = "security_alert"


class BackpressurePolicy(str, Enum):
    BLOCK = "block"
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    RAISE_ERROR = "raise_error"


@dataclass
class StreamEvent:
    """Individual event envelope submitted into the streaming pipeline."""

    event_id: str
    event_type: EventType
    trajectory_id: str
    domain: str
    timestamp: float
    payload: dict[str, Any]
    event_hash: str = ""

    @classmethod
    def create(
        cls,
        event_type: EventType,
        trajectory_id: str,
        domain: str,
        payload: dict[str, Any],
    ) -> StreamEvent:
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        now_ts = time.time()
        canonical_str = f"{event_id}|{event_type.value}|{trajectory_id}|{domain}|{now_ts}|{json.dumps(payload, sort_keys=True, default=str)}"
        h = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
        return cls(
            event_id=event_id,
            event_type=event_type,
            trajectory_id=trajectory_id,
            domain=domain,
            timestamp=now_ts,
            payload=payload,
            event_hash=h,
        )


@dataclass
class StreamMicroBatch:
    """
    A sealed micro-batch of events ready for persistent storage or downstream dispatch.

    Contains a cryptographic Merkle root of all constituent events.
    """

    batch_id: str
    batch_index: int
    events: list[StreamEvent]
    merkle_root: str
    prev_batch_hash: str
    sealed_at: float
    flush_latency_ms: float = 0.0

    @property
    def event_count(self) -> int:
        return len(self.events)


# ---------------------------------------------------------------------------
# Micro-Batch Merkle Calculation
# ---------------------------------------------------------------------------


def compute_batch_merkle_root(events: list[StreamEvent]) -> str:
    """Compute binary Merkle root over list of event hashes."""
    if not events:
        return hashlib.sha256(b"").hexdigest()

    current = [e.event_hash for e in events]
    while len(current) > 1:
        next_level: list[str] = []
        for i in range(0, len(current), 2):
            left = current[i]
            right = current[i + 1] if i + 1 < len(current) else current[i]
            combined = hashlib.sha256(f"{left}:{right}".encode()).hexdigest()
            next_level.append(combined)
        current = next_level
    return current[0]


# ---------------------------------------------------------------------------
# High-Throughput Event Stream Buffer
# ---------------------------------------------------------------------------


class EventStreamBuffer:
    """
    High-concurrency streaming buffer with asynchronous micro-batch flushing.

    Acts as the entry buffer between the synchronous agent execution loop
    and asynchronous storage/analytics layers.
    """

    def __init__(
        self,
        batch_size: int = 50,
        flush_interval_seconds: float = 0.5,
        max_queue_size: int = 5000,
        backpressure: BackpressurePolicy = BackpressurePolicy.BLOCK,
        on_batch_flushed: Callable[[StreamMicroBatch], None] | None = None,
    ) -> None:
        self.batch_size = batch_size
        self.flush_interval = flush_interval_seconds
        self.max_queue_size = max_queue_size
        self.backpressure = backpressure
        self.on_batch_flushed = on_batch_flushed

        self._queue: queue.Queue[StreamEvent] = queue.Queue(maxsize=max_queue_size)
        self._flushed_batches: list[StreamMicroBatch] = []
        self._prev_batch_hash = hashlib.sha256(b"vo-stream-genesis").hexdigest()
        self._batch_counter = 0

        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Telemetry metrics
        self.stats = {
            "events_ingested": 0,
            "events_dropped": 0,
            "batches_flushed": 0,
            "total_flushed_events": 0,
            "avg_flush_latency_ms": 0.0,
        }

    # -----------------------------------------------------------------------
    # Lifecycle (Start / Stop)
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the background micro-batch flusher thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._flusher_loop,
            name="EventStreamFlusher",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info(
            "EventStreamBuffer worker started (batch_size=%d, interval=%.2fs)",
            self.batch_size,
            self.flush_interval,
        )

    def stop(self, timeout: float = 3.0) -> None:
        """Stop background flusher and flush any remaining events."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
        self.flush_now()
        logger.info("EventStreamBuffer stopped. Total flushed batches: %d", self.stats["batches_flushed"])

    # -----------------------------------------------------------------------
    # Ingestion API
    # -----------------------------------------------------------------------

    def emit(self, event: StreamEvent) -> bool:
        """
        Submit a StreamEvent into the buffer, handling backpressure.

        Returns True if event was accepted; False if dropped.
        """
        try:
            if self.backpressure == BackpressurePolicy.BLOCK:
                self._queue.put(event, block=True, timeout=1.0)
            elif self.backpressure == BackpressurePolicy.RAISE_ERROR:
                self._queue.put_nowait(event)
            elif self.backpressure == BackpressurePolicy.DROP_NEWEST:
                if self._queue.full():
                    with self._lock:
                        self.stats["events_dropped"] += 1
                    return False
                self._queue.put_nowait(event)
            elif self.backpressure == BackpressurePolicy.DROP_OLDEST:
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                        with self._lock:
                            self.stats["events_dropped"] += 1
                    except queue.Empty:
                        pass
                self._queue.put_nowait(event)

            with self._lock:
                self.stats["events_ingested"] += 1
            return True

        except queue.Full:
            with self._lock:
                self.stats["events_dropped"] += 1
            if self.backpressure == BackpressurePolicy.RAISE_ERROR:
                raise BufferError(f"EventStreamBuffer full ({self.max_queue_size} events)")
            return False

    def emit_event(
        self,
        event_type: EventType,
        trajectory_id: str,
        domain: str,
        payload: dict[str, Any],
    ) -> bool:
        """Convenience method to construct and emit a StreamEvent."""
        evt = StreamEvent.create(event_type, trajectory_id, domain, payload)
        return self.emit(evt)

    # -----------------------------------------------------------------------
    # Flushing Logic
    # -----------------------------------------------------------------------

    def flush_now(self) -> StreamMicroBatch | None:
        """Immediately drain available events into a sealed StreamMicroBatch."""
        events_to_flush: list[StreamEvent] = []
        while not self._queue.empty():
            try:
                events_to_flush.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if not events_to_flush:
            return None

        return self._seal_batch(events_to_flush)

    def _seal_batch(self, events: list[StreamEvent]) -> StreamMicroBatch:
        t0 = time.perf_counter()
        merkle_root = compute_batch_merkle_root(events)

        with self._lock:
            self._batch_counter += 1
            batch_idx = self._batch_counter
            prev_hash = self._prev_batch_hash

            # Chain this batch hash
            batch_hash_payload = f"{batch_idx}|{merkle_root}|{prev_hash}|{len(events)}"
            new_batch_hash = hashlib.sha256(batch_hash_payload.encode()).hexdigest()
            self._prev_batch_hash = new_batch_hash

            flush_time_ms = (time.perf_counter() - t0) * 1000.0

            batch = StreamMicroBatch(
                batch_id=f"batch-{uuid.uuid4().hex[:12]}",
                batch_index=batch_idx,
                events=events,
                merkle_root=merkle_root,
                prev_batch_hash=prev_hash,
                sealed_at=time.time(),
                flush_latency_ms=round(flush_time_ms, 3),
            )
            self._flushed_batches.append(batch)

            # Update stats
            self.stats["batches_flushed"] += 1
            self.stats["total_flushed_events"] += len(events)
            prev_avg = self.stats["avg_flush_latency_ms"]
            n = self.stats["batches_flushed"]
            self.stats["avg_flush_latency_ms"] = round(
                ((prev_avg * (n - 1)) + flush_time_ms) / n, 3
            )

        # Notify callback outside lock
        if self.on_batch_flushed:
            try:
                self.on_batch_flushed(batch)
            except Exception as exc:
                logger.error("Error in on_batch_flushed callback: %s", exc)

        return batch

    def _flusher_loop(self) -> None:
        """Background loop flushing batches periodically or on batch_size threshold."""
        while not self._stop_event.is_set():
            # Wait for flush interval or until stop
            self._stop_event.wait(timeout=self.flush_interval)

            if self._queue.qsize() >= self.batch_size or self._queue.qsize() > 0:
                # Collect up to batch_size
                collected: list[StreamEvent] = []
                while len(collected) < self.batch_size and not self._queue.empty():
                    try:
                        collected.append(self._queue.get_nowait())
                    except queue.Empty:
                        break

                if collected:
                    self._seal_batch(collected)

    # -----------------------------------------------------------------------
    # Inspection API
    # -----------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def flushed_batches(self) -> list[StreamMicroBatch]:
        with self._lock:
            return list(self._flushed_batches)
