import threading
import time
from abc import ABC
from collections import OrderedDict
from typing import overload

from sqlmodel import Session, select

from app.internal.models import Config


class CacheMetrics:
    """Thread-safe cache metrics tracker."""

    hits: int
    misses: int
    evictions: int
    _lock: threading.Lock

    def __init__(self):
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def record_hit(self):
        with self._lock:
            self.hits += 1

    def record_miss(self):
        with self._lock:
            self.misses += 1

    def record_eviction(self):
        with self._lock:
            self.evictions += 1

    def hit_rate(self) -> float:
        """Return cache hit rate as percentage (0-100)."""
        with self._lock:
            total = self.hits + self.misses
            if total == 0:
                return 0.0
            return (self.hits / total) * 100

    def reset(self):
        """Reset all metrics to zero."""
        with self._lock:
            self.hits = 0
            self.misses = 0
            self.evictions = 0


class ModificationTracker:
    """Thread-safe tracker for file modification times.

    Prevents race conditions when checking file modifications across multiple
    concurrent requests. Uses threading.Lock to ensure atomic reads/writes.
    """

    _lock: threading.Lock
    _modification_time: float

    def __init__(self):
        self._lock = threading.Lock()
        self._modification_time = 0.0

    def has_changed(self, current_mtime: float) -> bool:
        """Check if file modification time has changed.

        Args:
            current_mtime: Current file modification time from os.path.getmtime()

        Returns:
            True if mtime differs from tracked value, False if unchanged.
        """
        with self._lock:
            if current_mtime != self._modification_time:
                self._modification_time = current_mtime
                return True
            return False

    def reset(self):
        """Reset the tracked modification time to 0."""
        with self._lock:
            self._modification_time = 0


class SimpleCache[VT, *KTs]:
    _cache: OrderedDict[tuple[*KTs], tuple[int, VT]]
    _lock: threading.Lock
    _maxsize: int | None
    _metrics: CacheMetrics

    def __init__(self, maxsize: int | None = None):
        """Initialize cache with optional size limit.

        Args:
            maxsize: Maximum number of entries. None = unlimited. Uses LRU eviction.
        """
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._maxsize = maxsize
        self._metrics = CacheMetrics()

    def get(self, source_ttl: int, *query: *KTs) -> VT | None:
        with self._lock:
            hit = self._cache.get(query)
            if not hit:
                self._metrics.record_miss()
                return None
            cached_at, sources = hit
            if cached_at + source_ttl < time.time():
                self._metrics.record_miss()
                return None
            # Move to end for LRU tracking
            self._cache.move_to_end(query)
            self._metrics.record_hit()
            return sources

    def get_all(self, source_ttl: int) -> dict[tuple[*KTs], VT]:
        with self._lock:
            now = int(time.time())

            return {
                query: sources
                for query, (cached_at, sources) in self._cache.items()
                if cached_at + source_ttl > now
            }

    def set(self, sources: VT, *query: *KTs):
        with self._lock:
            # Remove old entry if exists to update position
            if query in self._cache:
                del self._cache[query]

            # Add new entry at end (most recently used)
            self._cache[query] = (int(time.time()), sources)

            # Evict oldest entry if over maxsize
            if self._maxsize is not None and len(self._cache) > self._maxsize:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                self._metrics.record_eviction()

    def flush(self):
        with self._lock:
            self._cache = OrderedDict()

    def get_metrics(self) -> CacheMetrics:
        """Return the metrics tracker for this cache."""
        return self._metrics

    def size(self) -> int:
        """Return current number of entries in cache."""
        with self._lock:
            return len(self._cache)


class StringConfigCache[L: str](ABC):
    _cache: dict[L, str] = {}

    @overload
    def get(self, session: Session, key: L) -> str | None: ...

    @overload
    def get(self, session: Session, key: L, default: str) -> str: ...

    def get(self, session: Session, key: L, default: str | None = None) -> str | None:
        if key in self._cache:
            return self._cache[key]
        return (
            session.exec(select(Config.value).where(Config.key == key)).one_or_none()
            or default
        )

    def set(self, session: Session, key: L, value: str):
        old = session.exec(select(Config).where(Config.key == key)).one_or_none()
        if old:
            old.value = value
        else:
            old = Config(key=key, value=value)
        session.add(old)
        session.commit()
        self._cache[key] = value

    def delete(self, session: Session, key: L):
        old = session.exec(select(Config).where(Config.key == key)).one_or_none()
        if old:
            session.delete(old)
            session.commit()
        if key in self._cache:
            del self._cache[key]

    @overload
    def get_int(self, session: Session, key: L) -> int | None: ...

    @overload
    def get_int(self, session: Session, key: L, default: int) -> int: ...

    def get_int(
        self, session: Session, key: L, default: int | None = None
    ) -> int | None:
        val = self.get(session, key)
        if val:
            return int(val)
        return default

    def set_int(self, session: Session, key: L, value: int):
        self.set(session, key, str(value))

    def get_bool(self, session: Session, key: L) -> bool | None:
        try:
            val = self.get_int(session, key)
        except ValueError:  # incase if the db has an old bool string instead of an int
            return False
        if val is not None:
            return val != 0
        return None

    def set_bool(self, session: Session, key: L, value: bool):
        self.set_int(session, key, int(value))
