from pydantic import ValidationError

from agent_schemas import AgentPlan, EvidenceSource, FocusArea


POLICY_VERSION = "1.0"
SANDBOX_PROFILE = "default_restricted_docker"
ALLOWED_FOCUS_AREAS = frozenset(FocusArea)
ALLOWED_EVIDENCE = frozenset(EvidenceSource)


def evaluate_plan(plan: AgentPlan) -> dict:
    if not isinstance(plan, AgentPlan):
        raise TypeError("Policy engine accepts only a validated AgentPlan.")

    try:
        plan = AgentPlan.model_validate(plan.model_dump())
    except ValidationError:
        return {
            "allowed": False,
            "policy_version": POLICY_VERSION,
            "sandbox_profile": SANDBOX_PROFILE,
            "reasons": ["Plan failed policy-bound schema validation."],
            "telemetry_limitations": [
                EvidenceSource.OPERATION_TELEMETRY_UNAVAILABLE.value,
                "Filesystem, process, and network operations are not "
                "independently verified by this policy decision.",
            ],
        }

    reasons = []
    for test_case in plan.test_cases:
        if test_case.focus_area not in ALLOWED_FOCUS_AREAS:
            reasons.append(
                f"Unsupported focus area: {test_case.focus_area.value}"
            )
        unsupported = set(test_case.expected_evidence) - ALLOWED_EVIDENCE
        if unsupported:
            reasons.append(
                "Unsupported evidence sources: "
                + ", ".join(sorted(item.value for item in unsupported))
            )

    if not reasons:
        reasons.append(
            "Plan is limited to server-controlled restricted Docker "
            "execution and declared evidence sources."
        )

    return {
        "allowed": not any(
            reason.startswith("Unsupported") for reason in reasons
        ),
        "policy_version": POLICY_VERSION,
        "sandbox_profile": SANDBOX_PROFILE,
        "reasons": reasons,
        "telemetry_limitations": [
            EvidenceSource.OPERATION_TELEMETRY_UNAVAILABLE.value,
            "Filesystem, process, and network operations are not "
            "independently verified by this policy decision.",
        ],
    }


def validate_plan_policy(plan: AgentPlan) -> dict:
    return evaluate_plan(plan)
