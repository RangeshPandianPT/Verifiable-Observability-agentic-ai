"""
Healthcare Domain — Seed Rule Set (Phase 6)

12 rules covering three Healthcare task types:
  - medication_management
  - patient_data_access
  - clinical_decision_support

Each rule is an observation→action mapping derived from plausible
"if observation X then approved action Y" pairs a healthcare agent would follow.

All rules are loaded with verification_status=PENDING by default;
call RuleBank.verify_rule(rule_id, verifier) to promote them to VERIFIED.

Usage::

    from verifiable_observability.simulation.domains.healthcare.seed_rules import (
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
    """Return the 12 Healthcare seed rules (unsaved, status=PENDING)."""
    return [

        # ===============================
        # MEDICATION MANAGEMENT (4 rules)
        # ===============================

        Rule(
            rule_id="hc-mm-001",
            domain=Domain.HEALTHCARE,
            name="medication_check_allergies_first",
            description=(
                "Before prescribing or administering any medication, the agent must "
                "retrieve the patient's allergy profile to check for contraindications."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="medication_management",
                reasoning_keywords=["medication", "prescribe", "administer", "drug"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="get_patient_allergies",
                description="Retrieve allergy profile before any medication action.",
            ),
        ),

        Rule(
            rule_id="hc-mm-002",
            domain=Domain.HEALTHCARE,
            name="medication_verify_dosage_range",
            description=(
                "Prescribed dosage must be verified against the therapeutic range "
                "for the patient's age, weight, and renal function."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="medication_management",
                reasoning_keywords=["dosage", "dose", "prescribe", "therapeutic"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="verify_dosage_range",
                description="Verify dose is within safe therapeutic range for this patient.",
                required_parameters={"check_renal_function": True},
            ),
        ),

        Rule(
            rule_id="hc-mm-003",
            domain=Domain.HEALTHCARE,
            name="medication_require_physician_cosign_controlled",
            description=(
                "Controlled substances (Schedule II-V) require a physician co-signature "
                "before the prescription is finalized, regardless of agent role."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="medication_management",
                reasoning_keywords=["controlled", "schedule", "opioid", "narcotic"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="request_physician_cosign",
                description="Submit for physician co-signature on controlled substance orders.",
                required_parameters={"urgency": "standard"},
            ),
        ),

        Rule(
            rule_id="hc-mm-004",
            domain=Domain.HEALTHCARE,
            name="medication_log_administration",
            description=(
                "Every medication administration event must be logged to the patient's "
                "medication administration record (MAR) immediately after dispensing."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="medication_management",
                reasoning_keywords=["administer", "dispense", "log", "record"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="log_medication_administration",
                description="Record the administration event in the MAR.",
            ),
        ),

        # ==============================
        # PATIENT DATA ACCESS (4 rules)
        # ==============================

        Rule(
            rule_id="hc-pda-001",
            domain=Domain.HEALTHCARE,
            name="patient_data_verify_access_rights",
            description=(
                "Before reading or writing patient records, the agent must verify "
                "that the requesting clinician has the appropriate role-based access."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="patient_data_access",
                reasoning_keywords=["patient", "record", "access", "retrieve"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="verify_clinician_access",
                description="Check RBAC permissions before accessing patient records.",
            ),
        ),

        Rule(
            rule_id="hc-pda-002",
            domain=Domain.HEALTHCARE,
            name="patient_data_hipaa_audit_log",
            description=(
                "Every access to protected health information (PHI) must generate "
                "an audit log entry compliant with HIPAA access logging requirements."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="patient_data_access",
                reasoning_keywords=["PHI", "health information", "records", "data"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="write_hipaa_audit_log",
                description="Write a HIPAA-compliant audit entry for this PHI access.",
                required_parameters={"log_type": "phi_access"},
            ),
        ),

        Rule(
            rule_id="hc-pda-003",
            domain=Domain.HEALTHCARE,
            name="patient_data_minimum_necessary",
            description=(
                "When retrieving patient data, the agent must request only the "
                "minimum necessary fields for the stated clinical purpose (HIPAA minimum-necessary rule)."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="patient_data_access",
                reasoning_keywords=["patient", "fields", "query", "filter"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="get_patient_record_filtered",
                description="Fetch only the required fields, not the full record.",
                required_parameters={"min_necessary": True},
            ),
        ),

        Rule(
            rule_id="hc-pda-004",
            domain=Domain.HEALTHCARE,
            name="patient_data_de_identify_for_research",
            description=(
                "When sharing patient data for research or analytics purposes, "
                "the dataset must be de-identified to HIPAA Safe Harbor standard before export."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="patient_data_access",
                reasoning_keywords=["research", "analytics", "export", "share", "de-identify"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="de_identify_patient_data",
                description="Apply HIPAA Safe Harbor de-identification before export.",
                required_parameters={"standard": "safe_harbor"},
            ),
        ),

        # ===================================
        # CLINICAL DECISION SUPPORT (4 rules)
        # ===================================

        Rule(
            rule_id="hc-cds-001",
            domain=Domain.HEALTHCARE,
            name="clinical_decision_verify_evidence_grade",
            description=(
                "Any clinical recommendation produced by the agent must cite its evidence "
                "source and grade (A/B/C/D) before it is presented to the clinician."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="clinical_decision_support",
                reasoning_keywords=["recommend", "guideline", "evidence", "treatment"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="get_clinical_guideline",
                description="Retrieve guideline with evidence grade before recommending.",
            ),
        ),

        Rule(
            rule_id="hc-cds-002",
            domain=Domain.HEALTHCARE,
            name="clinical_decision_flag_contraindications",
            description=(
                "Before any treatment recommendation, the agent must check for and "
                "flag contraindications given the patient's current condition and medications."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="clinical_decision_support",
                reasoning_keywords=["treatment", "contraindication", "condition", "current"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_contraindications",
                description="Identify contraindications before surfacing a recommendation.",
            ),
        ),

        Rule(
            rule_id="hc-cds-003",
            domain=Domain.HEALTHCARE,
            name="clinical_decision_human_override_mandatory",
            description=(
                "All AI-generated clinical decisions must be presented as recommendations "
                "only; the final decision authority rests with the attending physician, "
                "and the override pathway must always be available."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="clinical_decision_support",
                reasoning_keywords=["AI", "decision", "recommend", "final", "physician"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="present_recommendation_with_override",
                description="Surface recommendation with explicit physician override option.",
                required_parameters={"override_required": True},
            ),
        ),

        Rule(
            rule_id="hc-cds-004",
            domain=Domain.HEALTHCARE,
            name="clinical_decision_log_outcome",
            description=(
                "After a clinical decision is acted upon, the outcome (accepted/modified/rejected "
                "by the physician) must be recorded to support future model improvement."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.HEALTHCARE,
                task_type="clinical_decision_support",
                reasoning_keywords=["outcome", "accepted", "rejected", "physician", "log"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="log_clinical_decision_outcome",
                description="Record physician response to AI recommendation.",
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
    Load all Healthcare seed rules into the given Rule Bank.

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
        stored = rule_bank.add_rule(rule, provenance="healthcare_seed_v1")
        if auto_verify:
            stored = rule_bank.verify_rule(stored.rule_id, verifier=verifier)
        loaded.append(stored)
    return loaded
