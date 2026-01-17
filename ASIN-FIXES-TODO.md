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

### 2. Deduplicate Parallel Virtual Book Creation (MEDIUM PRIORITY)

**Location:** `app/routers/api/search.py:441-519`

**Problem:**
Multiple async tasks can create the same virtual ASIN:
```python
fallback_asin = generate_virtual_asin(p_result.title, p_result.author)  # Deterministic
fallback_book = Audiobook(asin=fallback_asin, ...)  # In memory, not yet in DB
```

Later at line 564, all tasks call `session.merge(enriched)` which can conflict.

**Solution:**

**A. Deduplication Before Database Operations**
```python
# Track seen virtual ASINs in memory
seen_virtual_asins = set()
deduplicated_results = []

for book, p_result in parallel_results:
    if book.asin.startswith("VIRTUAL-"):
        if book.asin not in seen_virtual_asins:
            seen_virtual_asins.add(book.asin)
            deduplicated_results.append((book, p_result))
    else:
        deduplicated_results.append((book, p_result))
```

**B. Async Lock Per ASIN**
```python
import asyncio
from collections import defaultdict

asin_locks = defaultdict(asyncio.Lock)

async def fetch_with_timeout(res, semaphore):
    virtual_asin = generate_virtual_asin(res.title, res.author)
    async with asin_locks[virtual_asin]:  # Only one task creates this ASIN
        # ... existing fetch logic ...
```

**Effort:** 30 minutes
**Risk:** Low - pure deduplication logic

---

### 3. Add Upsert Pattern to store_new_books() (LOW PRIORITY)

**Location:** `app/internal/book_search.py:545`

**Problem:**
```python
session.add_all(to_add + existing)  # Assumes perfect state
session.commit()                     # No IntegrityError handling
```

If database state is corrupted or concurrent operations occur, this can fail.

**Solution:**
```python
def store_new_books(session: Session, books: list[Audiobook]):
    from sqlalchemy.exc import IntegrityError

    for book in books:
        try:
            # Use merge for upsert-like behavior
            session.merge(book)
        except IntegrityError:
            logger.warning(f"Skipping duplicate book: {book.asin}")
            session.rollback()
            continue

    try:
        session.commit()
    except IntegrityError as e:
        session.rollback()
        logger.error(f"Failed to commit books: {e}")
        raise
```

**Effort:** 20 minutes
**Risk:** Very Low - defensive programming

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
