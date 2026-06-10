"""Persisted session records so capture sessions survive restarts."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN, SessionPhase

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.sessions"


@dataclass(slots=True)
class SessionRecord:
    """One interrupted or in-flight session, keyed by its directory name."""

    entry_id: str
    started_at: str | None
    phase: SessionPhase

    @classmethod
    def from_dict(cls, data: dict) -> SessionRecord:
        return cls(
            entry_id=data["entry_id"],
            started_at=data.get("started_at"),
            phase=SessionPhase(data.get("phase", SessionPhase.CAPTURING)),
        )


class SessionStore:
    """Domain-level store of active session records, keyed per subentry/dir."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, dict[str, dict]]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._data: dict[str, dict[str, dict]] | None = None
        self._load_lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load the store from disk once; later calls are no-ops."""
        async with self._load_lock:
            if self._data is None:
                self._data = await self._store.async_load() or {}

    @property
    def _records(self) -> dict[str, dict[str, dict]]:
        assert self._data is not None, "SessionStore used before async_load"
        return self._data

    def records(self, subentry_id: str) -> dict[str, SessionRecord]:
        """Return the persisted records for one trigger, keyed by dir name."""
        return {
            dir_name: SessionRecord.from_dict(raw)
            for dir_name, raw in self._records.get(subentry_id, {}).items()
        }

    async def async_set(
        self, subentry_id: str, dir_name: str, record: SessionRecord
    ) -> None:
        """Upsert a session record and save immediately."""
        self._records.setdefault(subentry_id, {})[dir_name] = asdict(record)
        await self._store.async_save(self._records)

    async def async_remove(self, subentry_id: str, dir_name: str) -> None:
        """Remove one session record if present."""
        subentry_records = self._records.get(subentry_id)
        if subentry_records is None or dir_name not in subentry_records:
            return
        del subentry_records[dir_name]
        if not subentry_records:
            del self._records[subentry_id]
        await self._store.async_save(self._records)

    async def async_remove_subentry(self, subentry_id: str) -> None:
        """Drop all records for a deleted trigger."""
        if self._records.pop(subentry_id, None) is not None:
            await self._store.async_save(self._records)

    async def async_remove_entry(self, entry_id: str) -> None:
        """Drop all records belonging to a removed config entry."""
        stale = [
            subentry_id
            for subentry_id, records in self._records.items()
            if any(raw.get("entry_id") == entry_id for raw in records.values())
        ]
        for subentry_id in stale:
            del self._records[subentry_id]
        if stale:
            await self._store.async_save(self._records)

    def subentry_ids_for_entry(self, entry_id: str) -> set[str]:
        """Return subentry ids that have records for the given entry."""
        return {
            subentry_id
            for subentry_id, records in self._records.items()
            if any(raw.get("entry_id") == entry_id for raw in records.values())
        }


@callback
def async_get_session_store(hass: HomeAssistant) -> SessionStore:
    """Return the shared session store, creating it on first use."""
    return hass.data.setdefault(DOMAIN, SessionStore(hass))
