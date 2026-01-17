# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AudioBookRequest is a FastAPI-based web application for managing audiobook requests on Plex/Audiobookshelf/Jellyfin servers. It integrates with Prowlarr for automatic downloading and uses the Audible API for book metadata.

## Development Commands

```bash
# Install dependencies (uses uv, not pip)
uv sync

# Run database migrations (required before first run)
just migrate  # or: uv run alembic upgrade heads

# Start development server (runs migrations first)
just dev  # or: uv run fastapi dev

# Start Tailwind CSS watcher (required for styling changes)
just tailwind  # or: tailwindcss -i static/tw.css -o static/globals.css --watch

# Type checking and linting
just types  # runs basedpyright, djlint, ruff format check, alembic check

# Individual checks
uv run basedpyright         # type checking
uv run djlint templates     # template linting
uv run ruff format --check app  # format check

# Run tests
uv run pytest tests/
uv run pytest tests/test_prowlarr_search.py -v  # specific file
uv run pytest tests/ --cov=app --cov-report=html  # with coverage

# Create database migration
just create_revision "message"  # or: uv run alembic revision --autogenerate -m "message"

# Docker (local profile)
docker compose --profile local up --build
```

## Architecture

### Core Stack
- **FastAPI** with Jinja2 templates and HTMX for frontend interactivity
- **SQLModel** (SQLAlchemy + Pydantic) for ORM with SQLite/PostgreSQL support
- **Alembic** for database migrations
- **Tailwind CSS** with DaisyUI for styling

### Directory Structure

- `app/main.py` - FastAPI app initialization, middleware, exception handlers
- `app/routers/` - Web page routes (search, wishlist, settings, auth)
- `app/routers/api/` - REST API endpoints under `/api`
- `app/internal/` - Core business logic
  - `auth/` - Authentication (forms, OIDC, API keys, sessions)
  - `prowlarr/` - Prowlarr integration for indexer searches
  - `ranking/` - Download source quality scoring
  - `indexers/` - Custom indexer implementations (MAM)
  - `metadata/` - Book metadata enrichment (Google Books)
- `app/util/` - Shared utilities (DB, caching, templates, logging)
- `templates/` - Jinja2 HTML templates
- `static/` - CSS, JS assets
- `alembic/versions/` - Database migrations

### Key Models (`app/internal/models.py`)

- `User` - Authentication with groups (untrusted/trusted/admin)
- `Audiobook` - Cached book metadata with ASIN as primary key
- `AudiobookRequest` - User requests linked to audiobooks
- `APIKey` - Argon2-hashed API keys for programmatic access

### Authentication System

Multiple auth methods configured via `ABR_APP__FORCE_LOGIN_TYPE`:
- `forms` - Session-based login
- `oidc` - OpenID Connect federation
- `basic` - HTTP Basic Auth
- `api_key` - Bearer token for API
- `none` - Disabled (all requests as admin)

### Configuration

Environment variables prefixed with `ABR_` using nested delimiter `__`:
- `ABR_APP__DEBUG`, `ABR_APP__PORT`, `ABR_APP__CONFIG_DIR`
- `ABR_DB__USE_POSTGRES`, `ABR_DB__POSTGRES_*` for PostgreSQL
- Settings loaded from `.env.local` or `.env` files

### Prowlarr Integration

The app queries Prowlarr for audiobook sources, ranks them by quality heuristics, and can auto-download for trusted users. Virtual ASINs are generated for books found on indexers but not in Audible.

## Conventions

- Uses Conventional Commits for commit messages
- Python 3.12+ required (uses new generics syntax)
- Alembic migrations: manually add unique constraints for PostgreSQL ALTER TABLE
- Template linting with djlint (Jinja profile)

## Testing Policy
- Do NOT run test suites automatically
- Only suggest test commands for me to run manually in a separate terminal
- Focus on code generation and review, not execution validation
- Exception: Only run tests if I explicitly request it for critical verification

## Implementation Journal

### 2026-01-17: Virtual Book Upgrade Race Condition Fix (Comprehensive)

**What was built:** Fixed critical database integrity issue where concurrent virtual book upgrades could cause primary key violations, detached session objects, and lost user requests. Implemented database-level locking with comprehensive error recovery and request migration.

**Plan followed:** ASIN-FIX-PLAN.md (Copilot implementation of Opus architectural plan)

**Files changed:**
- `app/routers/api/search.py`:
  - Import: Added `AudiobookRequest` to handle request migration
  - `check_and_upgrade_virtual_book()` (lines 189-346): Complete rewrite with 4-step upgrade process
  - `check_and_upgrade_virtual_book_cached()` (lines 349-406): Enhanced exception handling

**Implementation details:**

1. **Database-level locking** (Step 1):
   - Added `SELECT FOR UPDATE` to acquire exclusive row lock on virtual book
   - Prevents concurrent transactions from simultaneously upgrading same virtual ASIN
   - Works in PostgreSQL (true row locking) and SQLite (database-level lock)

2. **Request migration** (Step 4 from plan):
   - Before deleting virtual book, queries all `AudiobookRequest` records linked to it
   - Creates new request records with real ASIN to preserve user requests
   - Prevents CASCADE deletion from losing user request history

3. **Enhanced IntegrityError recovery** (Step 1 improvements):
   - After rollback, re-queries database to get current state
   - Returns virtual book if still exists (concurrent upgrade also failed)
   - Returns real book if another request succeeded (race condition winner)
   - Emergency fallback: recreates virtual book if both disappeared (database corruption)

4. **Generic exception recovery** (defensive programming):
   - Similar re-query logic for non-IntegrityError exceptions
   - Ensures returned object is always session-bound (prevents detached object errors)
   - Logs detailed error information for debugging

5. **Cache invalidation** (Step 2):
   - Wrapped upgrade call in try/except to catch unhandled exceptions
   - Prevents poisoned cache entries from propagating
   - Allows retry on next request if transient error

**Why this approach:**
- Database-level locking more reliable than application-level semaphores
- Works across multiple instances/containers (production deployment)
- Request migration preserves user intent during upgrade (critical for UX)
- Re-query pattern ensures session consistency after rollback
- Emergency fallbacks prevent total failure in edge cases

**Edge cases handled:**
- Concurrent upgrades to same virtual book → first wins, second returns winner's result
- Concurrent upgrades to different virtual books → both succeed (no contention)
- Virtual book with existing user requests → requests migrated before deletion
- Database corruption → emergency fallback recreates virtual book
- Cache failures → retry allowed, no poisoned entries

**Deviations from plan:**
- None - implemented all 4 steps from ASIN-FIX-PLAN.md exactly as designed

**Testing recommendations:**
- Test concurrent requests: 5+ parallel searches for same book (see plan Test Scenario 1)
- Test request migration: Create virtual book with requests, trigger upgrade, verify requests transferred
- Test IntegrityError path: Use mock to force IntegrityError, verify recovery logic
- Manual: Search for "Evolution of God" with available_only=true, check logs for upgrade flow

---

### 2026-01-17: Virtual Book Deduplication and Upsert Pattern (Defensive)

**What was built:** Fixed remaining ASIN duplicate insertion issues with in-memory deduplication and defensive database operations. Prevents IntegrityError from parallel virtual book creation and batch storage failures.

**Plan followed:** ASIN-FIXES-TODO.md tasks 2 and 3 (Copilot autonomous implementation)

**Files changed:**
- `app/routers/api/search.py` (lines 613-623, 563, 594):
  - Added `seen_virtual_asins: set[str]` tracking before results_map insertion
  - Skip duplicate virtual ASINs in parallel_results processing loop
  - Fixed missing `subtitle=None` parameter in Audiobook instantiation (2 locations)
  
- `app/internal/book_search.py` (lines 545-569):
  - Replaced `session.add_all()` with individual `session.merge()` calls
  - Added IntegrityError handling with batch+individual fallback strategy
  - Graceful degradation with warning logs for duplicate books

**Implementation details:**

1. **Virtual ASIN Deduplication (Task 2)**:
   - Problem: Multiple async tasks can create identical virtual ASINs (deterministic hash from title+author)
   - Solution: Track `seen_virtual_asins` set and skip duplicates before database operations
   - Why: Simpler than async locks, catches duplicates at earliest point (before results_map)
   - Performance: O(1) set lookups, minimal overhead

2. **Upsert Pattern in store_new_books() (Task 3)**:
   - Problem: `add_all()` fails with IntegrityError if book already exists (no recovery)
   - Solution: Use `merge()` for upsert-like behavior with 2-tier error handling
   - Fast path: Batch merge all books, commit once
   - Slow path: On IntegrityError, rollback and merge individually with per-book logging
   - Why: Optimistic approach (batch) with pessimistic fallback (individual)

**Why this approach:**
- Deduplication prevents creation of duplicates (better than handling after failure)
- In-memory tracking simpler than distributed locks, works within single request lifecycle
- Merge pattern handles both insert and update, eliminates need for explicit existence checks
- 2-tier strategy balances performance (batch fast path) with resilience (individual fallback)

**Edge cases handled:**
- Parallel searches returning identical Prowlarr results → only first virtual book kept
- Concurrent batch storage attempts → merge handles conflicts gracefully
- Mixed real + virtual books in batch → all handled uniformly
- Individual merge failures → logged but don't block other books

**Testing recommendations:**
- Test parallel searches for same book with multiple Prowlarr matches
- Test rapid-fire searches that would create duplicate virtual ASINs
- Test store_new_books() with pre-existing books in database
- Monitor logs for "Skipping duplicate virtual ASIN" and "Skipping duplicate book during storage" messages
