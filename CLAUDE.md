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

Detailed implementation history is maintained in [IMPLEMENTATION_JOURNAL.md](./IMPLEMENTATION_JOURNAL.md).

### Recent Changes (Last 2 Weeks)

**2026-01-18: Phase 3 Complete - Code Quality & Caching**
- Enhanced SimpleCache with LRU eviction and metrics tracking
- Fixed thread-safety issues in cache and global state
- Added 21 comprehensive tests for thread safety and performance

**2026-01-18: Phase 2b Complete - Race Condition Fixes**
- Fixed OIDC login and password rehash race conditions
- Added comprehensive API authorization tests (30 tests)

**2026-01-18: Phase 1 Complete - Search Performance**
- Implemented fuzzy match caching (60-80% speedup)
- Configurable concurrency semaphore (50-70% speedup)
- Virtual book upgrade caching (90%+ reduction)

See [IMPLEMENTATION_JOURNAL.md](./IMPLEMENTATION_JOURNAL.md) for complete history and detailed technical information.
