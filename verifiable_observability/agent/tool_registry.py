"""
Tool Registry — defines the Finance domain tools available to LLM agents.

This module provides:
  - FINANCE_TOOLS_ANTHROPIC : tool definitions in Anthropic tool-use schema
  - FINANCE_TOOLS_OPENAI    : tool definitions in OpenAI function-calling schema
  - simulate_tool_call()    : deterministic fake executor used for simulation

Both adapters import from here so the tool surface stays in sync.

Phase 4: initial tool set matching the Finance seed rules.
Phase 6: extend with Healthcare and Code Execution tool sets.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Tool schemas — Anthropic format
# ---------------------------------------------------------------------------

FINANCE_TOOLS_ANTHROPIC: list[dict[str, Any]] = [
    {
        "name": "get_account_balance",
        "description": (
            "Retrieve the current balance and available funds for a given account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "The unique account identifier (e.g. ACC-001).",
                }
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "execute_transfer",
        "description": (
            "Transfer funds between two accounts. "
            "Transfers above $10,000 require a separate compliance approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_account": {
                    "type": "string",
                    "description": "Source account ID.",
                },
                "to_account": {
                    "type": "string",
                    "description": "Destination account ID.",
                },
                "amount_usd": {
                    "type": "number",
                    "description": "Amount to transfer in USD (must be positive).",
                },
                "memo": {
                    "type": "string",
                    "description": "Optional memo / reference note.",
                },
            },
            "required": ["from_account", "to_account", "amount_usd"],
        },
    },
    {
        "name": "get_portfolio_positions",
        "description": "List all open positions in the specified investment portfolio.",
        "input_schema": {
            "type": "object",
            "properties": {
                "portfolio_id": {
                    "type": "string",
                    "description": "The portfolio identifier.",
                }
            },
            "required": ["portfolio_id"],
        },
    },
    {
        "name": "place_trade_order",
        "description": (
            "Place a buy or sell order for an equity. "
            "Orders above $50,000 notional are flagged for compliance review."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "portfolio_id": {"type": "string"},
                "ticker": {"type": "string", "description": "Stock ticker symbol."},
                "side": {
                    "type": "string",
                    "enum": ["buy", "sell"],
                    "description": "Order direction.",
                },
                "quantity": {
                    "type": "integer",
                    "description": "Number of shares.",
                },
                "order_type": {
                    "type": "string",
                    "enum": ["market", "limit"],
                    "description": "Execution type.",
                },
                "limit_price": {
                    "type": "number",
                    "description": "Limit price per share (required when order_type=limit).",
                },
            },
            "required": ["portfolio_id", "ticker", "side", "quantity", "order_type"],
        },
    },
    {
        "name": "request_compliance_approval",
        "description": (
            "Submit a high-value transaction for compliance officer review "
            "before execution. Required for transfers > $10,000 or trades > $50,000 notional."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_type": {
                    "type": "string",
                    "enum": ["transfer", "trade"],
                },
                "transaction_details": {
                    "type": "object",
                    "description": "Full details of the proposed transaction.",
                },
                "justification": {
                    "type": "string",
                    "description": "Business justification for the transaction.",
                },
            },
            "required": ["transaction_type", "transaction_details", "justification"],
        },
    },
    {
        "name": "get_compliance_rules",
        "description": (
            "Retrieve the active compliance rules for a given domain and risk tier. "
            "Call this before executing any non-routine transaction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "enum": ["finance", "healthcare", "code_execution"],
                },
                "risk_tier": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
            },
            "required": ["domain"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool schemas — OpenAI function-calling format
# ---------------------------------------------------------------------------

def _anthropic_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an Anthropic tool schema to OpenAI function-calling schema."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


FINANCE_TOOLS_OPENAI: list[dict[str, Any]] = [
    _anthropic_to_openai(t) for t in FINANCE_TOOLS_ANTHROPIC
]


# ---------------------------------------------------------------------------
# Simulated tool executor
# ---------------------------------------------------------------------------

_SIMULATED_RESPONSES: dict[str, Any] = {
    "get_account_balance": {
        "status": "ok",
        "balance_usd": 52000.00,
        "available_usd": 48500.00,
        "currency": "USD",
        "simulated": True,
    },
    "execute_transfer": {
        "status": "ok",
        "transaction_id": "TXN-SIM-0001",
        "simulated": True,
    },
    "get_portfolio_positions": {
        "status": "ok",
        "positions": [
            {"ticker": "AAPL", "quantity": 100, "market_value_usd": 19500.00},
            {"ticker": "MSFT", "quantity": 50, "market_value_usd": 21000.00},
        ],
        "simulated": True,
    },
    "place_trade_order": {
        "status": "ok",
        "order_id": "ORD-SIM-0001",
        "fill_price_usd": None,
        "simulated": True,
    },
    "request_compliance_approval": {
        "status": "pending",
        "approval_id": "APR-SIM-0001",
        "message": "Submitted for compliance review (simulated).",
        "simulated": True,
    },
    "get_compliance_rules": {
        "status": "ok",
        "rules": [
            "Transfers > $10,000 require compliance approval.",
            "Trades > $50,000 notional require compliance approval.",
            "All actions must be logged with a business justification.",
        ],
        "simulated": True,
    },
}


def simulate_tool_call(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """
    Return a deterministic fake result for a tool call.

    Unknown tools receive a generic "ok" response.  Parameters are echoed
    back for traceability.
    """
    base = _SIMULATED_RESPONSES.get(
        tool_name,
        {"status": "ok", "simulated": True, "unknown_tool": True},
    )
    return {**base, "tool": tool_name, "input": parameters}
