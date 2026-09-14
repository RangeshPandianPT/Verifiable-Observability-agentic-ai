"""
Feedback Manager (Phase 5: Adversarial Hardening).

Provides a mechanism to capture human-in-the-loop decisions on FLAG escalations
and feed them back into the system to calibrate classifiers and update the Rule Bank.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from verifiable_observability.core.rule_bank import RuleBankBase
from verifiable_observability.storage.models import VerificationStatus
from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)


class HumanFeedback(BaseModel):
    trajectory_id: str
    action_id: str
    approved: bool
    feedback_notes: str = ""
    reviewer_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FeedbackManager:
    """Manages human-in-the-loop feedback for FLAG escalations."""

    def __init__(self, engine: Engine, rule_bank: RuleBankBase | None = None):
        self.engine = engine
        self.rule_bank = rule_bank
        self._init_db()

    def _init_db(self):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS human_feedback (
                        trajectory_id TEXT,
                        action_id TEXT,
                        approved BOOLEAN,
                        feedback_notes TEXT,
                        reviewer_id TEXT,
                        created_at TIMESTAMP,
                        PRIMARY KEY (trajectory_id, action_id)
                    )
                    """
                )
            )

    def submit_feedback(self, feedback: HumanFeedback) -> None:
        """
        Record a human's decision on a flagged action.
        If approved, this might eventually relax a rule.
        If rejected (blocked), this might reinforce a rule.
        """
        logger.info(
            "Received human feedback for action %s (approved=%s) from %s",
            feedback.action_id,
            feedback.approved,
            feedback.reviewer_id,
        )
        
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO human_feedback 
                    (trajectory_id, action_id, approved, feedback_notes, reviewer_id, created_at)
                    VALUES (:trajectory_id, :action_id, :approved, :feedback_notes, :reviewer_id, :created_at)
                    """
                ),
                {
                    "trajectory_id": feedback.trajectory_id,
                    "action_id": feedback.action_id,
                    "approved": feedback.approved,
                    "feedback_notes": feedback.feedback_notes,
                    "reviewer_id": feedback.reviewer_id,
                    "created_at": feedback.created_at.isoformat(),
                }
            )

        # In a real system, this would trigger an async job to retrain classifiers
        # or analyze the Rule Bank for potential updates. 
        if self.rule_bank and not feedback.approved:
            logger.info("Feedback indicated rejection. Simulating rule refinement...")
            # Example: We could tag the source trajectory's rules as needing review
            pass

    def get_pending_flags(self) -> list[dict[str, Any]]:
        """
        Retrieve a list of recent flagged actions that have no feedback yet.
        Returns a simplified dict representation for the dashboard.
        """
        try:
            pending = []
            with self.engine.begin() as conn:
                # Get the set of action_ids that already have feedback
                feedback_rows = conn.execute(text("SELECT action_id FROM human_feedback")).fetchall()
                reviewed_actions = {r._mapping["action_id"] for r in feedback_rows}

                # We need to load recent trajectories and find FLAG decisions
                from verifiable_observability.storage.db import TrajectoryStore
                store = TrajectoryStore(self.engine)
                summaries = store.list_trajectories(limit=50)
                
                for s in summaries:
                    try:
                        t = store.load(s["trajectory_id"])
                        if t:
                            for turn in t.turns:
                                for cc in turn.constraint_checks:
                                    if cc.decision == "FLAG" and cc.action_id not in reviewed_actions:
                                        pending.append({
                                            "action_id": cc.action_id,
                                            "details": cc.details,
                                            "checked_at": cc.checked_at.isoformat(),
                                        })
                    except Exception as e:
                        logger.warning(f"Failed to parse trajectory {s['trajectory_id']}: {e}")

            return pending
        except Exception as e:
            logger.error(f"Error fetching pending flags: {e}")
            return []
