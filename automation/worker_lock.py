"""Database-backed worker lock management."""

from __future__ import annotations

import logging
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.database import DatabaseManager

logger = logging.getLogger(__name__)

LOCK_TTL_MINUTES = 30


class WorkerLock:
    """Prevent concurrent execution of the same worker type."""

    def __init__(self, database: DatabaseManager, lock_name: str) -> None:
        self._db = database
        self._lock_name = lock_name
        self._owner_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._acquired = False

    @property
    def owner_id(self) -> str:
        return self._owner_id

    def acquire(self, ttl_minutes: int = LOCK_TTL_MINUTES) -> bool:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
        acquired = self._db.acquire_worker_lock(self._lock_name, self._owner_id, expires_at)
        self._acquired = acquired
        if acquired:
            logger.info("Acquired worker lock %s as %s.", self._lock_name, self._owner_id)
        else:
            logger.warning("Failed to acquire worker lock %s.", self._lock_name)
        return acquired

    def release(self) -> None:
        if self._acquired:
            self._db.release_worker_lock(self._lock_name, self._owner_id)
            logger.info("Released worker lock %s.", self._lock_name)
            self._acquired = False

    def __enter__(self) -> WorkerLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
