"""
Ecommerce Domain — Seed Rule Set

Rules for order_processing, refunds, inventory_management.
"""

from __future__ import annotations

from verifiable_observability.core.rule_bank import RuleBankBase
from verifiable_observability.storage.models import (
    ActionPattern,
    Domain,
    ObservationPattern,
    Rule,
)

def get_seed_rules() -> list[Rule]:
    return [
        Rule(
            rule_id="ec-op-001",
            domain=Domain.ECOMMERCE,
            name="order_verify_stock_first",
            description="Verify item is in stock before processing order.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="order_processing",
                reasoning_keywords=["order", "process", "verify", "stock"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="verify_stock_availability",
                description="Check inventory before proceeding.",
            ),
        ),
        Rule(
            rule_id="ec-op-002",
            domain=Domain.ECOMMERCE,
            name="order_fraud_check",
            description="Run fraud detection for high-value orders.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="order_processing",
                reasoning_keywords=["fraud", "high value", "check"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="run_fraud_check",
                description="Run fraud analysis.",
                required_parameters={"strict_mode": True},
            ),
        ),
        Rule(
            rule_id="ec-op-003",
            domain=Domain.ECOMMERCE,
            name="order_calculate_shipping",
            description="Calculate shipping costs based on destination.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="order_processing",
                reasoning_keywords=["shipping", "cost", "calculate", "destination"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="calculate_shipping",
                description="Calculate shipping cost.",
            ),
        ),
        Rule(
            rule_id="ec-op-004",
            domain=Domain.ECOMMERCE,
            name="order_log_fulfillment",
            description="Log order as fulfilled.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="order_processing",
                reasoning_keywords=["fulfill", "log", "complete"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="log_order_fulfillment",
                description="Log order fulfillment.",
            ),
        ),
        Rule(
            rule_id="ec-ref-001",
            domain=Domain.ECOMMERCE,
            name="refund_check_policy",
            description="Check if order is within the refund policy window.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="refunds",
                reasoning_keywords=["refund", "policy", "window", "days"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_refund_policy",
                description="Verify policy window.",
            ),
        ),
        Rule(
            rule_id="ec-ref-002",
            domain=Domain.ECOMMERCE,
            name="refund_require_manager",
            description="Refunds over 500 require manager approval.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="refunds",
                reasoning_keywords=["large refund", "manager", "approve", "over 500"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="request_manager_approval",
                description="Request manager approval for large refund.",
            ),
        ),
        Rule(
            rule_id="ec-ref-003",
            domain=Domain.ECOMMERCE,
            name="refund_process_payment",
            description="Process the refund to the original payment method.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="refunds",
                reasoning_keywords=["process", "payment", "original method"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="issue_refund",
                description="Issue refund to customer.",
            ),
        ),
        Rule(
            rule_id="ec-ref-004",
            domain=Domain.ECOMMERCE,
            name="refund_log_reason",
            description="Log the customer's reason for the refund.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="refunds",
                reasoning_keywords=["reason", "log", "why", "return"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="log_refund_reason",
                description="Log refund reason.",
            ),
        ),
        Rule(
            rule_id="ec-inv-001",
            domain=Domain.ECOMMERCE,
            name="inventory_check_supplier",
            description="Check supplier availability before restocking.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="inventory_management",
                reasoning_keywords=["supplier", "restock", "available"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_supplier_availability",
                description="Check supplier catalog.",
            ),
        ),
        Rule(
            rule_id="ec-inv-002",
            domain=Domain.ECOMMERCE,
            name="inventory_alert_low_stock",
            description="Send an alert when stock drops below threshold.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="inventory_management",
                reasoning_keywords=["alert", "low stock", "threshold"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="send_low_stock_alert",
                description="Send low stock alert.",
            ),
        ),
        Rule(
            rule_id="ec-inv-003",
            domain=Domain.ECOMMERCE,
            name="inventory_reorder_approval",
            description="Large restock orders require approval.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="inventory_management",
                reasoning_keywords=["reorder", "large", "approve"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="request_restock_approval",
                description="Request restock approval.",
            ),
        ),
        Rule(
            rule_id="ec-inv-004",
            domain=Domain.ECOMMERCE,
            name="inventory_update_ledger",
            description="Update the inventory ledger after restocking.",
            observation_pattern=ObservationPattern(
                domain=Domain.ECOMMERCE,
                task_type="inventory_management",
                reasoning_keywords=["update", "ledger", "inventory", "record"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="update_inventory_ledger",
                description="Update ledger.",
            ),
        ),
    ]

def load_seed_rules_into_bank(
    rule_bank: RuleBankBase,
    auto_verify: bool = False,
    verifier: str = "automatic:seed_loader",
) -> list[Rule]:
    rules = get_seed_rules()
    loaded = []
    for rule in rules:
        stored = rule_bank.add_rule(rule, provenance="ecommerce_seed_v1")
        if auto_verify:
            stored = rule_bank.verify_rule(stored.rule_id, verifier=verifier)
        loaded.append(stored)
    return loaded
