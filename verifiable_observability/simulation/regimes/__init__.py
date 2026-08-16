"""
simulation.regimes — Phase 5 Behavioral Regimes.

Four scripted behavioral scenarios for controlled evaluation of the
Verifiable Observability verification stack.

Usage
-----
    from verifiable_observability.simulation.regimes import (
        RegimeType,
        build_regime,
    )

    regime = build_regime(RegimeType.COMPLIANT)
    adapter = regime.build_adapter(task_description="Transfer $500 ...")
"""

from __future__ import annotations

from verifiable_observability.simulation.regimes.adversarial_injection import (
    AdversarialInjectionRegime,
)
from verifiable_observability.simulation.regimes.base import (
    REGIME_EXPECTATIONS,
    RegimeBase,
    RegimeType,
)
from verifiable_observability.simulation.regimes.compliant import CompliantRegime
from verifiable_observability.simulation.regimes.mild_drift import MildDriftRegime
from verifiable_observability.simulation.regimes.tool_failure_drift import (
    ToolFailureDriftRegime,
)

__all__ = [
    "RegimeType",
    "RegimeBase",
    "REGIME_EXPECTATIONS",
    "CompliantRegime",
    "MildDriftRegime",
    "AdversarialInjectionRegime",
    "ToolFailureDriftRegime",
    "build_regime",
    "ALL_REGIMES",
]

# Registry: regime type → class
_REGISTRY: dict[RegimeType, type[RegimeBase]] = {
    RegimeType.COMPLIANT: CompliantRegime,
    RegimeType.MILD_DRIFT: MildDriftRegime,
    RegimeType.ADVERSARIAL_INJECTION: AdversarialInjectionRegime,
    RegimeType.TOOL_FAILURE_DRIFT: ToolFailureDriftRegime,
}

# Ordered list for CLI display and sweep iteration
ALL_REGIMES: list[RegimeType] = list(RegimeType)


def build_regime(regime_type: RegimeType | str) -> RegimeBase:
    """
    Instantiate a regime by type.

    Args:
        regime_type: A RegimeType enum member or its string value
                     (e.g. ``"compliant"``, ``"mild_drift"``).
    Returns:
        An instantiated RegimeBase subclass.
    Raises:
        ValueError: If the regime_type string is not recognised.
    """
    if isinstance(regime_type, str):
        try:
            regime_type = RegimeType(regime_type)
        except ValueError:
            valid = [r.value for r in RegimeType]
            raise ValueError(
                f"Unknown regime {regime_type!r}. Valid values: {valid}"
            ) from None
    cls = _REGISTRY[regime_type]
    return cls()
