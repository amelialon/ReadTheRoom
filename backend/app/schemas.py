"""Request and response bodies shared by every endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

State = Literal["pending", "escalated", "acked"]

REQUEST_EXAMPLE = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "local_id": 1,
    "patient_id": "11111111-1111-4111-8111-111111111111",
    "device_id": "22222222-2222-4222-8222-222222222222",
    "message": "Bed 4: patient is thirsty and wants water",
    "urgent": False,
    "state": "pending",
    "created_at": "2026-11-21T15:30:00Z",
    "acked_at": None,
    "category": None,
    "detected_object": None,
    "context": {},
}


class RequestIn(BaseModel):
    """A confirmed bedside request. The device supplies the id and reuses it on retry."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [REQUEST_EXAMPLE]})

    id: UUID
    local_id: int = Field(gt=0, le=2147483647, examples=[1])
    patient_id: UUID
    device_id: UUID
    message: str = Field(min_length=1, max_length=500)
    urgent: bool
    state: State
    created_at: datetime
    acked_at: Optional[datetime] = None
    category: Optional[str] = None
    detected_object: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def matches_bedside_rules(self) -> "RequestIn":
        if not self.message.strip():
            raise ValueError("message is required")
        _aware("created_at", self.created_at)
        if self.acked_at is not None:
            _aware("acked_at", self.acked_at)
        if self.state == "acked" and self.acked_at is None:
            raise ValueError("acked_at is required when state is acked")
        if self.state != "acked" and self.acked_at is not None:
            raise ValueError("acked_at must be null unless state is acked")
        if self.urgent and self.state == "pending":
            raise ValueError("urgent requests are created as escalated")
        return self


class RequestUpdate(BaseModel):
    """Nurse acknowledgement. The only accepted value is acked."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"state": "acked"}]})

    state: Literal["acked"]


def _as_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class RequestOut(BaseModel):
    id: UUID
    local_id: int
    patient_id: UUID
    device_id: UUID
    display_name: str
    bed: str
    message: str
    urgent: bool
    state: State
    created_at: datetime
    received_at: datetime
    acked_at: Optional[datetime]
    category: Optional[str]
    detected_object: Optional[str]
    context: Dict[str, Any]
    waiting_s: int
    overdue: bool

    @field_serializer("created_at", "received_at", "acked_at", when_used="json")
    def _request_times(self, value: Optional[datetime]) -> Optional[str]:
        return _as_utc(value)


class RequestList(BaseModel):
    requests: List[RequestOut]


class PatientOut(BaseModel):
    id: UUID
    display_name: str
    bed: str
    active: bool
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _patient_time(self, value: datetime) -> str:
        return _as_utc(value)


class PatientList(BaseModel):
    patients: List[PatientOut]


class DeviceOut(BaseModel):
    id: UUID
    patient_id: UUID
    name: str
    display_name: str
    bed: str
    last_heartbeat_at: Optional[datetime]
    online: bool
    created_at: datetime

    @field_serializer("last_heartbeat_at", "created_at", when_used="json")
    def _device_times(self, value: Optional[datetime]) -> Optional[str]:
        return _as_utc(value)


class DeviceList(BaseModel):
    devices: List[DeviceOut]


class SensorIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{
                "device_id": "22222222-2222-4222-8222-222222222222",
                "temperature_c": 27.5,
                "humidity_pct": 40,
                "observed_at": "2026-11-21T15:30:00Z",
            }]
        },
    )

    device_id: UUID
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    observed_at: datetime

    @model_validator(mode="after")
    def has_a_measurement(self) -> "SensorIn":
        _aware("observed_at", self.observed_at)
        if self.temperature_c is None and self.humidity_pct is None:
            raise ValueError("temperature_c or humidity_pct is required")
        return self


class SensorOut(BaseModel):
    id: int
    device_id: UUID
    temperature_c: Optional[float]
    humidity_pct: Optional[float]
    observed_at: datetime

    @field_serializer("observed_at", when_used="json")
    def _sensor_time(self, value: datetime) -> str:
        return _as_utc(value)


class SensorLatest(BaseModel):
    reading: Optional[SensorOut]


class SettingsOut(BaseModel):
    heartbeat_interval_s: int
    offline_after_s: int
    escalate_after_s: int


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must include a timezone")
