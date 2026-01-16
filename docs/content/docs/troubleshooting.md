---
title: Troubleshooting
---

# Troubleshooting

## Internal Server Error (500) - NameError in Logs

**Symptom:** The application returns a 500 Internal Server Error when accessing certain pages (like `/search`). The Docker logs show:

```
NameError: name 'query' is not defined
```

**Root Cause:** The Docker container is running outdated code. This typically happens when:
- Code has been updated but the Docker image hasn't been rebuilt
- A git pull was performed but the container wasn't rebuilt

**Solution:** Rebuild the Docker containers to pick up the latest code:

```bash
docker compose down
docker compose --profile local up --build
```

The `--build` flag forces Docker to rebuild the image with the current codebase.

**Prevention:** Always rebuild containers after:
- Pulling new code from git
- Making significant code changes
- Switching branches
- Updating dependencies

**Technical Details:**

In this specific case (January 2026), the error occurred in `app/internal/book_search.py` in the `list_popular_books()` function. The stale container had outdated logging code at line ~378 that referenced an undefined `query` variable:

```python
# Incorrect (old code in stale container):
logger.info(f"📚 AUDIBLE API RESULTS | Query: '{query}' | Found {len(ordered)} books")
```

The current code correctly logs without referencing the `query` parameter (which doesn't exist in `list_popular_books()`):

```python
# Correct (current code):
logger.info(f"📚 POPULAR BOOKS | Found {len(ordered)} books")
```

The function `list_popular_books()` doesn't have a `query` parameter, so referencing it caused the NameError.
