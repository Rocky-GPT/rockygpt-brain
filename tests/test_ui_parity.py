"""What the data service used to answer, and Brain now has to answer identically.

These seven routes moved off `rockygpt-data`, and the Node service that defined
their shape is being switched off. Once it is gone nothing is left to diff
against, so its answers are captured under `fixtures/ui_parity/expected/` and
asserted here against Brain's, over the artifact payloads in
`fixtures/ui_parity/artifacts/` that produced them.

The faculty fixture holds 25 of the 223 published contacts, and the directory
baseline is the data service's own answer with the rest removed rather than
anything regenerated — every entry in it is still Node's bytes. 25 is a prefix
of the contacts *sorted by name*, which is the order ids are assigned in, so
`faculty-1..25` are unchanged by the trim. No contact in the published set
merges more than one row, so nothing is lost by keeping fewer of them.

The subtle part is not the data, it is the request-time reshaping the Node
routes did on the way out: a dining label repeated inside `time`, a closed range
dropped unless it stands alone, an absent `office` omitted rather than sent as
null because `JSON.stringify` drops `undefined`. Each of those was a real
mismatch during the cutover, and each is pinned below.

What these tests do *not* cover: `PostgresData`'s json/jsonb codec. Payloads
arrive here already decoded, so a stub cannot show what asyncpg would return.
`test_artifact_payload_is_decoded` covers that, and needs a database.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.services.artifacts import PublishedArtifact
from rockygpt_brain.services.data import DataUnavailable
from rockygpt_brain.services.ui_data import InvalidDate, UiDataService, _location_hours

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ui_parity"
CAMPUS = ZoneInfo("America/New_York")

# The day the baselines were captured. `menu` reads the clock rather than a
# parameter, so the clock is frozen to the captured day or its answer drifts.
CAPTURED_DATE = "2026-08-28"
FROZEN = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _load(*parts: str) -> Any:
    return json.loads((FIXTURES.joinpath(*parts)).read_text())


def expected(name: str) -> Any:
    return _load("expected", f"{name}.json")


class StubArtifacts:
    """The published artifacts, exactly as the database handed them over."""

    def __init__(self) -> None:
        self._meta = _load("artifacts", "_meta.json")

    async def artifact(self, key: str) -> PublishedArtifact:
        path = FIXTURES / "artifacts" / f"{key}.json"
        if not path.exists():
            raise DataUnavailable(f"The active dataset has no {key!r} artifact")
        meta = self._meta[key]
        return PublishedArtifact(
            payload=json.loads(path.read_text()),
            release_version=meta["release_version"],
            activated_at=meta["activated_at"],
            content_hash=meta["content_hash"],
        )


class FrozenDatetime(datetime):
    """`datetime` with a fixed `now`; every other constructor is untouched."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return FROZEN.astimezone(tz) if tz else FROZEN.replace(tzinfo=None)


@pytest.fixture
def service() -> UiDataService:
    return UiDataService(StubArtifacts())


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from rockygpt_brain.services import ui_data

    monkeypatch.setattr(ui_data, "datetime", FrozenDatetime)


async def test_shuttle_matches_the_data_service(service: UiDataService) -> None:
    assert await service.shuttle() == expected("shuttle")


async def test_map_matches_the_data_service(service: UiDataService) -> None:
    assert await service.map() == expected("map")


async def test_menu_matches_the_data_service(
    service: UiDataService, frozen_clock: None
) -> None:
    payload, _ = await service.menu()
    assert payload == expected("menu")


async def test_menu_browse_matches_the_data_service(service: UiDataService) -> None:
    payload, _ = await service.menu_browse(CAPTURED_DATE)
    assert payload == expected("menu-browse")


async def test_dining_hours_matches_the_data_service(service: UiDataService) -> None:
    payload, _ = await service.dining_hours(CAPTURED_DATE)
    assert payload == expected("dining-hours")


async def test_published_artifact_is_served_unchanged(service: UiDataService) -> None:
    artifact = await service.artifact("hours")
    assert artifact.payload == expected("data-hours")


async def test_directory_matches_the_data_service(service: UiDataService) -> None:
    """Identical but for `generatedAt`, which is deliberately not Node's format.

    Node passed the activation timestamp through as PostgreSQL had rendered it
    — `2026-08-27 21:05:42.447898+00`. The UI parses that field with `new Date`
    and gates on a `T` separator, so the space-separated form reads as Invalid
    Date in WebKit. Brain sends ISO 8601 instead, and this is the one place the
    cutover deliberately does not reproduce the old answer.
    """
    payload, _ = await service.directory()
    baseline = expected("directory")

    generated = payload.pop("generatedAt")
    node_generated = baseline.pop("generatedAt")
    assert payload == baseline

    assert generated != node_generated
    assert datetime.fromisoformat(generated).tzinfo is not None
    assert "T" in generated and generated.endswith("Z")


async def test_offices_without_a_room_omit_the_key(service: UiDataService) -> None:
    """`office: undefined` vanished through `JSON.stringify`; null would not have."""
    payload, _ = await service.directory()
    offices = [c for c in payload["allContacts"] if c["kind"] == "office"]
    missing = [c for c in offices if "office" not in c]
    assert missing, "fixture no longer covers an office without a room"
    assert all(c["office"] is not None for c in offices if "office" in c)


def _range(
    sh: str, sm: str, sp: str, fh: str, fm: str, fp: str, label: str | None = None
) -> dict[str, Any]:
    """One published range. Times arrive as string parts, not integers."""
    return {
        **({"label": label} if label else {}),
        "startTime": {"hour": sh, "minute": sm, "period": sp},
        "finishTime": {"hour": fh, "minute": fm, "period": fp},
    }


def test_labelled_hours_repeat_the_label_inside_time() -> None:
    hours = _location_hours([_range("11", "00", "AM", "02", "00", "PM", "Lunch"),
                             _range("05", "00", "PM", "08", "00", "PM")])
    assert hours[0]["time"].startswith("Lunch: ")
    assert not hours[1]["time"].startswith(":")
    assert "label" not in hours[1]


def test_closed_ranges_survive_only_when_alone() -> None:
    only = _location_hours([{"label": "Brunch"}])
    assert only == [{"label": "Brunch", "time": "Closed"}]

    alongside = _location_hours(
        [{"label": "Brunch"}, _range("05", "00", "PM", "08", "00", "PM", "Dinner")]
    )
    assert [entry["label"] for entry in alongside] == ["Dinner"]


def test_a_closed_range_never_takes_the_label_prefix() -> None:
    assert _location_hours([{"label": "Brunch"}])[0]["time"] == "Closed"


@pytest.mark.parametrize("value", ["notadate", "2026-13-99", "08-28-2026", "2026-2-8"])
async def test_unusable_dates_are_the_callers_fault(
    service: UiDataService, value: str
) -> None:
    """Both the shape and the calendar: `2026-13-99` clears the pattern and is
    still not a date, and it must not be filed as unavailable data."""
    with pytest.raises(InvalidDate):
        await service.dining_hours(value)


async def test_a_missing_artifact_is_not_reported_as_a_bad_date() -> None:
    """The failure that made this distinction worth having.

    `dining-hours` came back without `locations`, the route answered "date must
    use YYYY-MM-DD", and a valid date took the blame for missing data.
    """

    class Empty:
        async def artifact(self, key: str) -> PublishedArtifact:
            raise DataUnavailable(f"The active dataset has no {key!r} artifact")

    service = UiDataService(Empty())
    with pytest.raises(DataUnavailable):
        await service.dining_hours(CAPTURED_DATE)
    with pytest.raises(DataUnavailable):
        await service.menu_browse(CAPTURED_DATE)


def _database_url() -> str | None:
    from rockygpt_brain.config import get_settings

    settings = get_settings()
    return settings.secret_value(settings.database_url)


@pytest.mark.skipif(not _database_url(), reason="needs a database")
async def test_artifact_payload_is_decoded() -> None:
    """asyncpg hands back json/jsonb as text unless a codec says otherwise.

    Without one, `artifact()` returned a JSON string, every consumer expecting
    an object failed, and `/v1/data/programs` serialised a second time on the
    way out. A stub cannot reproduce that, so this one talks to the database.
    """
    from rockygpt_brain.services.postgres_data import PostgresData

    url = _database_url()
    assert url is not None
    data = PostgresData(url)
    await data.initialize()
    try:
        artifact = await data.artifact("hours")
        assert not isinstance(artifact.payload, str)
    finally:
        await data.close()
