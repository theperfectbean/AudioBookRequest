# Database Integrity Risk Scan Results

## Scan Summary
- **Files with DB writes:** 13
- **Files with IntegrityError handling:** 2 (requests.py, search.py) ✅
- **Models with primary keys:** 8
- **Potential risks found:** 3

## Critical Findings

### 1. User Creation Race Condition ⚠️

**Files:**
- `app/routers/api/users.py:149-150`
- `app/routers/api/users.py:215-216`

**Risk:** Concurrent user creation with same username
**Primary Key:** `User.username` (line 25 in models.py)

**Current Code:**
```python
session.add(user)
session.commit()
```

**Risk Level:** MEDIUM
- User creation likely infrequent
- But no IntegrityError handling = 500 errors on collision

**Recommendation:** Add try/except IntegrityError, return 409 "Username already exists"

---

### 2. User Last Login Updates ⚠️

**Files:**
- `app/routers/auth.py:149-150`
- `app/routers/auth.py:272-273`

**Risk:** Concurrent last_login updates (less critical)
**Operation:** UPDATE existing user

**Current Code:**
```python
user.last_login = datetime.now()
session.add(user)
session.commit()
```

**Risk Level:** LOW
- Updates to existing records, not inserts
- Worst case: one login timestamp lost
- No data corruption risk

**Recommendation:** Add rollback on exception, but IntegrityError unlikely

---

### 3. APIKey Creation (Not Found in Scan)

**Model:** `APIKey.key` primary key (line 246 in models.py)

**Risk Level:** LOW-MEDIUM
- Keys are randomly generated (UUIDs or secure random)
- Collision probability extremely low
- But no IntegrityError handling found

**Recommendation:** Review APIKey creation code if it exists

---

## Models Using UUIDs (Safe) ✅

These use random UUIDs as primary keys - collision risk negligible:
- `ManualBookRequest.id` (removed in recent commit)
- `NotificationMethod.id`
- `NotificationEndpoint.id`

No action needed.

---

## Already Fixed ✅

- `AudiobookRequest` (asin + user_username) - Fixed in commit 3088eb1
- `Audiobook` (asin) virtual book merges - Fixed in commit 3088eb1

---

## Recommendation Priority

1. **HIGH:** Fix remaining ASIN issues (see ASIN-FIXES-TODO.md)
2. **MEDIUM:** Add IntegrityError handling to user creation (users.py)
3. **LOW:** Add generic rollback to auth.py last_login updates
4. **AUDIT:** Search for APIKey creation code

## Safe Patterns Found ✅

These files likely have proper error handling or low risk:
- `app/util/cache.py` - Cache operations
- `app/routers/api/settings/*` - Settings updates
- `tests/test_prowlarr_search.py` - Test code

---

## Quick Fix Template

For user creation in `users.py`:

```python
from sqlalchemy.exc import IntegrityError

try:
    session.add(user)
    session.commit()
except IntegrityError:
    session.rollback()
    raise HTTPException(status_code=409, detail="Username already exists")
except Exception as e:
    session.rollback()
    logger.exception("Failed to create user", username=user.username)
    raise HTTPException(status_code=500, detail="Failed to create user")
```

---

**Generated:** 2026-01-17
**Scan Method:** Regex pattern matching for session operations
**Files Scanned:** 13 Python files in app/ directory
