"""A DataPort backed by canned records, so the harness can be tested off-network.

Only the tests use this. It exists because the harness's own test suite must
not depend on the data service being awake — the thing being tested is the
comparison, not the campus.
"""

from __future__ import annotations

from typing import Any


class StubData:
    """Answers every DataPort method from whatever was handed to the constructor."""

    def __init__(self, **records: list[dict[str, Any]]) -> None:
        self._records = records

    def _for(self, method: str) -> list[dict[str, Any]]:
        return [dict(record) for record in self._records.get(method, [])]

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self._for("shuttle")

    async def dining(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("dining")

    async def events(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("events")

    async def campus_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("campus_hours")

    async def dining_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("dining_hours")

    async def courses(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("courses")

    async def course_subjects(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("course_subjects")

    async def transportation(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return self._for("transportation")

    async def calendar(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("calendar")

    async def clubs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("clubs")

    async def directory(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("directory")

    async def locations(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("locations")

    async def programs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._for("programs")
