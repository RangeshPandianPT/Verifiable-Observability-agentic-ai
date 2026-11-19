# Verifiable Observability

[![CI](https://github.com/RangeshPandianPT/Verifiable-Observability-agentic-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/RangeshPandianPT/Verifiable-Observability-agentic-ai/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

**Real-time behavioral verification and cryptographic enforcement framework for multi-turn LLM agents.**

A publication-grade framework that adds three runtime verification layers to any LLM agent loop — checking reasoning consistency and enforcing deterministic constraints *before* actions are dispatched, while generating tamper-evident cryptographic compliance certificates.

---

## Key Capabilities

- **3-Layer Runtime Firewall**: Intercepts reasoning steps (RCR) and tool actions (CCR) synchronously before execution.
- **Cryptographic Auditability**: SHA-256 turn hash chains, Merkle compliance trees, and Ed25519/HMAC execution tickets to eliminate Time-of-Check to Time-of-Use (TOCTOU) exploits.
- **Verifiable Compliance Certificates (VCC)**: Generates and verifies digitally signed certificates for auditors and regulators offline.
- **Explainable Matching Engine**: Structured symbolic predicates, stopword-filtered semantic matching, and `HybridMatcher` with transparent `MatchDiagnostics`.
- **Pre-flight System Diagnostics**: Built-in CLI self-diagnostics (`vo check`) inspecting runtime, storage, rule banks, cryptography, and LLM providers.
- **Pareto Safety-Utility Optimization**: Quantifies the empirical "verifier tax", Pareto frontiers, and ASR reduction across real LLM backends.

---

## Architecture

```
User Prompt
    │
    ▼
[Pre-flight Input Guardrail] ──► Block malicious injection before agent starts
    │ Passed
    ▼
[Strategy Profiler] ──────────► Task Domain, Task Type, Calibrated Risk Tier
    │
    ▼
Agent Proposes Decision
    │
    ├──► [Rule Bank & Matcher] ──► RuleCheckResult (RCR) & MatchDiagnostics
    │
    ▼
Agent Proposes Tool Action
    │
    ├──► [CCM Execution Firewall] ─► ConstraintCheckResult (CCR)
            ├── ALLOW ──► Issues Signed Execution Ticket ──► Tool Dispatches
            ├── BLOCK ──► Halts Action (or triggers Self-Healing Remediation)
            └── FLAG  ──► Escalates to Human Review / Async Audit
    │
    ▼
[Metrics & Cryptographic Engine]
    ├── Beta-Binomial RCR/CCR Posteriors + CUSUM Drift Detection
    ├── Turn Hash Chaining (SHA-256)
    └── Merkle Compliance Tree & Verifiable Compliance Certificate (VCC)
```

---

## Formal Metrics

- **RCR (Reasoning Consistency Ratio)**: Fraction of internal reasoning steps mapped to verified SOP rules:
  $$\text{RCR} = \frac{\text{Reasoning Steps Mapped to Verified Rules}}{\text{Total Extracted Reasoning Steps}}$$
- **CCR (Constraint Compliance Ratio)**: Fraction of tool calls satisfying all active domain constraints:
  $$\text{CCR} = \frac{\text{Actions Satisfying Constraints}}{\text{Total Dispatched Actions}}$$

---

## Installation

```bash
# Clone the repository
git clone https://github.com/RangeshPandianPT/Verifiable-Observability-agentic-ai.git
cd verifiable-observability

# Install in editable mode with development dependencies
pip install -e ".[dev]"
```

Or using `uv`:
```bash
uv pip install -e ".[dev]"
```

---

## Quickstart

### Python Programmatic SDK

You can run the full verification loop in Python using the unified public API:

```python
import verifiable_observability as vo
from verifiable_observability.agent.adapter import AgentResponse, ScriptedAgentAdapter
from verifiable_observability.core.constraint_monitor import FinanceCCM
from verifiable_observability.core.execution_ticket import TicketIssuer, TicketValidator
from verifiable_observability.simulation.domains.finance.seed_rules import load_seed_rules_into_bank

# 1. Setup in-memory store and seed SOP rules
engine = vo.create_db_engine(":memory:")
rule_bank = vo.RuleBank(vo.RuleStore(engine))
load_finance_rules(rule_bank, auto_verify=True)

# 2. Setup cryptographic ticket verification
secret = b"your-production-secret-key-32b!"
issuer = TicketIssuer(secret=secret)
validator = TicketValidator(secret=secret)

# 3. Setup Orchestrator
orchestrator = vo.Orchestrator(
    strategy_profiler=vo.StrategyProfiler(),
    rule_bank=rule_bank,
    ccm=FinanceCCM(ticket_issuer=issuer),
    agent_adapter=ScriptedAgentAdapter([
        AgentResponse(
            reasoning="Verifying balance before executing transfer.",
            tool_name="verify_balance",
            tool_parameters={"account_id": "ACC-001"},
        ),
        AgentResponse(
            reasoning="Balance verified. Transferring $1,500.",
            tool_name="execute_transfer",
            tool_parameters={"source_account": "ACC-001", "destination_account": "ACC-002", "amount": 1500.0},
            is_final=True,
        ),
    ]),
    trajectory_store=vo.TrajectoryStore(engine),
    ticket_validator=validator,
)

# 4. Run trajectory
task = vo.Task(description="Transfer $1,500 from ACC-001 to ACC-002", domain=vo.Domain.FINANCE)
trajectory = orchestrator.run(task)
print(f"Status: {trajectory.outcome.value} in {len(trajectory.turns)} turns")

# 5. Issue offline verifiable compliance certificate
auditor = vo.AuditVerifier(signing_secret=secret)
cert = auditor.generate_compliance_certificate(trajectory)
print(f"Merkle Root: {cert.merkle_root} | Valid: {auditor.verify_certificate(cert)}")
```

To run the complete executable quickstart demo:
```bash
python examples/quickstart.py
```

---

## CLI Reference

### System Health & Diagnostics
Run comprehensive pre-flight verification across Python runtime, SQLite schemas, domain rule coverage, and cryptographic subsystems:
```bash
vo check
# or
vo health
```

### Cryptographic Audit & Certificate Management
Perform offline hash chain audit and generate a cryptographically signed Verifiable Compliance Certificate (VCC):
```bash
# Audit latest trajectory and view certificate in console
vo audit-cert

# Export certificate to a JSON file for regulators
vo audit-cert --out compliance_cert.json

# Verify authenticity of an exported certificate completely offline
vo audit-cert --verify-file compliance_cert.json
```

### Formal Rule Bank Verification
Check declarative rules in the Rule Bank for logical contradictions, parameter conflicts, subsumption, or coverage gaps:
```bash
vo verify-rules --domain finance
```

### Running Real LLM Agents
```bash
# Local zero-cost inference via Ollama (default)
vo run "Transfer $500 from ACC-001 to ACC-002" --backend ollama --model llama3.2:3b

# Cloud inference via Anthropic Claude or OpenAI GPT-4o
vo run "Prescribe amoxicillin for patient PAT-001" --domain healthcare --backend anthropic
vo run "Review this script for security flaws" --domain code_execution --backend openai
```

### Behavioral Regimes & Drift Detection
```bash
# Run simulated degradation regimes
vo regime run compliant
vo regime run mild_drift
vo regime run adversarial_injection

# Trajectory analysis and drift attribution
vo analyze trajectories --drift-only
```

### Verifier Tax Benchmark & Pareto Frontier
Quantify the security-utility tradeoff across Unprotected, Regex Guardrail, and Verifiable Observability:
```bash
vo benchmark-tax --bootstrap 500
```

---

## Repository Structure

```
verifiable_observability/
├── __init__.py                # Top-level public package exports (63 core components)
├── core/
│   ├── orchestrator.py        # Central runtime loop & intervention handler
│   ├── strategy_profiler.py   # Domain and risk tier profiler with confidence scoring
│   ├── rule_bank.py           # Auditable SOP store with in-memory caching
│   ├── matching.py            # Structured, similarity, and hybrid matchers + diagnostics
│   ├── rule_verifier.py       # Formal contradiction, subsumption & coverage analyzer
│   ├── constraint_monitor.py  # Deterministic CCM firewalls for 4 domains
│   ├── constraint_ledger.py   # Stateful rolling-window velocity checks
│   ├── constraint_graph.py    # Cross-agent delegation constraint inheritance
│   ├── execution_ticket.py    # HMAC/Ed25519 execution tickets (anti-TOCTOU)
│   ├── audit_verifier.py      # Merkle tree & SHA-256 hash chain compliance auditor
│   ├── metrics.py             # Beta-Binomial RCR/CCR posteriors & CUSUM drift detection
│   └── alerting.py            # Webhook and composite alerting
├── agent/                     # Modular adapters for Ollama, Anthropic Claude, OpenAI GPT-4o
├── simulation/
│   ├── domains/               # Verified seed rules for Finance, Healthcare, Code, E-Commerce
│   └── regimes/               # Behavioral regimes (Compliant, Mild Drift, Adversarial, Tool Failure)
├── eval/                      # Evaluation harness, Pareto frontier, and red-teaming arena
├── storage/                   # Pydantic v2 schemas and SQLite persistence
└── cli/                       # Rich interactive CLI (vo run, check, audit-cert, demo, etc.)
examples/
└── quickstart.py              # End-to-end runnable SDK demonstration
tests/                         # Complete unit, integration, and chaos test suites
```

---

## Running Tests

Run the test suite across all subsystems:

```bash
pytest tests/ -v
```

---

## License

This project is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
