
import threading
import time
import pytest
from app.util.cache import ModificationTracker, SimpleCache, CacheMetrics


class TestModificationTracker:
    """Thread-safe file modification tracker tests."""

    def test_has_changed_initial_state_always_changes(self):
        """First call to has_changed with any mtime should return True."""
        tracker = ModificationTracker()
        assert tracker.has_changed(123.456) is True

    def test_has_changed_same_mtime_returns_false(self):
        """Repeated calls with same mtime should return False."""
        tracker = ModificationTracker()
        tracker.has_changed(100.0)
        assert tracker.has_changed(100.0) is False

    def test_has_changed_new_mtime_returns_true(self):
        """Different mtime should return True and update internal state."""
        tracker = ModificationTracker()
        tracker.has_changed(100.0)
        assert tracker.has_changed(101.0) is True
        assert tracker.has_changed(101.0) is False

    def test_reset_clears_state(self):
        """Reset should allow next has_changed to return True."""
        tracker = ModificationTracker()
        tracker.has_changed(100.0)
        tracker.reset()
        assert tracker.has_changed(100.0) is True

    def test_thread_safety_concurrent_modifications(self):
        """Multiple threads accessing tracker simultaneously should be safe."""
        tracker = ModificationTracker()
        results = []
        
        def worker(mtime):
            result = tracker.has_changed(mtime)
            results.append(result)
        
        threads = [
            threading.Thread(target=worker, args=(100.0,)),
            threading.Thread(target=worker, args=(100.0,)),
            threading.Thread(target=worker, args=(101.0,)),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Results should show safe concurrent access (no crashes, valid values)
        assert all(isinstance(r, bool) for r in results)
        assert len(results) == 3

    def test_thread_safety_no_state_corruption(self):
        """Concurrent access should not corrupt internal state."""
        tracker = ModificationTracker()
        tracker.has_changed(100.0)
        
        changes = []
        
        def check_state():
            for _ in range(100):
                changes.append(tracker.has_changed(100.0))
                time.sleep(0.001)
        
        threads = [threading.Thread(target=check_state) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # After concurrent access with same mtime, all should be False
        # (first call sets it, rest just check)
        assert all(not c for c in changes[1:])


class TestSimpleCacheLRU:
    """LRU eviction tests for SimpleCache."""

    def test_simple_cache_no_maxsize_unlimited(self):
        """Cache without maxsize should store unlimited entries."""
        cache = SimpleCache()
        for i in range(1000):
            cache.set([f"book_{i}"], f"query_{i}")
        assert cache.size() == 1000

    def test_simple_cache_lru_eviction_removes_oldest(self):
        """When maxsize exceeded, oldest entry should be evicted."""
        cache = SimpleCache(maxsize=3)
        cache.set(["book1"], "q1")
        cache.set(["book2"], "q2")
        cache.set(["book3"], "q3")
        assert cache.size() == 3

        # Add 4th entry - should evict oldest (q1)
        cache.set(["book4"], "q4")
        assert cache.size() == 3
        assert cache.get(1000, "q1") is None
        assert cache.get(1000, "q4") is not None

    def test_simple_cache_lru_get_moves_to_end(self):
        """Accessing an entry should move it to end (mark as recently used)."""
        cache = SimpleCache(maxsize=3)
        cache.set(["book1"], "q1")
        cache.set(["book2"], "q2")
        cache.set(["book3"], "q3")

        # Access q1 - should mark it as recently used
        cache.get(1000, "q1")

        # Add q4 - should evict q2 (not q1, since q1 was accessed)
        cache.set(["book4"], "q4")
        assert cache.get(1000, "q1") is not None
        assert cache.get(1000, "q2") is None

    def test_simple_cache_metrics_hit_miss(self):
        """Metrics should track hit/miss rates accurately."""
        cache = SimpleCache()
        metrics = cache.get_metrics()

        cache.set(["book1"], "q1")
        assert cache.get(1000, "q1") is not None  # Hit
        assert cache.get(1000, "q2") is None  # Miss

        assert metrics.hits == 1
        assert metrics.misses == 1
        assert metrics.hit_rate() == 50.0

    def test_simple_cache_metrics_eviction_count(self):
        """Metrics should track eviction count."""
        cache = SimpleCache(maxsize=2)
        metrics = cache.get_metrics()

        cache.set(["b1"], "q1")
        cache.set(["b2"], "q2")
        cache.set(["b3"], "q3")  # Evict q1
        cache.set(["b4"], "q4")  # Evict q2

        assert metrics.evictions == 2

    def test_simple_cache_metrics_reset(self):
        """Metrics reset should clear all counters."""
        cache = SimpleCache()
        metrics = cache.get_metrics()

        cache.set(["book"], "query")
        cache.get(1000, "query")
        cache.get(1000, "missing")

        assert metrics.hits > 0
        assert metrics.misses > 0

        metrics.reset()
        assert metrics.hits == 0
        assert metrics.misses == 0
        assert metrics.evictions == 0

    def test_simple_cache_set_updates_lru_position(self):
        """Setting existing key should move it to end (recently used)."""
        cache = SimpleCache(maxsize=3)
        cache.set(["b1"], "q1")
        cache.set(["b2"], "q2")
        cache.set(["b3"], "q3")

        # Update q1
        cache.set(["b1_new"], "q1")

        # Add q4 - should evict q2 (not q1, since q1 was updated)
        cache.set(["b4"], "q4")
        assert cache.get(1000, "q1") is not None
        assert cache.get(1000, "q2") is None

    def test_simple_cache_thread_safe_eviction(self):
        """Concurrent operations should not corrupt LRU order."""
        cache = SimpleCache(maxsize=5)
        import threading

        def worker(id):
            for i in range(10):
                cache.set([f"book_{id}_{i}"], f"q_{id}_{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Cache should never exceed maxsize
        assert cache.size() <= 5
        # All operations should complete without errors
        metrics = cache.get_metrics()
        assert isinstance(metrics.evictions, int)


class TestCacheMetrics:
    """CacheMetrics class tests."""

    def test_metrics_initial_state(self):
        """New metrics should start at zero."""
        metrics = CacheMetrics()
        assert metrics.hits == 0
        assert metrics.misses == 0
        assert metrics.evictions == 0
        assert metrics.hit_rate() == 0.0

    def test_metrics_hit_rate_no_data(self):
        """Hit rate with no data should be 0.0."""
        metrics = CacheMetrics()
        assert metrics.hit_rate() == 0.0

    def test_metrics_hit_rate_calculation(self):
        """Hit rate should be calculated correctly."""
        metrics = CacheMetrics()
        for _ in range(75):
            metrics.record_hit()
        for _ in range(25):
            metrics.record_miss()

        assert metrics.hit_rate() == 75.0

    def test_metrics_thread_safe_increment(self):
        """Concurrent record operations should be thread-safe."""
        metrics = CacheMetrics()
        import threading

        def worker():
            for _ in range(100):
                metrics.record_hit()
                metrics.record_miss()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert metrics.hits == 500
        assert metrics.misses == 500


class TestCachePerformance:
    """Performance benchmarks for cache behavior."""

    def test_performance_lru_eviction_under_load(self):
        """Test cache eviction performance with concurrent operations."""
        cache = SimpleCache(maxsize=50)
        import time

        start = time.time()
        for i in range(1000):
            cache.set([f"book_{i}"], f"query_{i}")
        elapsed = time.time() - start

        # Should handle 1000 sets in reasonable time even with LRU tracking
        assert elapsed < 5.0  # 5 seconds max
        assert cache.size() == 50  # Maxsize maintained
        assert cache.get_metrics().evictions >= 950  # Evicted excess entries

    def test_performance_cache_hit_lookup_speed(self):
        """Cache get() should be fast even with TTL checks."""
        cache = SimpleCache()
        for i in range(100):
            cache.set([f"book_{i}"], f"data_{i}", f"query_{i}")

        start = time.time()
        for i in range(10000):
            cache.get(3600, f"query_{i % 100}")
        elapsed = time.time() - start

        # 10k lookups should be very fast
        assert elapsed < 1.0

    def test_performance_concurrent_access(self):
        """Cache should handle concurrent access efficiently."""
        cache = SimpleCache(maxsize=100)
        import threading

        results = []

        def worker():
            for i in range(100):
                cache.set([f"b_{threading.current_thread().ident}_{i}"], f"q_{i}")
                cache.get(3600, f"q_{i}")
                results.append(1)

        start = time.time()
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start

        # 10 threads x 100 operations should complete reasonably fast
        assert elapsed < 10.0
        assert len(results) == 1000
        assert cache.size() <= 100
