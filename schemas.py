from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictStr, field_validator


MAX_SUMMARY_LENGTH = 4_000
MAX_OPERATION_LENGTH = 200
MAX_TARGET_LENGTH = 2_000
MAX_REASON_LENGTH = 4_000
MAX_EVIDENCE_LENGTH = 8_000
MAX_LIMITATION_LENGTH = 1_000
MAX_PREDICTIONS = 100
MAX_LIMITATIONS = 50


def _require_non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty or whitespace-only")
    return value


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def _parse_risk_level(value: object) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value
    if isinstance(value, str):
        return RiskLevel(value)
    raise ValueError("risk_level must be a valid risk level string")


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: StrictStr = Field(
        min_length=1,
        max_length=MAX_OPERATION_LENGTH,
    )
    target: StrictStr = Field(
        min_length=1,
        max_length=MAX_TARGET_LENGTH,
    )
    risk_level: RiskLevel
    reason: StrictStr = Field(
        min_length=1,
        max_length=MAX_REASON_LENGTH,
    )
    evidence: StrictStr = Field(max_length=MAX_EVIDENCE_LENGTH)
    confidence: StrictFloat = Field(ge=0.0, le=1.0)

    _validate_operation = field_validator("operation")(_require_non_empty)
    _validate_target = field_validator("target")(_require_non_empty)
    _validate_reason = field_validator("reason")(_require_non_empty)
    _validate_risk_level = field_validator(
        "risk_level", mode="before"
    )(_parse_risk_level)


class AIAnalysisReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: StrictStr = Field(
        min_length=1,
        max_length=MAX_SUMMARY_LENGTH,
    )
    risk_level: RiskLevel
    predictions: list[Prediction] = Field(max_length=MAX_PREDICTIONS)
    limitations: list[StrictStr] = Field(max_length=MAX_LIMITATIONS)

    _validate_summary = field_validator("summary")(_require_non_empty)
    _validate_risk_level = field_validator(
        "risk_level", mode="before"
    )(_parse_risk_level)

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, limitations: list[str]) -> list[str]:
        for limitation in limitations:
            if not isinstance(limitation, str):
                raise ValueError("limitations must contain only strings")
            if not limitation.strip():
                raise ValueError("limitations must not contain empty strings")
            if len(limitation) > MAX_LIMITATION_LENGTH:
                raise ValueError("limitation exceeds maximum length")
        return limitations
