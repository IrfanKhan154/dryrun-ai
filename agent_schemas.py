from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


MAX_PLAN_SUMMARY_LENGTH = 2_000
MAX_QUESTION_LENGTH = 500
MAX_RATIONALE_LENGTH = 1_500
MAX_STOP_CONDITION_LENGTH = 500
MAX_TEST_CASES = 5
MAX_STOP_CONDITIONS = 20
MAX_EXPECTED_EVIDENCE = 7


def _non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty or whitespace-only")
    return value


class FocusArea(str, Enum):
    FILESYSTEM = "filesystem"
    SUBPROCESS = "subprocess"
    NETWORK = "network"
    ENVIRONMENT = "environment"
    DYNAMIC_EXECUTION = "dynamic_execution"
    DESTRUCTIVE_BEHAVIOR = "destructive_behavior"
    GENERAL = "general"


class EvidenceSource(str, Enum):
    PROGRAM_STDOUT = "program_stdout"
    DOCKER_LIFECYCLE = "docker_lifecycle"
    EXIT_STATUS = "exit_status"
    TIMEOUT = "timeout"
    OOM_STATUS = "oom_status"
    OPERATION_TELEMETRY_UNAVAILABLE = "operation_telemetry_unavailable"


def _parse_enum(enum_type, value: object):
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        return enum_type(value)
    raise ValueError(f"must be a valid {enum_type.__name__} value")


class AgentTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    focus_area: FocusArea
    question: StrictStr = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    rationale: StrictStr = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    expected_evidence: list[EvidenceSource] = Field(
        min_length=1,
        max_length=MAX_EXPECTED_EVIDENCE,
    )

    _validate_focus_area = field_validator(
        "focus_area", mode="before"
    )(lambda value: _parse_enum(FocusArea, value))
    _validate_question = field_validator("question")(_non_empty)
    _validate_rationale = field_validator("rationale")(_non_empty)

    @field_validator("expected_evidence", mode="before")
    @classmethod
    def validate_expected_evidence(cls, value):
        if not isinstance(value, list):
            raise ValueError("expected_evidence must be a list")
        return [_parse_enum(EvidenceSource, item) for item in value]


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: StrictStr = Field(min_length=1, max_length=MAX_PLAN_SUMMARY_LENGTH)
    test_cases: list[AgentTestCase] = Field(
        min_length=1,
        max_length=MAX_TEST_CASES,
    )
    stop_conditions: list[StrictStr] = Field(
        min_length=1,
        max_length=MAX_STOP_CONDITIONS,
    )

    _validate_summary = field_validator("summary")(_non_empty)

    @field_validator("stop_conditions")
    @classmethod
    def validate_stop_conditions(cls, conditions: list[str]) -> list[str]:
        for condition in conditions:
            if not condition.strip():
                raise ValueError(
                    "stop_conditions must not contain empty strings"
                )
            if len(condition) > MAX_STOP_CONDITION_LENGTH:
                raise ValueError("stop condition exceeds maximum length")
        return conditions
