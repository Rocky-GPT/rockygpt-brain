"""Trusted transportation database access and record conversion."""

import os
import socket
from dataclasses import dataclass
from datetime import date, datetime
from importlib.resources import files
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import certifi
from psycopg import Connection, OperationalError, connect
from psycopg.rows import dict_row

from rockygpt_brain.capabilities.transportation.models import ServiceDay


@dataclass(frozen=True)
class TrustedSourceData:
    source_id: str
    title: str
    url: str
    trust_tier: Literal["official_primary", "official_secondary", "community"]
    freshness_sla_hours: int
    collected_at: datetime


@dataclass(frozen=True)
class TrustedTripData:
    trip_id: str
    source_record_key: str
    route: str
    service_day: ServiceDay
    sequence: int
    departure: str
    arrival: str
    stops: tuple[tuple[str, str], ...]
    valid_from: date | None
    valid_until: date | None
    content_hash: str
    source_id: str


@dataclass(frozen=True)
class TrustedShuttleData:
    dataset_version: str
    dataset_activated_at: datetime
    source_commit_sha: str | None
    sources: tuple[TrustedSourceData, ...]
    trips: tuple[TrustedTripData, ...]


def load_trusted_shuttle_data(database_url: str | None = None) -> TrustedShuttleData:
    """Read the active trusted shuttle dataset directly from PostgreSQL."""
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for shuttle execution")

    hostname = urlsplit(url).hostname
    if not hostname:
        raise RuntimeError("DATABASE_URL has no hostname")
    try:
        host_addresses = socket.gethostbyname_ex(hostname)[2]
    except OSError as error:
        raise RuntimeError("unable to resolve the trusted shuttle database") from error

    connection: Connection[dict[str, Any]] | None = None
    last_error: OperationalError | None = None
    for host_address in host_addresses:
        try:
            connection = connect(
                url,
                hostaddr=host_address,
                sslrootcert=certifi.where(),
                connect_timeout=5,
                row_factory=dict_row,
            )
            break
        except OperationalError as error:
            last_error = error
    if connection is None:
        raise RuntimeError("unable to connect to the trusted shuttle database") from last_error

    with connection:
        query = (
            files(__package__)
            .joinpath("queries/load_schedule.sql")
            .read_text(encoding="utf-8")
        )
        rows: list[dict[str, Any]] = connection.execute(query).fetchall()

    if not rows:
        raise RuntimeError("the active trusted dataset has no shuttle trips")

    first = rows[0]
    sources: dict[str, TrustedSourceData] = {}
    trips: list[TrustedTripData] = []
    for row in rows:
        source_id = str(row["source_id"])
        trust_tier = str(row["trust_tier"])
        if trust_tier not in {"official_primary", "official_secondary", "community"}:
            raise RuntimeError(f"unsupported source trust tier in database: {trust_tier}")
        sources[source_id] = TrustedSourceData(
            source_id=source_id,
            title=str(row["source_title"]),
            url=str(row["source_url"]),
            trust_tier=cast(
                Literal["official_primary", "official_secondary", "community"],
                trust_tier,
            ),
            freshness_sla_hours=int(row["freshness_sla_hours"]),
            collected_at=cast(datetime, row["collected_at"]),
        )
        raw_stops = cast(list[dict[str, Any]], row["stops"])
        service_day = str(row["service_day"])
        if service_day not in {"weekday", "saturday", "sunday"}:
            raise RuntimeError(f"unsupported shuttle service day in database: {service_day}")
        trips.append(
            TrustedTripData(
                trip_id=str(row["trip_id"]),
                source_record_key=str(row["source_record_key"]),
                route=str(row["route"]),
                service_day=cast(ServiceDay, service_day),
                sequence=int(row["sequence"]),
                departure=str(row["departure"]),
                arrival=str(row["arrival"]),
                stops=tuple((str(stop["location"]), str(stop["time"])) for stop in raw_stops),
                valid_from=cast(date | None, row["valid_from"]),
                valid_until=cast(date | None, row["valid_until"]),
                content_hash=str(row["content_hash"]),
                source_id=source_id,
            )
        )

    return TrustedShuttleData(
        dataset_version=str(first["version"]),
        dataset_activated_at=cast(datetime, first["activated_at"]),
        source_commit_sha=(
            str(first["source_commit_sha"]) if first["source_commit_sha"] is not None else None
        ),
        sources=tuple(sources.values()),
        trips=tuple(trips),
    )
