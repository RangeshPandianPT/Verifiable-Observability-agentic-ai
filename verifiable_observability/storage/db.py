"""
SQLite persistence layer for Verifiable Observability.

Uses SQLAlchemy Core (not ORM) so the schema is fully transparent
and directly queryable. Trajectories and Rules are stored as JSON blobs
for flexibility during the research phase; a production system could
normalize this further.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import BaseModel
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import StaticPool

from verifiable_observability.storage.models import Rule, Trajectory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

metadata = sa.MetaData()

trajectories_table = sa.Table(
    "trajectories",
    metadata,
    sa.Column("trajectory_id", sa.String, primary_key=True),
    sa.Column("task_id", sa.String, nullable=False, index=True),
    sa.Column("domain", sa.String, nullable=False, index=True),
    sa.Column("outcome", sa.String, nullable=False, index=True),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("completed_at", sa.String, nullable=True),
    sa.Column("data", sa.Text, nullable=False),  # full JSON blob (encrypted)
)

encryption_keys_table = sa.Table(
    "encryption_keys",
    metadata,
    sa.Column("trajectory_id", sa.String, primary_key=True),
    sa.Column("key", sa.String, nullable=False),
)

rules_table = sa.Table(
    "rules",
    metadata,
    sa.Column("rule_id", sa.String, primary_key=True),
    sa.Column("domain", sa.String, nullable=False, index=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("verification_status", sa.String, nullable=False, index=True),
    sa.Column("version", sa.Integer, nullable=False, default=1),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("updated_at", sa.String, nullable=False),
    sa.Column("data", sa.Text, nullable=False),  # full JSON blob
)


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path("verifiable_observability.db")


def create_db_engine(db_path: Path | str | None = None) -> Engine:
    """
    Create (or open) a SQLite engine and ensure all tables exist.

    Pass db_path=":memory:" for an in-memory database (useful in tests).
    """
    raw = str(db_path) if db_path else str(_DEFAULT_DB_PATH)
    if raw == ":memory:":
        url = "sqlite:///:memory:"
        engine = create_engine(
            url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,  # share single in-memory DB across all connections
        )
    else:
        path = Path(raw)
        url = f"sqlite:///{path.resolve()}"
        engine = create_engine(url, echo=False, future=True)
    metadata.create_all(engine)
    logger.info("Database ready at %s", url)
    return engine


# ---------------------------------------------------------------------------
# Trajectory store
# ---------------------------------------------------------------------------


class TrajectoryStore:
    """Persist and retrieve Trajectory objects."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, trajectory: Trajectory) -> None:
        """Insert or replace a trajectory (encrypted)."""
        key = Fernet.generate_key()
        f = Fernet(key)
        
        data_json = trajectory.model_dump_json()
        encrypted_data = f.encrypt(data_json.encode('utf-8')).decode('utf-8')
        
        row = {
            "trajectory_id": trajectory.trajectory_id,
            "task_id": trajectory.task.task_id,
            "domain": trajectory.task.domain.value,
            "outcome": trajectory.outcome.value,
            "created_at": trajectory.created_at.isoformat(),
            "completed_at": (
                trajectory.completed_at.isoformat()
                if trajectory.completed_at
                else None
            ),
            "data": encrypted_data,
        }
        with self._engine.begin() as conn:
            # upsert: delete + insert (SQLite compatible)
            conn.execute(
                trajectories_table.delete().where(
                    trajectories_table.c.trajectory_id == trajectory.trajectory_id
                )
            )
            conn.execute(trajectories_table.insert().values(**row))
            
            # upsert encryption key
            conn.execute(
                encryption_keys_table.delete().where(
                    encryption_keys_table.c.trajectory_id == trajectory.trajectory_id
                )
            )
            conn.execute(
                encryption_keys_table.insert().values(
                    trajectory_id=trajectory.trajectory_id,
                    key=key.decode('utf-8')
                )
            )
        logger.debug("Saved trajectory %s", trajectory.trajectory_id)

    def load(self, trajectory_id: str) -> Trajectory | None:
        """Load a Trajectory by ID, decrypting it. Raises ValueError if shredded."""
        with self._engine.connect() as conn:
            row = conn.execute(
                trajectories_table.select().where(
                    trajectories_table.c.trajectory_id == trajectory_id
                )
            ).fetchone()
            
            if row is None:
                return None
            
            key_row = conn.execute(
                encryption_keys_table.select().where(
                    encryption_keys_table.c.trajectory_id == trajectory_id
                )
            ).fetchone()
            
            if key_row is None:
                raise ValueError(f"Crypto-shredded: No decryption key found for trajectory {trajectory_id}")
            
            f = Fernet(key_row.key.encode('utf-8'))
            try:
                decrypted_data = f.decrypt(row.data.encode('utf-8')).decode('utf-8')
            except Exception as e:
                # Fallback for unencrypted older rows if needed
                try:
                    return Trajectory.model_validate_json(row.data)
                except Exception:
                    raise ValueError(f"Failed to decrypt data for trajectory {trajectory_id}: {e}")
                
        return Trajectory.model_validate_json(decrypted_data)

    def crypto_shred(self, trajectory_id: str) -> bool:
        """
        Permanently delete the decryption key for a specific record, destroying 
        the content while keeping the hash-chain integrity of the audit log intact.
        Returns True if a key was deleted, False otherwise.
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                encryption_keys_table.delete().where(
                    encryption_keys_table.c.trajectory_id == trajectory_id
                )
            )
            return result.rowcount > 0

    def list_trajectories(
        self,
        domain: str | None = None,
        outcome: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return lightweight summary rows (no full data blob)."""
        stmt = sa.select(
            trajectories_table.c.trajectory_id,
            trajectories_table.c.task_id,
            trajectories_table.c.domain,
            trajectories_table.c.outcome,
            trajectories_table.c.created_at,
            trajectories_table.c.completed_at,
        )
        if domain:
            stmt = stmt.where(trajectories_table.c.domain == domain)
        if outcome:
            stmt = stmt.where(trajectories_table.c.outcome == outcome)
        stmt = stmt.order_by(trajectories_table.c.created_at.desc()).limit(limit)

        with self._engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Rule store
# ---------------------------------------------------------------------------


class RuleStore:
    """Persist and retrieve Rule objects for the Rule Bank."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, rule: Rule) -> None:
        """Insert or replace a rule."""
        row = {
            "rule_id": rule.rule_id,
            "domain": rule.domain.value,
            "name": rule.name,
            "verification_status": rule.verification_status.value,
            "version": rule.version,
            "created_at": rule.created_at.isoformat(),
            "updated_at": rule.updated_at.isoformat(),
            "data": rule.model_dump_json(),
        }
        with self._engine.begin() as conn:
            conn.execute(
                rules_table.delete().where(
                    rules_table.c.rule_id == rule.rule_id
                )
            )
            conn.execute(rules_table.insert().values(**row))
        logger.debug("Saved rule %s (%s)", rule.rule_id, rule.name)

    def load(self, rule_id: str) -> Rule | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                rules_table.select().where(rules_table.c.rule_id == rule_id)
            ).fetchone()
        if row is None:
            return None
        return Rule.model_validate_json(row.data)

    def list_rules(
        self,
        domain: str | None = None,
        status: str | None = None,
    ) -> list[Rule]:
        stmt = sa.select(rules_table.c.data)
        if domain:
            stmt = stmt.where(rules_table.c.domain == domain)
        if status:
            stmt = stmt.where(rules_table.c.verification_status == status)
        stmt = stmt.order_by(rules_table.c.created_at)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [Rule.model_validate_json(r.data) for r in rows]

    def delete(self, rule_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                rules_table.delete().where(rules_table.c.rule_id == rule_id)
            )
