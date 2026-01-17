"""
Comprehensive test suite for the notification dispatch system.

Tests cover:
1. Notification model validation
2. Variable replacement logic
3. Notification sending (success/failure paths)
4. Error handling and retry logic
5. Multiple simultaneous notifications
6. Empty or invalid configurations
7. All notification body types (text, JSON)
8. Database queries for notifications
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call, Mock
from typing import Optional
from contextlib import asynccontextmanager

import pytest
from aiohttp import ClientSession, InvalidUrlClientError, ClientError
from sqlmodel import Session, select

from app.internal.models import (
    Audiobook,
    EventEnum,
    Notification,
    NotificationBodyTypeEnum,
    User,
    GroupEnum,
)
from app.internal.notifications import (
    _replace_variables,
    _send,
    send_notification,
    send_all_notifications,
)


def create_async_context_manager_mock(return_value=None):
    """Helper to create a proper async context manager mock."""
    async_cm = AsyncMock()
    async_cm.__aenter__.return_value = return_value or AsyncMock()
    async_cm.__aexit__.return_value = None
    return async_cm


class TestNotificationModels:
    """Test notification model creation and validation."""

    def test_notification_creation(self, db_session: Session):
        """Test creating a notification record in database."""
        notification = Notification(
            name="Test Webhook",
            url="https://example.com/webhook",
            headers={"Content-Type": "application/json"},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.json,
            body='{"title": "{bookTitle}", "user": "{eventUser}"}',
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()
        db_session.refresh(notification)

        assert notification.id is not None
        assert notification.name == "Test Webhook"
        assert notification.url == "https://example.com/webhook"
        assert notification.enabled is True
        assert notification.event == EventEnum.on_new_request

    def test_notification_with_empty_headers(self, db_session: Session):
        """Test notification with empty headers dictionary."""
        notification = Notification(
            name="Simple Webhook",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_successful_download,
            body_type=NotificationBodyTypeEnum.text,
            body="Download completed: {bookTitle}",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()
        db_session.refresh(notification)

        assert notification.headers == {}

    def test_notification_disabled(self, db_session: Session):
        """Test creating disabled notification."""
        notification = Notification(
            name="Disabled Webhook",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_failed_download,
            body_type=NotificationBodyTypeEnum.json,
            body="{}",
            enabled=False,
        )
        db_session.add(notification)
        db_session.commit()
        db_session.refresh(notification)

        assert notification.enabled is False

    def test_all_event_types(self, db_session: Session):
        """Test all supported event types."""
        for event in EventEnum:
            notification = Notification(
                name=f"Test {event.value}",
                url="https://example.com/webhook",
                headers={},
                event=event,
                body_type=NotificationBodyTypeEnum.text,
                body="Test body",
                enabled=True,
            )
            db_session.add(notification)

        db_session.commit()
        notifications = db_session.exec(select(Notification)).all()
        assert len(notifications) == len(EventEnum)

    def test_all_body_types(self, db_session: Session):
        """Test all supported body types."""
        for body_type in NotificationBodyTypeEnum:
            body = '{"test": "json"}' if body_type == NotificationBodyTypeEnum.json else "plain text"
            notification = Notification(
                name=f"Test {body_type.value}",
                url="https://example.com/webhook",
                headers={},
                event=EventEnum.on_new_request,
                body_type=body_type,
                body=body,
                enabled=True,
            )
            db_session.add(notification)

        db_session.commit()
        notifications = db_session.exec(select(Notification)).all()
        assert len(notifications) == len(NotificationBodyTypeEnum)

    def test_notification_serialized_headers(self, db_session: Session):
        """Test serialized_headers property handles special characters."""
        headers = {"Authorization": "Bearer token123", "X-Custom": "value"}
        notification = Notification(
            name="Test Headers",
            url="https://example.com/webhook",
            headers=headers,
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.json,
            body="{}",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()
        db_session.refresh(notification)

        serialized = notification.serialized_headers
        assert "Authorization" in serialized
        assert "token123" in serialized


class TestVariableReplacement:
    """Test variable replacement in notification bodies."""

    def test_replace_user_variables(self):
        """Test replacing user-related variables."""
        user = User(
            username="testuser",
            password="hash",
            group=GroupEnum.trusted,
            extra_data="user_extra_data",
        )
        template = "User {eventUser} with data {eventUserExtraData} requested a book"
        result = _replace_variables(template, user=user)

        assert result == "User testuser with data user_extra_data requested a book"

    def test_replace_user_variables_none(self):
        """Test that missing user doesn't cause errors."""
        template = "User {eventUser} requested a book"
        result = _replace_variables(template, user=None)

        assert result == "User {eventUser} requested a book"

    def test_replace_book_variables(self):
        """Test replacing book-related variables."""
        template = "Book {bookTitle} by {bookAuthors} narrated by {bookNarrators}"
        result = _replace_variables(
            template,
            book_title="The Way of Kings",
            book_authors="Brandon Sanderson",
            book_narrators="Michael Kramer, Kate Reading",
        )

        expected = "Book The Way of Kings by Brandon Sanderson narrated by Michael Kramer, Kate Reading"
        assert result == expected

    def test_replace_event_type_variable(self):
        """Test replacing event type variable."""
        template = "Event type: {eventType}"
        result = _replace_variables(template, event_type=EventEnum.on_new_request.value)

        assert result == "Event type: onNewRequest"

    def test_replace_custom_variables(self):
        """Test replacing custom variables via other_replacements."""
        template = "Custom value: {customKey} and {anotherKey}"
        result = _replace_variables(
            template,
            other_replacements={"customKey": "value1", "anotherKey": "value2"},
        )

        assert result == "Custom value: value1 and value2"

    def test_replace_all_variables_combined(self):
        """Test replacing all variables at once."""
        user = User(username="alice", password="hash", group=GroupEnum.admin, extra_data="admin")
        template = (
            "User {eventUser} ({eventUserExtraData}) requested {bookTitle} "
            "by {bookAuthors}. Event: {eventType}. Custom: {ref}"
        )
        result = _replace_variables(
            template,
            user=user,
            book_title="Mistborn",
            book_authors="Brandon Sanderson",
            book_narrators="Michael Kramer",
            event_type=EventEnum.on_successful_download.value,
            other_replacements={"ref": "123"},
        )

        expected = (
            "User alice (admin) requested Mistborn by Brandon Sanderson. "
            "Event: onSuccessfulDownload. Custom: 123"
        )
        assert result == expected

    def test_replace_variables_with_none_values(self):
        """Test replacing variables when all optional parameters are None."""
        template = "Simple text without variables"
        result = _replace_variables(template)

        assert result == "Simple text without variables"

    def test_replace_variables_empty_strings(self):
        """Test replacing with empty string values - they don't trigger replacement."""
        template = "Author: {bookAuthors} Title: {bookTitle}"
        result = _replace_variables(template, book_authors="", book_title="")

        # Empty strings are falsy, so variables are NOT replaced
        assert result == "Author: {bookAuthors} Title: {bookTitle}"


class TestSendFunction:
    """Test the _send helper function."""

    @pytest.mark.asyncio
    async def test_send_json_body(self):
        """Test sending notification with JSON body."""
        notification = Notification(
            name="JSON Test",
            url="https://example.com/webhook",
            headers={"Content-Type": "application/json"},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.json,
            body='{"title": "Test"}',
            enabled=True,
        )
        body = {"title": "Test"}

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)
        mock_session = Mock(spec=ClientSession)
        mock_session.post = Mock(return_value=mock_cm)

        result = await _send(body, notification, mock_session)

        assert result == "OK"
        mock_session.post.assert_called_once_with(
            notification.url,
            json=body,
            headers=notification.headers,
        )

    @pytest.mark.asyncio
    async def test_send_text_body(self):
        """Test sending notification with text body."""
        notification = Notification(
            name="Text Test",
            url="https://example.com/webhook",
            headers={"Content-Type": "text/plain"},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Plain text message",
            enabled=True,
        )
        body = "Plain text message"

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="Accepted")

        mock_cm = create_async_context_manager_mock(mock_response)
        mock_session = Mock(spec=ClientSession)
        mock_session.post = Mock(return_value=mock_cm)

        result = await _send(body, notification, mock_session)

        assert result == "Accepted"
        mock_session.post.assert_called_once_with(
            notification.url,
            data=body,
            headers=notification.headers,
        )

    @pytest.mark.asyncio
    async def test_send_invalid_url_error(self):
        """Test handling of invalid URL error."""
        notification = Notification(
            name="Invalid URL",
            url="not-a-valid-url",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="test",
            enabled=True,
        )

        mock_session = Mock(spec=ClientSession)
        mock_session.post = Mock(side_effect=InvalidUrlClientError(Mock()))

        with pytest.raises(ValueError, match="Invalid URL"):
            await _send("body", notification, mock_session)

    @pytest.mark.asyncio
    async def test_send_http_error(self):
        """Test handling of HTTP errors from response."""
        notification = Notification(
            name="HTTP Error",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.json,
            body="{}",
            enabled=True,
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock(side_effect=Exception("500 Server Error"))

        mock_cm = create_async_context_manager_mock(mock_response)
        mock_session = Mock(spec=ClientSession)
        mock_session.post = Mock(return_value=mock_cm)

        with pytest.raises(Exception, match="500 Server Error"):
            await _send({}, notification, mock_session)

    @pytest.mark.asyncio
    async def test_send_with_custom_headers(self):
        """Test sending with custom headers."""
        notification = Notification(
            name="Custom Headers",
            url="https://example.com/webhook",
            headers={"Authorization": "Bearer token", "X-Custom": "value"},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.json,
            body="{}",
            enabled=True,
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)
        mock_session = Mock(spec=ClientSession)
        mock_session.post = Mock(return_value=mock_cm)

        result = await _send({}, notification, mock_session)

        mock_session.post.assert_called_once()
        call_kwargs = mock_session.post.call_args[1]
        assert call_kwargs["headers"] == notification.headers


class TestSendNotification:
    """Test send_notification function."""

    @pytest.mark.asyncio
    async def test_send_notification_success(self, db_session: Session):
        """Test successful notification sending."""
        notification = Notification(
            name="Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Book requested: {bookTitle}",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        # Create a book for reference
        book = Audiobook(
            asin="B001",
            title="Test Book",
            subtitle="A Test",
            authors=["Author One"],
            narrators=["Narrator One"],
            cover_image="https://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=100,
        )
        db_session.add(book)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(return_value=mock_cm)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            result = await send_notification(
                session=db_session,
                notification=notification,
                book_asin="B001",
            )

            assert result == "OK"

    @pytest.mark.asyncio
    async def test_send_notification_with_user(self, db_session: Session):
        """Test notification sending with user information."""
        user = User(
            username="testuser",
            password="hash",
            group=GroupEnum.trusted,
            extra_data="extra",
        )
        db_session.add(user)

        notification = Notification(
            name="Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="User {eventUser} requested a book",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="Received")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(return_value=mock_cm)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            result = await send_notification(
                session=db_session,
                notification=notification,
                requester=user,
            )

            assert result == "Received"

    @pytest.mark.asyncio
    async def test_send_notification_json_body_parsing(self, db_session: Session):
        """Test JSON body is properly parsed before sending."""
        notification = Notification(
            name="JSON Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.json,
            body='{"event": "{eventType}", "book": "{bookTitle}"}',
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        book = Audiobook(
            asin="B002",
            title="JSON Book",
            subtitle="Test",
            authors=["Author"],
            narrators=["Narrator"],
            cover_image="https://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=100,
        )
        db_session.add(book)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(return_value=mock_cm)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            result = await send_notification(
                session=db_session,
                notification=notification,
                book_asin="B002",
            )

            assert result == "OK"
            # Verify JSON was parsed
            call_kwargs = mock_session.post.call_args[1]
            assert isinstance(call_kwargs["json"], dict)

    @pytest.mark.asyncio
    async def test_send_notification_book_not_found(self, db_session: Session):
        """Test notification sending when book is not found."""
        notification = Notification(
            name="Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Book: {bookTitle} by {bookAuthors}",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(return_value=mock_cm)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            result = await send_notification(
                session=db_session,
                notification=notification,
                book_asin="NONEXISTENT",
            )

            # Should still send notification with unreplaced variables
            assert result == "OK"

    @pytest.mark.asyncio
    async def test_send_notification_with_custom_replacements(self, db_session: Session):
        """Test notification with custom replacement variables."""
        notification = Notification(
            name="Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Status: {status}, Request ID: {requestId}",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(return_value=mock_cm)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            result = await send_notification(
                session=db_session,
                notification=notification,
                other_replacements={"status": "success", "requestId": "REQ123"},
            )

            assert result == "OK"

    @pytest.mark.asyncio
    async def test_send_notification_generic_error(self, db_session: Session):
        """Test error handling for generic exceptions."""
        notification = Notification(
            name="Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Test",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(side_effect=ClientError("Connection failed"))
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            with pytest.raises(ClientError, match="Connection failed"):
                await send_notification(
                    session=db_session,
                    notification=notification,
                )


class TestSendAllNotifications:
    """Test send_all_notifications function."""

    @pytest.mark.asyncio
    async def test_send_all_notifications_multiple(self, db_session: Session):
        """Test sending multiple notifications for same event."""
        # Create multiple notifications
        for i in range(3):
            notification = Notification(
                name=f"Webhook {i}",
                url=f"https://example.com/webhook{i}",
                headers={},
                event=EventEnum.on_new_request,
                body_type=NotificationBodyTypeEnum.text,
                body="Test",
                enabled=True,
            )
            db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.get_session") as mock_get_session:
            mock_get_session.return_value = iter([db_session])

            with patch("app.internal.notifications.ClientSession") as mock_client_class:
                mock_session = Mock()
                mock_session.post = Mock(return_value=mock_cm)
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)

                mock_client_class.return_value = mock_session

                await send_all_notifications(EventEnum.on_new_request)

                # Verify post was called 3 times (once per notification)
                assert mock_session.post.call_count == 3

    @pytest.mark.asyncio
    async def test_send_all_notifications_filters_by_event(self, db_session: Session):
        """Test that only notifications for matching event are sent."""
        # Create notifications for different events
        for event in EventEnum:
            notification = Notification(
                name=f"Webhook {event.value}",
                url=f"https://example.com/{event.value}",
                headers={},
                event=event,
                body_type=NotificationBodyTypeEnum.text,
                body="Test",
                enabled=True,
            )
            db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.get_session") as mock_get_session:
            mock_get_session.return_value = iter([db_session])

            with patch("app.internal.notifications.ClientSession") as mock_client_class:
                mock_session = Mock()
                mock_session.post = Mock(return_value=mock_cm)
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)

                mock_client_class.return_value = mock_session

                await send_all_notifications(EventEnum.on_new_request)

                # Should only send notification for on_new_request
                assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_send_all_notifications_skips_disabled(self, db_session: Session):
        """Test that disabled notifications are not sent."""
        # Create enabled and disabled notifications
        for i in range(2):
            notification = Notification(
                name=f"Webhook {i}",
                url=f"https://example.com/webhook{i}",
                headers={},
                event=EventEnum.on_new_request,
                body_type=NotificationBodyTypeEnum.text,
                body="Test",
                enabled=(i == 0),  # Only first is enabled
            )
            db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.get_session") as mock_get_session:
            mock_get_session.return_value = iter([db_session])

            with patch("app.internal.notifications.ClientSession") as mock_client_class:
                mock_session = Mock()
                mock_session.post = Mock(return_value=mock_cm)
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)

                mock_client_class.return_value = mock_session

                await send_all_notifications(EventEnum.on_new_request)

                # Should only send 1 notification (the enabled one)
                assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_send_all_notifications_no_notifications(self, db_session: Session):
        """Test handling when no notifications exist for event."""
        with patch("app.internal.notifications.get_session") as mock_get_session:
            mock_get_session.return_value = iter([db_session])

            # Should not raise error, just log
            await send_all_notifications(EventEnum.on_new_request)

    @pytest.mark.asyncio
    async def test_send_all_notifications_failed_notification(self, db_session: Session):
        """Test error logging when notification fails."""
        notification = Notification(
            name="Failing Webhook",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Test",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        with patch("app.internal.notifications.get_session") as mock_get_session:
            mock_get_session.return_value = iter([db_session])

            with patch("app.internal.notifications.ClientSession") as mock_client_class:
                # Patch send_notification to return None/falsy value
                with patch("app.internal.notifications.send_notification", AsyncMock(return_value=None)):
                    mock_session = Mock()
                    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                    mock_session.__aexit__ = AsyncMock(return_value=None)

                    mock_client_class.return_value = mock_session

                    # Should log error when succ is None/falsy
                    await send_all_notifications(EventEnum.on_new_request)

    @pytest.mark.asyncio
    async def test_send_all_notifications_with_context(self, db_session: Session):
        """Test sending notifications with user and book context."""
        user = User(
            username="testuser",
            password="hash",
            group=GroupEnum.trusted,
            extra_data="extra",
        )
        db_session.add(user)

        book = Audiobook(
            asin="B003",
            title="Context Book",
            subtitle="Test",
            authors=["Author"],
            narrators=["Narrator"],
            cover_image="https://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=100,
        )
        db_session.add(book)

        notification = Notification(
            name="Context Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="{eventUser} requested {bookTitle}",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.get_session") as mock_get_session:
            mock_get_session.return_value = iter([db_session])

            with patch("app.internal.notifications.ClientSession") as mock_client_class:
                mock_session = Mock()
                mock_session.post = Mock(return_value=mock_cm)
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)

                mock_client_class.return_value = mock_session

                await send_all_notifications(
                    EventEnum.on_new_request,
                    requester=user,
                    book_asin="B003",
                    other_replacements={"extra": "data"},
                )

                assert mock_session.post.call_count == 1


class TestNotificationErrorHandling:
    """Test error handling in notification system."""

    @pytest.mark.asyncio
    async def test_invalid_json_body(self, db_session: Session):
        """Test handling of invalid JSON in body template."""
        notification = Notification(
            name="Invalid JSON",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.json,
            body='{"invalid": json}',  # Missing quotes
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        with patch("app.internal.notifications.ClientSession"):
            with pytest.raises(json.JSONDecodeError):
                await send_notification(
                    session=db_session,
                    notification=notification,
                )

    @pytest.mark.asyncio
    async def test_network_timeout(self, db_session: Session):
        """Test handling of network timeout."""
        notification = Notification(
            name="Timeout Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Test",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(side_effect=asyncio.TimeoutError("Request timed out"))
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            with pytest.raises(asyncio.TimeoutError):
                await send_notification(
                    session=db_session,
                    notification=notification,
                )

    @pytest.mark.asyncio
    async def test_connection_error(self, db_session: Session):
        """Test handling of connection errors."""
        notification = Notification(
            name="Connection Error",
            url="https://nonexistent.example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Test",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(side_effect=ClientError("Failed to connect"))
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            with pytest.raises(ClientError):
                await send_notification(
                    session=db_session,
                    notification=notification,
                )


class TestNotificationEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_notification_with_very_long_body(self, db_session: Session):
        """Test notification with very long body template."""
        long_body = "X" * 10000  # 10KB body
        notification = Notification(
            name="Long Body",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body=long_body,
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()
        db_session.refresh(notification)

        assert len(notification.body) == 10000

    def test_notification_with_many_headers(self, db_session: Session):
        """Test notification with many custom headers."""
        headers = {f"X-Custom-{i}": f"Value{i}" for i in range(50)}
        notification = Notification(
            name="Many Headers",
            url="https://example.com/webhook",
            headers=headers,
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.json,
            body="{}",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()
        db_session.refresh(notification)

        assert len(notification.headers) == 50

    @pytest.mark.asyncio
    async def test_send_notification_concurrent_requests(self, db_session: Session):
        """Test concurrent notification sending."""
        notification = Notification(
            name="Concurrent Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Test",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(return_value=mock_cm)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            # Send multiple notifications concurrently
            tasks = [
                send_notification(session=db_session, notification=notification)
                for _ in range(5)
            ]
            results = await asyncio.gather(*tasks)

            assert all(r == "OK" for r in results)
            assert mock_session.post.call_count == 5

    def test_notification_event_enum_values(self):
        """Test all EventEnum values are properly defined."""
        expected_events = {"on_new_request", "on_successful_download", "on_failed_download"}
        actual_events = {event.name for event in EventEnum}

        assert expected_events == actual_events

    def test_notification_body_type_enum_values(self):
        """Test all NotificationBodyTypeEnum values are properly defined."""
        expected_types = {"text", "json"}
        actual_types = {body_type.name for body_type in NotificationBodyTypeEnum}

        assert expected_types == actual_types

    @pytest.mark.asyncio
    async def test_empty_other_replacements_dict(self, db_session: Session):
        """Test that empty other_replacements dict is handled correctly."""
        notification = Notification(
            name="Test",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.text,
            body="Test message",
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(return_value=mock_cm)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            result = await send_notification(
                session=db_session,
                notification=notification,
                other_replacements={},
            )

            assert result == "OK"


class TestNotificationIntegration:
    """Integration tests for full notification workflow."""

    @pytest.mark.asyncio
    async def test_full_workflow_new_request(self, db_session: Session):
        """Test full workflow for new book request notification."""
        # Setup
        user = User(
            username="requester",
            password="hash",
            group=GroupEnum.trusted,
            extra_data="premium",
        )
        db_session.add(user)

        book = Audiobook(
            asin="B999",
            title="Epic Fantasy",
            subtitle="Book 1",
            authors=["Fantasy Author"],
            narrators=["Great Narrator"],
            cover_image="https://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=500,
        )
        db_session.add(book)

        notification = Notification(
            name="New Request Alert",
            url="https://example.com/requests",
            headers={"Authorization": "Bearer token"},
            event=EventEnum.on_new_request,
            body_type=NotificationBodyTypeEnum.json,
            body='{"user": "{eventUser}", "title": "{bookTitle}", "authors": "{bookAuthors}"}',
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="Accepted")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(return_value=mock_cm)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            result = await send_notification(
                session=db_session,
                notification=notification,
                requester=user,
                book_asin="B999",
            )

            assert result == "Accepted"
            # Verify correct data was posted
            call_kwargs = mock_session.post.call_args[1]
            assert call_kwargs["json"]["user"] == "requester"
            assert call_kwargs["json"]["title"] == "Epic Fantasy"

    @pytest.mark.asyncio
    async def test_full_workflow_with_all_context(self, db_session: Session):
        """Test notification with all available context variables."""
        user = User(
            username="alice",
            password="hash",
            group=GroupEnum.admin,
            extra_data="admin_data",
        )
        db_session.add(user)

        book = Audiobook(
            asin="B888",
            title="Complete Context",
            subtitle="Comprehensive Test",
            authors=["Author A", "Author B"],
            narrators=["Narrator X", "Narrator Y"],
            cover_image="https://example.com/cover.jpg",
            release_date=datetime.now(timezone.utc),
            runtime_length_min=600,
        )
        db_session.add(book)

        notification = Notification(
            name="Complete Context",
            url="https://example.com/webhook",
            headers={},
            event=EventEnum.on_successful_download,
            body_type=NotificationBodyTypeEnum.json,
            body=(
                '{"user": "{eventUser}", "userData": "{eventUserExtraData}", '
                '"event": "{eventType}", "title": "{bookTitle}", '
                '"authors": "{bookAuthors}", "narrators": "{bookNarrators}", '
                '"custom": "{custom}"}'
            ),
            enabled=True,
        )
        db_session.add(notification)
        db_session.commit()

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.text = AsyncMock(return_value="OK")

        mock_cm = create_async_context_manager_mock(mock_response)

        with patch("app.internal.notifications.ClientSession") as mock_client_class:
            mock_session = Mock()
            mock_session.post = Mock(return_value=mock_cm)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_session

            result = await send_notification(
                session=db_session,
                notification=notification,
                requester=user,
                book_asin="B888",
                other_replacements={"custom": "custom_value"},
            )

            assert result == "OK"
            call_kwargs = mock_session.post.call_args[1]
            body = call_kwargs["json"]

            assert body["user"] == "alice"
            assert body["userData"] == "admin_data"
            assert body["event"] == EventEnum.on_successful_download.value
            assert body["title"] == "Complete Context"
            assert "Author A" in body["authors"]
            assert "Narrator X" in body["narrators"]
            assert body["custom"] == "custom_value"
