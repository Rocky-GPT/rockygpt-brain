from __future__ import annotations

from typing import Any, Protocol


class DataUnavailable(Exception):
    pass


class DataPort(Protocol):
    async def ready(self) -> None: ...

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def dining(self, query: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def events(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def campus_hours(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def dining_hours(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def courses(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def course_subjects(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def transportation(self, query: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def calendar(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def clubs(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def directory(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def locations(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def programs(self, query: dict[str, str]) -> list[dict[str, Any]]: ...


class UnavailableData:
    """Development placeholder used when no database has been configured."""

    async def _raise(self) -> list[dict[str, Any]]:
        raise DataUnavailable("DATABASE_URL is not configured")

    async def ready(self) -> None:
        raise DataUnavailable("DATABASE_URL is not configured")

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._raise()

    async def dining(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._raise()

    async def events(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()

    async def campus_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()

    async def dining_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()

    async def courses(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()

    async def course_subjects(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()

    async def transportation(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._raise()

    async def calendar(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()

    async def clubs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()

    async def directory(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()

    async def locations(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()

    async def programs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._raise()
