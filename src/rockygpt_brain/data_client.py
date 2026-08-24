"""Typed, HTTP-only DATA v2 shuttle client."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from rockygpt_brain.errors import DataUnavailableError, ServiceError
from rockygpt_brain.planning import ServiceDay, ShuttleSelection, ShuttleTimeScope


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DataDataset(DataModel):
    id: str
    version: str
    activated_at: datetime = Field(alias="activatedAt")


class DataEvidence(DataModel):
    evidence_id: str = Field(alias="evidenceId")
    source_id: str = Field(alias="sourceId")
    title: str
    url: HttpUrl
    collected_at: datetime | None = Field(default=None, alias="collectedAt")


class Completeness(DataModel):
    state: Literal["complete", "partial", "unknown"]
    returned: int = Field(ge=0)
    matched: int | None = Field(default=None, ge=0)
    limit: int = Field(ge=1)
    truncated: bool
    reason: str | None = None


class DataOrdering(DataModel):
    field: str
    direction: Literal["asc", "desc"]


class ShuttleQuery(DataModel):
    route: Annotated[str | None, StringConstraints(min_length=1, max_length=120)] = None
    origin: Annotated[str | None, StringConstraints(min_length=1, max_length=120)] = None
    destination: Annotated[str | None, StringConstraints(min_length=1, max_length=120)] = None
    service_date: date | None = Field(default=None, alias="serviceDate")
    service_day: ServiceDay | None = Field(default=None, alias="serviceDay")
    as_of: datetime = Field(alias="asOf")
    selection: ShuttleSelection
    time_scope: ShuttleTimeScope = Field(alias="timeScope")
    limit: int = Field(default=8, ge=1, le=25)

    @model_validator(mode="after")
    def as_of_is_aware(self) -> "ShuttleQuery":
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("asOf must include a timezone")
        return self


class ShuttleAppliedFilters(DataModel):
    route: str | None = None
    origin: str | None = None
    destination: str | None = None
    service_date: date = Field(alias="serviceDate")
    service_day: ServiceDay = Field(alias="serviceDay")
    as_of: datetime = Field(alias="asOf")
    selection: ShuttleSelection
    time_scope: ShuttleTimeScope = Field(alias="timeScope")


class ShuttleStop(DataModel):
    location: str
    time: str


class ShuttleRecord(DataModel):
    route: str
    service_date: date = Field(alias="serviceDate")
    service_day: ServiceDay = Field(alias="serviceDay")
    departure: ShuttleStop
    stops: list[ShuttleStop]
    arrival: ShuttleStop
    matched_origin: ShuttleStop = Field(alias="matchedOrigin")
    matched_destination: ShuttleStop = Field(alias="matchedDestination")
    evidence_ids: list[str] = Field(alias="evidenceIds")


class ShuttleResponse(DataModel):
    outcome: Literal[
        "success", "empty", "no_match", "needs_clarification", "unsupported", "unavailable", "error"
    ]
    records: list[ShuttleRecord]
    completeness: Completeness
    applied_filters: ShuttleAppliedFilters = Field(alias="appliedFilters")
    ordering: list[DataOrdering]
    dataset: DataDataset
    evidence: list[DataEvidence]
    warnings: list[str] = Field(default_factory=list)
    safe_error_code: str | None = Field(default=None, alias="safeErrorCode")


class DataPort(Protocol):
    async def query_shuttle(self, query: ShuttleQuery) -> ShuttleResponse: ...

    async def readiness(self) -> bool: ...


class HttpDataV2Client:
    def __init__(
        self,
        base_url: str,
        *,
        environment_token: str | None = None,
        timeout_seconds: float = 4.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = environment_token
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return (
            {"x-rockygpt-environment-token": self._token} if self._token is not None else {}
        )

    async def query_shuttle(self, query: ShuttleQuery) -> ShuttleResponse:
        try:
            response = await self._client.post(
                "/v2/capabilities/shuttle/query",
                json=query.model_dump(mode="json", by_alias=True, exclude_none=True),
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise DataUnavailableError("DATA shuttle request failed") from exc
        if response.status_code == 400:
            raise ServiceError(400, "INVALID_REQUEST", "The shuttle request is invalid.")
        if response.status_code in {401, 503}:
            raise DataUnavailableError("DATA shuttle service is unavailable")
        if response.status_code != 200:
            raise DataUnavailableError(f"unexpected DATA response {response.status_code}")
        try:
            return ShuttleResponse.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise DataUnavailableError("DATA returned an invalid shuttle contract") from exc

    async def readiness(self) -> bool:
        try:
            response = await self._client.get("/readiness", timeout=2.5)
            return response.status_code == 200 and response.json().get("status") == "ready"
        except (httpx.HTTPError, ValueError):
            return False

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()
