"""
Core Pydantic v2 data models for the Verifiable Observability framework.

Schema hierarchy:
    Task → StrategyProfile
    Task → Trajectory → [Turn] → [Decision, Action, RuleCheckResult, ConstraintCheckResult]
    Rule (stored in Rule Bank, referenced by RuleCheckResult)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Domain(str, Enum):
    FINANCE = "finance"
    HEALTHCARE = "healthcare"
    CODE_EXECUTION = "code_execution"
    UNKNOWN = "unknown"


class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    DEPRECATED = "deprecated"


class ComplianceDecision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    FLAG = "FLAG"


class TrajectoryOutcome(str, Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    TRUNCATED = "truncated"
    IN_PROGRESS = "in_progress"


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class Task(BaseModel):
    """An incoming task submitted to the agent."""

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    domain: Domain
    description: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Strategy Profile
# ---------------------------------------------------------------------------


class StrategyProfile(BaseModel):
    """Behavioral baseline computed by the Strategy Profiler for a given task."""

    profile_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    domain: Domain
    task_type: str  # e.g. "routine_transfer", "portfolio_rebalance"
    risk_tier: RiskTier
    expected_turn_range: tuple[int, int] = Field(
        description="(min_turns, max_turns) expected for this task type"
    )
    active_constraint_set_id: str
    active_rule_bank_scope: list[str] = Field(
        default_factory=list,
        description="Rule IDs or tags that apply to this task",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class ObservationPattern(BaseModel):
    """
    Structured, matchable pattern for a rule's trigger condition.

    Fields act as AND-predicates: all non-None fields must match the
    incoming Decision/observation for the rule to fire.
    """

    domain: Domain | None = None
    task_type: str | None = None
    # Numeric predicates: {"amount_usd": {"gt": 10000, "lte": 100000}}
    numeric_conditions: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Keyword presence in the reasoning text
    reasoning_keywords: list[str] = Field(default_factory=list)
    # Arbitrary key=value conditions on the observation's metadata
    metadata_conditions: dict[str, Any] = Field(default_factory=dict)


class ActionPattern(BaseModel):
    """Structured description of the prescribed action for a rule."""

    tool_name: str | None = None
    required_parameters: dict[str, Any] = Field(default_factory=dict)
    forbidden_parameters: list[str] = Field(default_factory=list)
    description: str = ""


class Rule(BaseModel):
    """
    An observation→action mapping stored in the Rule Bank.

    Provenance is tracked via source_trajectory_id and verifier.
    """

    rule_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    domain: Domain
    name: str
    description: str
    observation_pattern: ObservationPattern
    prescribed_action_pattern: ActionPattern
    source_trajectory_id: str | None = None
    verification_status: VerificationStatus = VerificationStatus.PENDING
    verifier: str | None = None  # "human" or "automatic:<method>"
    deprecation_reason: str | None = None
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("updated_at", mode="before")
    @classmethod
    def set_updated_at(cls, v: Any) -> Any:
        return v or datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Agent reasoning primitives
# ---------------------------------------------------------------------------


class Action(BaseModel):
    """A dispatchable tool call produced by the agent."""

    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    raw_text: str = ""  # agent's raw output text, if applicable


class Decision(BaseModel):
    """
    A single reasoning step: the agent's chain-of-thought + intended action.

    A Turn may contain multiple Decisions if the agent revises its plan.
    """

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    turn_index: int
    reasoning: str  # chain-of-thought text
    intended_action: Action | None = None
    observation_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured metadata extracted from this decision for rule matching",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Check results
# ---------------------------------------------------------------------------


class RuleCheckResult(BaseModel):
    """Result of checking a Decision against the Rule Bank."""

    decision_id: str
    matched: bool
    rule_id: str | None = None
    rule_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    match_method: str = ""  # "structured_predicate" | "similarity" | "none"
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ViolatedConstraint(BaseModel):
    constraint_id: str
    constraint_name: str
    severity: str  # "hard" | "soft"
    details: str


class ConstraintCheckResult(BaseModel):
    """Result of the Constraint Compliance Monitor's evaluation of an Action."""

    action_id: str
    decision: ComplianceDecision
    violated_constraints: list[ViolatedConstraint] = Field(default_factory=list)
    details: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Turn & Trajectory
# ---------------------------------------------------------------------------


class TurnMetrics(BaseModel):
    rcr: float | None = None  # Reasoning Consistency Ratio
    ccr: float | None = None  # Constraint Compliance Ratio


class Turn(BaseModel):
    """One complete think→act→observe cycle."""

    turn_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    turn_index: int
    decisions: list[Decision] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    rule_checks: list[RuleCheckResult] = Field(default_factory=list)
    constraint_checks: list[ConstraintCheckResult] = Field(default_factory=list)
    tool_result: dict[str, Any] | None = None  # simulated tool output
    metrics: TurnMetrics = Field(default_factory=TurnMetrics)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Trajectory(BaseModel):
    """Full record of one agent run: task → turns → outcome."""

    trajectory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task: Task
    strategy_profile: StrategyProfile | None = None
    turns: list[Turn] = Field(default_factory=list)
    outcome: TrajectoryOutcome = TrajectoryOutcome.IN_PROGRESS
    failure_reason: str | None = None
    # Backend provenance — enables cross-model RCR/CCR comparison (Phase 5-7)
    agent_backend: str = "unknown"   # "ollama" | "anthropic" | "openai" | "scripted"
    model_name: str = "unknown"      # e.g. "llama3.2:3b", "claude-3-5-sonnet-20241022"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Agent I/O
# ---------------------------------------------------------------------------


class AgentResponse(BaseModel):
    """Raw output from an LLM adapter or scripted agent."""

    reasoning: str
    tool_name: str | None = None
    tool_parameters: dict[str, Any] = Field(default_factory=dict)
    is_final: bool = False  # True = agent signals task complete
    raw_text: str = ""
