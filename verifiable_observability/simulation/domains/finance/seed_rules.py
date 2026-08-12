"""
Finance Domain — Seed Rule Set (Phase 1)

15 rules covering the three Finance task types:
  - routine_transfer
  - portfolio_rebalance
  - high_value_trade

Each rule is an observation→action mapping derived from plausible
"if observation X then approved action Y" pairs a finance agent would follow.

All rules are loaded with verification_status=PENDING by default;
call RuleBank.verify_rule(rule_id, verifier) to promote them to VERIFIED.

Usage::

    from verifiable_observability.simulation.domains.finance.seed_rules import (
        get_seed_rules,
        load_seed_rules_into_bank,
    )
    rules = get_seed_rules()
    load_seed_rules_into_bank(rule_bank, auto_verify=True)
"""

from __future__ import annotations

from verifiable_observability.core.rule_bank import RuleBankBase
from verifiable_observability.storage.models import (
    ActionPattern,
    Domain,
    ObservationPattern,
    Rule,
)

# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------


def get_seed_rules() -> list[Rule]:
    """Return the 15 Finance seed rules (unsaved, status=PENDING)."""
    return [

        # ===========================
        # ROUTINE TRANSFER (5 rules)
        # ===========================

        Rule(
            rule_id="fin-rt-001",
            domain=Domain.FINANCE,
            name="routine_transfer_verify_balance_first",
            description=(
                "Before initiating any transfer, the agent must first verify "
                "the account balance to confirm sufficient funds."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="routine_transfer",
                reasoning_keywords=["transfer", "balance"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="get_account_balance",
                description="Call get_account_balance before executing a transfer.",
            ),
        ),

        Rule(
            rule_id="fin-rt-002",
            domain=Domain.FINANCE,
            name="routine_transfer_below_10k_auto_approve",
            description=(
                "Transfers below $10,000 on approved instruments may be "
                "executed automatically without additional review."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="routine_transfer",
                numeric_conditions={"amount_usd": {"lt": 10000}},
                reasoning_keywords=["transfer"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="execute_transfer",
                description="Execute the transfer directly for amounts under $10k.",
            ),
        ),

        Rule(
            rule_id="fin-rt-003",
            domain=Domain.FINANCE,
            name="routine_transfer_log_transaction",
            description=(
                "Every completed transfer must be logged to the transaction "
                "ledger immediately after execution."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="routine_transfer",
                reasoning_keywords=["transfer", "log"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="log_transaction",
                description="Log the transaction after transfer execution.",
            ),
        ),

        Rule(
            rule_id="fin-rt-004",
            domain=Domain.FINANCE,
            name="routine_transfer_approved_instruments_only",
            description=(
                "Transfers must only use instruments on the approved instrument list. "
                "If the instrument is not approved, abort and escalate."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="routine_transfer",
                reasoning_keywords=["instrument", "approved"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_instrument_approval",
                description="Verify the instrument is on the approved list before proceeding.",
            ),
        ),

        Rule(
            rule_id="fin-rt-005",
            domain=Domain.FINANCE,
            name="routine_transfer_confirm_recipient",
            description=(
                "Recipient account details must be verified against the counterparty "
                "directory before dispatch."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="routine_transfer",
                reasoning_keywords=["recipient", "verify", "counterparty"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="verify_counterparty",
                description="Look up and confirm the recipient in the counterparty directory.",
            ),
        ),

        # ==============================
        # PORTFOLIO REBALANCE (5 rules)
        # ==============================

        Rule(
            rule_id="fin-pr-001",
            domain=Domain.FINANCE,
            name="rebalance_fetch_current_allocation",
            description=(
                "Before any rebalancing trades, retrieve the current portfolio "
                "allocation to establish the baseline."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="portfolio_rebalance",
                reasoning_keywords=["rebalance", "allocation", "current"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="get_portfolio_allocation",
                description="Fetch the current portfolio allocation before rebalancing.",
            ),
        ),

        Rule(
            rule_id="fin-pr-002",
            domain=Domain.FINANCE,
            name="rebalance_target_within_policy_bands",
            description=(
                "Rebalancing targets must remain within the policy band (±5% of "
                "the strategic allocation). Deviations require compliance sign-off."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="portfolio_rebalance",
                reasoning_keywords=["target", "allocation", "policy"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="validate_rebalance_target",
                description="Validate that the proposed rebalance is within policy bands.",
                required_parameters={"tolerance_pct": 5.0},
            ),
        ),

        Rule(
            rule_id="fin-pr-003",
            domain=Domain.FINANCE,
            name="rebalance_no_single_trade_over_25pct_nav",
            description=(
                "No single rebalancing trade may exceed 25% of the portfolio's "
                "net asset value (NAV). Larger rebalances must be chunked."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="portfolio_rebalance",
                reasoning_keywords=["trade", "rebalance", "size"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_trade_size_vs_nav",
                description="Verify individual trade does not exceed 25% of NAV.",
                required_parameters={"max_pct_nav": 25.0},
            ),
        ),

        Rule(
            rule_id="fin-pr-004",
            domain=Domain.FINANCE,
            name="rebalance_approved_asset_classes_only",
            description=(
                "Rebalancing trades may only involve asset classes in the approved "
                "investment policy statement (IPS). Novel assets require human approval."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="portfolio_rebalance",
                reasoning_keywords=["asset", "class", "approved", "IPS"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_asset_class_approval",
                description="Verify asset class is in the approved IPS before trading.",
            ),
        ),

        Rule(
            rule_id="fin-pr-005",
            domain=Domain.FINANCE,
            name="rebalance_record_rationale",
            description=(
                "After completing a rebalancing sequence, the agent must record "
                "the rationale and resulting allocation in the portfolio log."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="portfolio_rebalance",
                reasoning_keywords=["rebalance", "complete", "rationale"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="log_rebalance_rationale",
                description="Log the rebalancing rationale and outcome.",
            ),
        ),

        # ==============================
        # HIGH VALUE TRADE (5 rules)
        # ==============================

        Rule(
            rule_id="fin-hv-001",
            domain=Domain.FINANCE,
            name="high_value_trade_mandatory_risk_assessment",
            description=(
                "All trades above $100,000 require a risk assessment before "
                "any order is submitted."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="high_value_trade",
                numeric_conditions={"amount_usd": {"gte": 100000}},
                reasoning_keywords=["trade", "risk"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="run_risk_assessment",
                description="Run a risk assessment before submitting a high-value trade.",
            ),
        ),

        Rule(
            rule_id="fin-hv-002",
            domain=Domain.FINANCE,
            name="high_value_trade_human_approval_required",
            description=(
                "Trades above $500,000 require explicit human approval via the "
                "compliance portal before the order can be dispatched."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="high_value_trade",
                numeric_conditions={"amount_usd": {"gte": 500000}},
                reasoning_keywords=["trade", "approval"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="request_human_approval",
                description="Submit a human approval request for trades over $500k.",
                required_parameters={"approval_type": "high_value_trade"},
            ),
        ),

        Rule(
            rule_id="fin-hv-003",
            domain=Domain.FINANCE,
            name="high_value_trade_check_market_hours",
            description=(
                "High-value trades must only be submitted during market hours. "
                "After-hours submissions must be queued, not immediately dispatched."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="high_value_trade",
                reasoning_keywords=["market", "hours", "trade"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_market_hours",
                description="Verify market is open before submitting a high-value order.",
            ),
        ),

        Rule(
            rule_id="fin-hv-004",
            domain=Domain.FINANCE,
            name="high_value_trade_no_duplicate_within_5_turns",
            description=(
                "The same high-value trade (same instrument, same direction) must not "
                "be submitted more than once within a 5-turn window without re-approval."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="high_value_trade",
                reasoning_keywords=["duplicate", "trade", "instrument"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_duplicate_trade",
                description="Check trade history to avoid duplicates within 5 turns.",
                required_parameters={"lookback_turns": 5},
            ),
        ),

        Rule(
            rule_id="fin-hv-005",
            domain=Domain.FINANCE,
            name="high_value_trade_post_execution_notification",
            description=(
                "After a high-value trade is executed, an automated notification "
                "must be sent to the risk and compliance team."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="high_value_trade",
                reasoning_keywords=["executed", "notify", "compliance"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="send_compliance_notification",
                description="Notify risk/compliance after high-value trade execution.",
                required_parameters={"recipient_group": "risk_and_compliance"},
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Loader helper
# ---------------------------------------------------------------------------


def load_seed_rules_into_bank(
    rule_bank: RuleBankBase,
    auto_verify: bool = False,
    verifier: str = "automatic:seed_loader",
) -> list[Rule]:
    """
    Load all Finance seed rules into the given Rule Bank.

    Args:
        rule_bank:    The RuleBank instance to load rules into.
        auto_verify:  If True, immediately verify all loaded rules.
        verifier:     Verifier string used when auto_verify=True.

    Returns:
        List of loaded (and possibly verified) Rule objects.
    """
    rules = get_seed_rules()
    loaded = []
    for rule in rules:
        stored = rule_bank.add_rule(rule, provenance="finance_seed_v1")
        if auto_verify:
            stored = rule_bank.verify_rule(stored.rule_id, verifier=verifier)
        loaded.append(stored)
    return loaded
