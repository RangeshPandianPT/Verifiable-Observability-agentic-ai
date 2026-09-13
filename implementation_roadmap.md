# Verifiable Observability: Implementation Roadmap

This document provides a phased, actionable implementation plan to address the architectural and theoretical flaws identified in the project. It bridges the gap between the current proof-of-concept and the defensible, enterprise-grade architecture required for publication.

---

## Phase 0: Foundations & Literature Catch-up
*Goal: Fix paper-blocking theoretical issues before writing more code.*

- [x] **Unify Terminology:** Decide between "Rule Compliance Rate" and "Reasoning Consistency Ratio" for the RCR acronym. Update all documentation and code to use the single chosen term.
- [x] **Formal Definitions:** Write down the exact mathematical formalizations for RCR and CCR (i.e., exact numerators, denominators, and bounding constraints).
- [x] **Literature Survey Update:** Incorporate the 2026 papers on constraint drift, multi-agent delegation safety, and the "verifier tax" (e.g., AgentSpec, Pro²Guard, Safe Bilevel Delegation) into the project's background research to correctly position its novelty.

---

## Phase 1: Symbolic Core & Execution Integrity (Single Domain)
*Goal: Prove the deterministic fast path works securely, starting with the Finance domain.*

- [x] **Implement Deterministic Checks:** Refactor the `FinanceCCM` to rely strictly on code-based, symbolic rules (e.g., dollar amount thresholds) rather than LLM-judged criteria for critical constraints.
- [x] **Implement Signed Execution Tickets (TOCTOU Fix):** 
  - Modify the Constraint Compliance Monitor (CCM) to compute a canonical hash over the approved action (tool name, parameters, trajectory ID, monotonic sequence number).
  - Issue a short-TTL, signed execution ticket (HMAC or Ed25519) alongside the `ALLOW` decision.
  - Require the execution engine to validate this ticket immediately prior to executing the tool.
- [x] **Risk-Tier-Aware Fail-Safe Policy:** 
  - Add explicit fail-open/fail-closed behaviors to `constraint_monitor.py`.
  - HIGH risk tasks must fail-closed (block) on CCM unavailability.
  - LOW risk tasks should fail-open with mandatory async audit logging.
- [x] **Chaos Testing:** Introduce tests that intentionally kill or time-out the CCM to verify the fail-safe policies act as intended.

---

## Phase 2: Statefulness & Risk Calibration
*Goal: Close structural gaps regarding memory and decision confidence.*

- [x] **Stateful Constraint Ledger:**
  - Implement a fast-lookup ledger (e.g., simulated Redis) that aggregates actions over a rolling window.
  - Add velocity checks to `FinanceCCM` (e.g., blocking 10 transfers of $4,999 to bypass a $50k limit).
- [x] **Cross-Agent Constraint Graph:** 
  - Create a shared mechanism where delegated intents inherit the parent task's constraint state, preventing an agent from simply asking a sub-agent to perform a blocked action.
- [x] **Confidence-Aware Strategy Profiler:** 
  - Update `strategy_profiler.py` to output a calibrated confidence score alongside its LOW/MEDIUM/HIGH risk tier.
  - Implement an "escalate-on-uncertainty" fallback (treat as a higher tier if confidence is low).
  - Add deterministic keyword/regex cross-checks that automatically force a HIGH tier regardless of the LLM's classification.

---

## Phase 3: Metrics Engine v2
*Goal: Replace OLS drift detection with rigorous statistical modeling and attribution.*

- [x] **Beta-Binomial Modeling:** Update `metrics.py` to model RCR/CCR turn-outcomes as Beta-Binomial rather than standard Gaussian residuals.
- [x] **Proper Changepoint Detection:** Replace the OLS slope estimation with a robust changepoint method like CUSUM or Bayesian Online Changepoint Detection (BOCPD).
- [x] **Drift Attribution:** Decompose the drift metric by rule-ID and tool-ID, so alerts can specify *which* rule compliance is degrading.
- [x] **Validation:** Create tests that synthetically inject drift (gradually worsening compliance) and measure the precision/recall of the new drift detector.

---

## Phase 4: Cross-Domain Extension & Compliance
*Goal: Generalize the core architecture and ensure legal data handling.*

- [x] **Expand Domains:** Implement `HealthcareCCM` and `CodeExecutionCCM` using the same deterministic DSL and core as the Finance domain.
- [x] **Field-Level Encryption:** Update `storage/db.py` to encrypt sensitive payloads (like PHI in Healthcare) using per-record keys before saving to SQLite.
- [x] **Crypto-Shredding:** Implement a mechanism to permanently delete the decryption key for a specific record, destroying the content while keeping the hash-chain integrity of the audit log intact (satisfying right-to-erasure laws).
- [x] **Asynchronous Semantic Path:** Split the execution pipeline so heavy semantic checks run asynchronously for LOW/MEDIUM risk tasks, while symbolic checks run synchronously.

---

## Phase 5: Adversarial Hardening
*Goal: Close advanced security vulnerabilities.*

- [ ] **Information-Flow Tracking (Taint Labels):** Add a mechanism to tag data originating from untrusted sources (e.g., an external email) and track those labels through to the tool-call arguments. Block/flag actions where sensitive tool arguments are tainted.
- [ ] **Human-in-the-loop Feedback:** Capture human decisions on `FLAG` escalations and feed them back into the system to calibrate the classifiers and update the Rule Bank.
- [ ] **Automated Red-Teaming:** Set up a sandbox loop where an adversarial agent generates attacks (outside of the design-time training set) to continuously test the CCM.

---

## Phase 6: Scaling & Evaluation
*Goal: Prepare the system for performance claims and publication.*

- [ ] **Meta-Observability Dashboard:** Add tracking for the safety system's own health (CCM latency, profiler confidence drift, false-block rates).
- [ ] **Scale Argument Prototyping:** Document the Kafka/Flink/ClickHouse target architecture. Perform load testing on the synchronous check path (10k-100k simulated calls) to provide extrapolated latency/throughput numbers.
- [ ] **Safety-Utility Tradeoff Measurement:** Design experiments to capture the "verifier tax"—measure both the attack success rate reduction *and* the false-block rate (task completion rate on benign tasks).
- [ ] **Pareto Curve Generation:** Vary the strictness of the CCM and plot the safety vs. utility Pareto frontier as a headline empirical result.
