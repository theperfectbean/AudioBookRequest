# ASIN Duplicate Insertion - Remaining Fixes

## Completed ✅

1. **TOCTOU Race in Request Creation** (requests.py:88-91)
   - Added IntegrityError handler for duplicate request constraint violations
   - Returns 409 instead of 500 on race conditions
   - Commit: 3088eb1

2. **Virtual Book Merge Conflicts** (search.py:578-587)
   - Added IntegrityError handler for parallel enrichment conflicts
   - Graceful handling with non-persisted fallback
   - Commit: 3088eb1

## Remaining Work 🚧

### 1. Fix Virtual Book Upgrade Race Condition ✅ **COMPLETED**

**Location:** `app/routers/api/search.py:195-346` (check_and_upgrade_virtual_book)

**Status:** Fixed in commit `f2c439e` (2026-01-17)

**Implementation:** Database-level locking with enhanced error recovery

**Changes Made:**
- Added `SELECT FOR UPDATE` row locking to prevent concurrent upgrades
- Implemented request migration logic to preserve user requests during upgrade
- Enhanced IntegrityError recovery to re-query database after rollback
- Added emergency fallback to recreate virtual book if state is lost
- Improved cache invalidation to prevent poisoned cache entries

**Files Modified:**
- `app/routers/api/search.py`: Enhanced upgrade logic and cache wrapper

**See:** `ASIN-FIX-PLAN.md` for full implementation details

---

### 2. Deduplicate Parallel Virtual Book Creation ✅ **COMPLETED**

**Location:** `app/routers/api/search.py:610-638`

**Status:** Fixed in commit `871b0cd` (2026-01-17)

**Implementation:** In-memory deduplication using set tracking

**Changes Made:**
- Added `seen_virtual_asins: set[str]` to track virtual ASINs before database operations
- Skip duplicate virtual ASINs in parallel results processing loop
- Prevents IntegrityError from multiple async tasks creating identical virtual books
- Also fixed missing `subtitle=None` parameter in virtual book instantiation

**Solution Used:** Option A (deduplication before database operations) - simpler and more efficient than async locks

**Files Modified:**
- `app/routers/api/search.py`: Added deduplication logic at line 613-623

---

### 3. Add Upsert Pattern to store_new_books() ✅ **COMPLETED**

**Location:** `app/internal/book_search.py:545-569`

**Status:** Fixed in commit `871b0cd` (2026-01-17)

**Implementation:** Merge pattern with graceful error recovery

**Changes Made:**
- Replaced `session.add_all()` with individual `session.merge()` calls for upsert-like behavior
- Added IntegrityError exception handling with batch + individual fallback strategy
- Batch merge first (fast path), then individual merge on conflict (slow path with logging)
- Graceful degradation: logs warnings for duplicates but continues processing other books

**Approach:**
1. Try batch merge + commit (optimistic)
2. On IntegrityError, rollback and retry individual merges
3. Skip duplicates with warning logs, continue with remaining books

**Files Modified:**
- `app/internal/book_search.py`: Enhanced store_new_books() with merge pattern and error handling

---

## Testing Checklist

After implementing fixes, test these scenarios:

- [ ] Concurrent requests for the same virtual book upgrade
- [ ] Parallel searches that would create identical virtual ASINs
- [ ] Rapid-fire duplicate request creation from same user
- [ ] Database failures during enrichment merge
- [ ] Mixed virtual + real book batch inserts

## Performance Considerations

- Virtual book upgrade cache (implemented) reduces redundant API calls
- Ranking cache (implemented) speeds up repeated searches
- Semaphore limits (implemented) prevent API rate limiting

## Related Files

- `app/internal/models.py:67-101` - Audiobook model (ASIN is primary key)
- `app/routers/api/requests.py` - Request creation (fixed)
- `app/routers/api/search.py` - Search logic (partially fixed)
- `app/internal/book_search.py` - Book storage utilities

## References

- Investigation report: See conversation history for detailed analysis
- Commit 3088eb1: IntegrityError handling fixes
