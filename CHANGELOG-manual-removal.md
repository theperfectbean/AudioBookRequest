# Manual Feature Removal - Changelog

**Date:** 2026-01-17
**Scope:** Complete removal of manual book request functionality

## Summary

Removed the manual book entry feature, which allowed users to manually add book requests without Audible metadata. All UI components, backend routes, database queries, and notification functions related to this feature have been removed.

## Changes

### Templates Removed
- `templates/manual.html` - Manual book entry form page
- `templates/wishlist_page/manual.html` - Manual requests wishlist view

### Templates Modified
- `templates/search.html:15-18` - Removed "Manual" button from search page header
- `templates/wishlist_page/tablist.html:12-16` - Removed "Manual" tab from wishlist navigation

### Backend Routes Removed

#### `app/routers/search.py`
- `GET /search/manual` - Display manual entry form
- `POST /search/manual` - Create/update manual request
- Removed imports: `uuid`, `ManualBookRequest`, `create_manual_request`, `ManualRequest`, `update_manual_request`

#### `app/routers/wishlist.py`
- `GET /wishlist/manual` - Display manual requests
- `PATCH /wishlist/manual/{id}` - Mark manual request as downloaded
- `DELETE /wishlist/manual/{id}` - Delete manual request
- Removed imports: `get_all_manual_requests`, `delete_manual_request`, `mark_manual_downloaded`

### API Endpoints Removed

#### `app/routers/api/requests.py`
- `GET /api/requests/manual` - List manual requests (response_model: list[ManualBookRequest])
- `POST /api/requests/manual` - Create manual request (status: 201)
- `PUT /api/requests/manual/{id}` - Update manual request (status: 204)
- `PATCH /api/requests/manual/{id}/downloaded` - Mark manual request as downloaded (status: 204)
- `DELETE /api/requests/manual/{id}` - Delete manual request (status: 204)
- Removed class: `ManualRequest` (BaseModel with title, author, narrator, subtitle, publish_date, info)
- Removed imports: `uuid`, `asc`, `ManualBookRequest`, `send_all_manual_notifications`

### Database Query Functions Removed

#### `app/internal/db_queries.py`
- `get_all_manual_requests(session, user)` - Retrieve all manual book requests for a user
- Modified `WishlistCounts` model - Removed `manual: int` field
- Modified `get_wishlist_counts()` - Removed manual request counting logic
- Removed imports: `Sequence`, `asc`, `ManualBookRequest`

### Notification Functions Removed

#### `app/internal/notifications.py`
- `send_manual_notification(notification, book, requester, other_replacements)` - Send notification for single manual request
- `send_all_manual_notifications(event_type, book_request, other_replacements)` - Send all notifications for manual request events
- Removed import: `ManualBookRequest`

### Documentation Updates
- `CLAUDE.md:73` - Removed `ManualBookRequest` from Key Models section

## Database Note

The `ManualBookRequest` model definition remains in `app/internal/models.py` for database schema compatibility. However, it is no longer referenced or used anywhere in the codebase.

If you wish to completely remove this feature including the database table, you will need to:
1. Create an Alembic migration to drop the `manualbookrequest` table
2. Remove the `ManualBookRequest` class from `app/internal/models.py`

## Testing

After removal, the application was tested:
- ✓ Server restarts without import errors
- ✓ Type checking passes (basedpyright)
- ✓ Application responds to requests normally
- ✓ No references to manual feature in active codebase

## Impact

- Users can no longer manually enter book requests without Audible metadata
- All book requests must now go through the Audible search workflow
- Admin users cannot access or manage manual requests
- Notification webhooks will no longer trigger for manual request events

## Rollback

To rollback this change, restore the following from git history:
```bash
git checkout <commit-before-removal> -- templates/manual.html
git checkout <commit-before-removal> -- templates/wishlist_page/manual.html
git checkout <commit-before-removal> -- templates/search.html
git checkout <commit-before-removal> -- templates/wishlist_page/tablist.html
git checkout <commit-before-removal> -- app/routers/search.py
git checkout <commit-before-removal> -- app/routers/wishlist.py
git checkout <commit-before-removal> -- app/routers/api/requests.py
git checkout <commit-before-removal> -- app/internal/db_queries.py
git checkout <commit-before-removal> -- app/internal/notifications.py
```
