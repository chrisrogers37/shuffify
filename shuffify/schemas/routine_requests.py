"""Strict wire contracts for reviewed routine activation."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from shuffify.schemas.requests import JSONSizeLimit

# A JSON object of at most MAX_JSON_BYTES once serialized.
JSONObject = Annotated[dict[str, Any], JSONSizeLimit]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RoutineDefinition(_Strict):
    """A complete new definition, or supplied fields of an exact edit."""

    job_type: str | None = None
    algorithm_name: str | None = None
    algorithm_params: JSONObject | None = None
    timing: JSONObject | None = None


class RoutineReviewRequest(_Strict):
    request_key: str = Field(min_length=1, max_length=128)
    definition: RoutineDefinition
    schedule_id: int | None = Field(default=None, gt=0)
    expected_revision: str | None = None
    desired_enabled: bool | None = None
    create_another: bool = False


class RoutineActivateRequest(_Strict):
    request_key: str = Field(min_length=1, max_length=128)
    definition: JSONObject
    intent: JSONObject
    dependency_revision: str = Field(min_length=64, max_length=64)
    schedule_id: int | None = Field(default=None, gt=0)
    expected_revision: str | None = None
    desired_enabled: bool | None = None
    create_another: bool = False


class RoutineStateRequest(_Strict):
    desired_enabled: bool
    expected_revision: str = Field(min_length=64, max_length=64)
