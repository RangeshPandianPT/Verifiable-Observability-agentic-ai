"""
Verifiable Observability - Real-time behavioral verification for multi-turn LLM agents.

Core Architecture:
  1. Strategy Profiler  - classifies task structure, domain, and risk tier
  2. Rule Bank          - versioned store of observation->action mappings and matchers
  3. Constraint Compliance Monitor (CCM) - deterministic execution-time firewall
  4. Audit Verifier     - cryptographic hash-chaining and Merkle compliance proofs

Key Metrics:
  - RCR (Reasoning Consistency Ratio)  - fraction of decisions traceable to verified rules
  - CCR (Constraint Compliance Ratio)  - fraction of actions satisfying all active constraints
"""

from __future__ import annotations

from verifiable_observability.core.alerting import (
    AlerterBase,
    CompositeAlerter,
    WebhookAlerter,
)
from verifiable_observability.core.audit_verifier import (
    AuditVerificationResult,
    AuditVerifier,
    ComplianceCertificate,
    MerkleTree,
)
from verifiable_observability.core.constraint_graph import ConstraintGraph
from verifiable_observability.core.constraint_ledger import ConstraintLedger
from verifiable_observability.core.constraint_monitor import (
    ConstraintComplianceMonitorBase,
    StubCCM,
    build_ccm,
)
from verifiable_observability.core.execution_ticket import (
    ExecutionTicket,
    TicketIssuer,
    TicketValidationError,
    TicketValidator,
    canonical_action_hash,
)
from verifiable_observability.core.fail_safe import (
    FailSafePolicy,
    apply_fail_safe,
    get_fail_safe_policy,
)
from verifiable_observability.core.matching import (
    CONFIDENCE_THRESHOLD,
    HybridMatcher,
    MatchDiagnostics,
    MatcherBase,
    SimilarityMatcher,
    StructuredPredicateMatcher,
)
from verifiable_observability.core.metrics import (
    BasicMetricsEngine,
    BetaBinomialModel,
    CUSUMDetector,
    MetricsEngineBase,
)
from verifiable_observability.core.orchestrator import (
    Orchestrator,
    OrchestratorCallback,
)
from verifiable_observability.core.rule_bank import (
    RuleBank,
    RuleBankBase,
    StubRuleBank,
)
from verifiable_observability.core.rule_verifier import (
    ConflictSeverity,
    ConflictType,
    RuleConflict,
    RuleVerificationReport,
    RuleVerifier,
)
from verifiable_observability.core.strategy_profiler import (
    StrategyProfile,
    StrategyProfiler,
    StrategyProfilerBase,
    StubStrategyProfiler,
)
from verifiable_observability.storage.db import (
    RuleStore,
    TrajectoryStore,
    create_db_engine,
)
from verifiable_observability.storage.models import (
    Action,
    ActionPattern,
    ComplianceDecision,
    ConstraintCheckResult,
    Decision,
    Domain,
    ObservationPattern,
    RiskTier,
    Rule,
    RuleCheckResult,
    Task,
    Trajectory,
    TrajectoryOutcome,
    Turn,
    VerificationStatus,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Core orchestration
    "Orchestrator",
    "OrchestratorCallback",
    # Profiler
    "StrategyProfile",
    "StrategyProfiler",
    "StrategyProfilerBase",
    "StubStrategyProfiler",
    # Rule bank & matchers
    "RuleBank",
    "RuleBankBase",
    "StubRuleBank",
    "MatcherBase",
    "StructuredPredicateMatcher",
    "SimilarityMatcher",
    "HybridMatcher",
    "MatchDiagnostics",
    "CONFIDENCE_THRESHOLD",
    "RuleVerifier",
    "RuleVerificationReport",
    "RuleConflict",
    "ConflictSeverity",
    "ConflictType",
    # Enforcement & CCM
    "ConstraintComplianceMonitorBase",
    "StubCCM",
    "build_ccm",
    "ConstraintLedger",
    "ConstraintGraph",
    "FailSafePolicy",
    "get_fail_safe_policy",
    "apply_fail_safe",
    # Cryptographic integrity & tickets
    "ExecutionTicket",
    "TicketIssuer",
    "TicketValidator",
    "TicketValidationError",
    "canonical_action_hash",
    "AuditVerifier",
    "AuditVerificationResult",
    "ComplianceCertificate",
    "MerkleTree",
    # Metrics & alerting
    "BasicMetricsEngine",
    "MetricsEngineBase",
    "BetaBinomialModel",
    "CUSUMDetector",
    "AlerterBase",
    "WebhookAlerter",
    "CompositeAlerter",
    # Storage & DB
    "create_db_engine",
    "TrajectoryStore",
    "RuleStore",
    # Models
    "Task",
    "Domain",
    "RiskTier",
    "Trajectory",
    "TrajectoryOutcome",
    "Turn",
    "Decision",
    "Action",
    "Rule",
    "ObservationPattern",
    "ActionPattern",
    "ComplianceDecision",
    "ConstraintCheckResult",
    "RuleCheckResult",
    "VerificationStatus",
]
