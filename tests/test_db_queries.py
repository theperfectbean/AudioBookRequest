"""
Comprehensive test suite for database query functions in app/internal/db_queries.py.

Tests cover:
1. get_wishlist_counts() - Count requested and downloaded books
   - Admin users see all counts
   - Non-admin users see only their own counts
   - Empty results handling
   - Mixed downloaded/not_downloaded status
   
2. get_wishlist_results() - Retrieve wishlist books with filtering
   - All response types ("all", "downloaded", "not_downloaded")
   - User filtering (admin vs specific user)
   - Relationship loading with requests
   - Empty results
   - Multiple requests for same book
"""

from datetime import datetime, timezone
from typing import Annotated

import pytest
from sqlmodel import Session

from app.internal.db_queries import (
    get_wishlist_counts,
    get_wishlist_results,
    WishlistCounts,
)
from app.internal.models import (
    Audiobook,
    AudiobookRequest,
    AudiobookWishlistResult,
    User,
    GroupEnum,
)


class TestWishlistCounts:
    """Test WishlistCounts model and get_wishlist_counts() function."""

    @pytest.fixture
    def admin_user(self, db_session: Session) -> User:
        """Create an admin user."""
        user = User(
            username="admin",
            password="hashed_password",
            group=GroupEnum.admin,
            root=False,
        )
        db_session.add(user)
        db_session.commit()
        return user

    @pytest.fixture
    def trusted_user(self, db_session: Session) -> User:
        """Create a trusted user."""
        user = User(
            username="trusted_user",
            password="hashed_password",
            group=GroupEnum.trusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()
        return user

    @pytest.fixture
    def untrusted_user(self, db_session: Session) -> User:
        """Create an untrusted user."""
        user = User(
            username="untrusted_user",
            password="hashed_password",
            group=GroupEnum.untrusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()
        return user

    @pytest.fixture
    def sample_audiobooks(self, db_session: Session) -> dict[str, Audiobook]:
        """Create sample audiobooks for testing."""
        books = {
            "downloaded_book": Audiobook(
                asin="B001",
                title="Downloaded Book",
                subtitle=None,
                authors=["Author One"],
                narrators=["Narrator One"],
                cover_image=None,
                release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=600,
                downloaded=True,
            ),
            "not_downloaded_book": Audiobook(
                asin="B002",
                title="Not Downloaded Book",
                subtitle=None,
                authors=["Author Two"],
                narrators=["Narrator Two"],
                cover_image=None,
                release_date=datetime(2021, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=500,
                downloaded=False,
            ),
            "another_not_downloaded": Audiobook(
                asin="B003",
                title="Another Not Downloaded",
                subtitle=None,
                authors=["Author Three"],
                narrators=["Narrator Three"],
                cover_image=None,
                release_date=datetime(2022, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=700,
                downloaded=False,
            ),
        }
        for book in books.values():
            db_session.add(book)
        db_session.commit()
        return books

    def test_get_wishlist_counts_with_no_requests(self, db_session: Session):
        """Empty database should return zero counts."""
        counts = get_wishlist_counts(db_session)
        assert isinstance(counts, WishlistCounts)
        assert counts.requests == 0
        assert counts.downloaded == 0

    def test_get_wishlist_counts_admin_sees_all_counts(
        self,
        db_session: Session,
        admin_user: User,
        sample_audiobooks: dict[str, Audiobook],
    ):
        """Admin user should see all request counts."""
        # Create requests from multiple users
        request1 = AudiobookRequest(
            asin="B001", user_username="trusted_user", request_date=datetime.now(timezone.utc)
        )
        request2 = AudiobookRequest(
            asin="B002", user_username="untrusted_user", request_date=datetime.now(timezone.utc)
        )
        request3 = AudiobookRequest(
            asin="B003", user_username="trusted_user", request_date=datetime.now(timezone.utc)
        )
        db_session.add_all([request1, request2, request3])
        db_session.commit()

        # Admin should see all 3 requests (2 not downloaded, 1 downloaded)
        counts = get_wishlist_counts(db_session, user=admin_user)
        assert counts.requests == 2  # B002, B003 are not_downloaded
        assert counts.downloaded == 1  # B001 is downloaded

    def test_get_wishlist_counts_non_admin_sees_only_own_requests(
        self,
        db_session: Session,
        trusted_user: User,
        sample_audiobooks: dict[str, Audiobook],
    ):
        """Non-admin user should see only their own request counts."""
        # Create requests from multiple users
        request1 = AudiobookRequest(
            asin="B001", user_username="trusted_user", request_date=datetime.now(timezone.utc)
        )
        request2 = AudiobookRequest(
            asin="B002", user_username="other_user", request_date=datetime.now(timezone.utc)
        )
        request3 = AudiobookRequest(
            asin="B003", user_username="trusted_user", request_date=datetime.now(timezone.utc)
        )
        db_session.add_all([request1, request2, request3])
        db_session.commit()

        # Trusted user should see only their 2 requests (1 downloaded, 1 not)
        counts = get_wishlist_counts(db_session, user=trusted_user)
        assert counts.requests == 1  # B003 is not_downloaded
        assert counts.downloaded == 1  # B001 is downloaded

    def test_get_wishlist_counts_no_user_specified_shows_all(
        self,
        db_session: Session,
        sample_audiobooks: dict[str, Audiobook],
    ):
        """When no user is specified, should show all counts (same as admin)."""
        request1 = AudiobookRequest(
            asin="B001", user_username="user1", request_date=datetime.now(timezone.utc)
        )
        request2 = AudiobookRequest(
            asin="B002", user_username="user2", request_date=datetime.now(timezone.utc)
        )
        request3 = AudiobookRequest(
            asin="B003", user_username="user1", request_date=datetime.now(timezone.utc)
        )
        db_session.add_all([request1, request2, request3])
        db_session.commit()

        counts = get_wishlist_counts(db_session)
        assert counts.requests == 2  # B002, B003 not downloaded
        assert counts.downloaded == 1  # B001 downloaded

    def test_get_wishlist_counts_only_downloaded(
        self,
        db_session: Session,
        sample_audiobooks: dict[str, Audiobook],
    ):
        """Test counts when all books are downloaded."""
        # Create audiobook that's downloaded
        downloaded = Audiobook(
            asin="B004",
            title="All Downloaded",
            subtitle=None,
            authors=["Author"],
            narrators=["Narrator"],
            cover_image=None,
            release_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=600,
            downloaded=True,
        )
        db_session.add(downloaded)
        db_session.commit()

        request = AudiobookRequest(
            asin="B004", user_username="user1", request_date=datetime.now(timezone.utc)
        )
        db_session.add(request)
        db_session.commit()

        counts = get_wishlist_counts(db_session)
        assert counts.requests == 0
        assert counts.downloaded == 1

    def test_get_wishlist_counts_only_not_downloaded(
        self,
        db_session: Session,
        sample_audiobooks: dict[str, Audiobook],
    ):
        """Test counts when all books are not downloaded."""
        request1 = AudiobookRequest(
            asin="B002", user_username="user1", request_date=datetime.now(timezone.utc)
        )
        request2 = AudiobookRequest(
            asin="B003", user_username="user2", request_date=datetime.now(timezone.utc)
        )
        db_session.add_all([request1, request2])
        db_session.commit()

        counts = get_wishlist_counts(db_session)
        assert counts.requests == 2
        assert counts.downloaded == 0

    def test_get_wishlist_counts_multiple_requests_same_book(
        self,
        db_session: Session,
        sample_audiobooks: dict[str, Audiobook],
    ):
        """Multiple requests for same book should count as multiple requests."""
        # Multiple users request the same book
        request1 = AudiobookRequest(
            asin="B002", user_username="user1", request_date=datetime.now(timezone.utc)
        )
        request2 = AudiobookRequest(
            asin="B002", user_username="user2", request_date=datetime.now(timezone.utc)
        )
        db_session.add_all([request1, request2])
        db_session.commit()

        counts = get_wishlist_counts(db_session)
        # Query counts requests (rows), not unique books
        assert counts.requests == 2
        assert counts.downloaded == 0

    def test_get_wishlist_counts_untrusted_user(
        self,
        db_session: Session,
        untrusted_user: User,
        sample_audiobooks: dict[str, Audiobook],
    ):
        """Untrusted users should also see only their own counts."""
        request1 = AudiobookRequest(
            asin="B001", user_username="untrusted_user", request_date=datetime.now(timezone.utc)
        )
        request2 = AudiobookRequest(
            asin="B002", user_username="other_user", request_date=datetime.now(timezone.utc)
        )
        db_session.add_all([request1, request2])
        db_session.commit()

        counts = get_wishlist_counts(db_session, user=untrusted_user)
        assert counts.requests == 0  # B001 is downloaded
        assert counts.downloaded == 1


class TestWishlistResults:
    """Test get_wishlist_results() function."""

    @pytest.fixture
    def admin_user(self, db_session: Session) -> User:
        """Create an admin user."""
        user = User(
            username="admin",
            password="hashed_password",
            group=GroupEnum.admin,
            root=False,
        )
        db_session.add(user)
        db_session.commit()
        return user

    @pytest.fixture
    def trusted_user(self, db_session: Session) -> User:
        """Create a trusted user."""
        user = User(
            username="trusted_user",
            password="hashed_password",
            group=GroupEnum.trusted,
            root=False,
        )
        db_session.add(user)
        db_session.commit()
        return user

    @pytest.fixture
    def setup_wishlist_data(self, db_session: Session) -> tuple[dict, dict]:
        """Create sample audiobooks and requests for wishlist testing."""
        # Create audiobooks
        books = {
            "book1": Audiobook(
                asin="A001",
                title="Book One",
                subtitle="Subtitle One",
                authors=["Author One"],
                narrators=["Narrator One"],
                cover_image="http://example.com/1.jpg",
                release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=600,
                downloaded=True,
            ),
            "book2": Audiobook(
                asin="A002",
                title="Book Two",
                subtitle="Subtitle Two",
                authors=["Author Two"],
                narrators=["Narrator Two"],
                cover_image="http://example.com/2.jpg",
                release_date=datetime(2021, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=500,
                downloaded=False,
            ),
            "book3": Audiobook(
                asin="A003",
                title="Book Three",
                subtitle="Subtitle Three",
                authors=["Author Three"],
                narrators=["Narrator Three"],
                cover_image="http://example.com/3.jpg",
                release_date=datetime(2022, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=700,
                downloaded=False,
            ),
            "book4": Audiobook(
                asin="A004",
                title="Book Four",
                subtitle="Subtitle Four",
                authors=["Author Four"],
                narrators=["Narrator Four"],
                cover_image="http://example.com/4.jpg",
                release_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=400,
                downloaded=True,
            ),
        }
        for book in books.values():
            db_session.add(book)
        db_session.commit()

        # Create requests
        requests = {
            "req1": AudiobookRequest(
                asin="A001", user_username="trusted_user", request_date=datetime.now(timezone.utc)
            ),
            "req2": AudiobookRequest(
                asin="A002", user_username="trusted_user", request_date=datetime.now(timezone.utc)
            ),
            "req3": AudiobookRequest(
                asin="A002", user_username="other_user", request_date=datetime.now(timezone.utc)
            ),
            "req4": AudiobookRequest(
                asin="A003", user_username="trusted_user", request_date=datetime.now(timezone.utc)
            ),
            "req5": AudiobookRequest(
                asin="A004", user_username="trusted_user", request_date=datetime.now(timezone.utc)
            ),
        }
        for req in requests.values():
            db_session.add(req)
        db_session.commit()

        return books, requests

    def test_get_wishlist_results_empty_database(self, db_session: Session):
        """Empty database should return empty list."""
        results = get_wishlist_results(db_session)
        assert isinstance(results, list)
        assert len(results) == 0

    def test_get_wishlist_results_all_response_type(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """response_type='all' should return all requested books."""
        results = get_wishlist_results(db_session, response_type="all")
        assert len(results) == 4  # A001, A002, A003, A004
        assert all(isinstance(r, AudiobookWishlistResult) for r in results)

        # Check book ASINs are present
        asins = [r.book.asin for r in results]
        assert set(asins) == {"A001", "A002", "A003", "A004"}

    def test_get_wishlist_results_downloaded_filter(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """response_type='downloaded' should return only downloaded books."""
        results = get_wishlist_results(db_session, response_type="downloaded")
        assert len(results) == 2  # A001, A004
        assert all(r.book.downloaded for r in results)

        asins = {r.book.asin for r in results}
        assert asins == {"A001", "A004"}

    def test_get_wishlist_results_not_downloaded_filter(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """response_type='not_downloaded' should return only not downloaded books."""
        results = get_wishlist_results(db_session, response_type="not_downloaded")
        assert len(results) == 2  # A002, A003
        assert all(not r.book.downloaded for r in results)

        asins = {r.book.asin for r in results}
        assert asins == {"A002", "A003"}

    def test_get_wishlist_results_with_specific_user(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """Specifying username should filter to that user's requests."""
        results = get_wishlist_results(
            db_session, username="trusted_user", response_type="all"
        )
        assert len(results) == 4  # trusted_user requested A001, A002, A003, A004

        # All results should be from trusted_user's requests
        for result in results:
            usernames = [req.user_username for req in result.requests]
            assert "trusted_user" in usernames

    def test_get_wishlist_results_user_specific_downloaded(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """Filter by user and downloaded status."""
        results = get_wishlist_results(
            db_session, username="trusted_user", response_type="downloaded"
        )
        assert len(results) == 2  # A001, A004

        asins = {r.book.asin for r in results}
        assert asins == {"A001", "A004"}

    def test_get_wishlist_results_user_specific_not_downloaded(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """Filter by user and not downloaded status."""
        results = get_wishlist_results(
            db_session, username="trusted_user", response_type="not_downloaded"
        )
        assert len(results) == 2  # A002, A003

        asins = {r.book.asin for r in results}
        assert asins == {"A002", "A003"}

    def test_get_wishlist_results_requests_loaded(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """Results should include all requests for each book."""
        results = get_wishlist_results(db_session, response_type="all")

        # Find book A002 which has 2 requests
        book_a002 = next((r for r in results if r.book.asin == "A002"), None)
        assert book_a002 is not None
        assert len(book_a002.requests) == 2

        # Check both requesters
        requesters = {req.user_username for req in book_a002.requests}
        assert requesters == {"trusted_user", "other_user"}

    def test_get_wishlist_results_single_request_per_book(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """Books with single request should have exactly one request loaded."""
        results = get_wishlist_results(db_session, response_type="all")

        # Find book A001 which has 1 request
        book_a001 = next((r for r in results if r.book.asin == "A001"), None)
        assert book_a001 is not None
        assert len(book_a001.requests) == 1
        assert book_a001.requests[0].user_username == "trusted_user"

    def test_get_wishlist_results_other_user_isolated(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """Other user should see books they requested (with all requests loaded)."""
        results = get_wishlist_results(
            db_session, username="other_user", response_type="all"
        )
        assert len(results) == 1  # only A002

        assert results[0].book.asin == "A002"
        # Note: requests includes ALL requests for the book, not just the user's
        assert len(results[0].requests) == 2
        # But we should verify the user's request is in there
        usernames = {req.user_username for req in results[0].requests}
        assert "other_user" in usernames

    def test_get_wishlist_results_no_requests_no_results(self, db_session: Session):
        """Books without any requests should not appear in results."""
        # Create a book but no request for it
        book = Audiobook(
            asin="LONELY",
            title="Lonely Book",
            subtitle=None,
            authors=["Lonely Author"],
            narrators=["Lonely Narrator"],
            cover_image=None,
            release_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=400,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()

        results = get_wishlist_results(db_session)
        assert len(results) == 0

        asins = {r.book.asin for r in results}
        assert "LONELY" not in asins

    def test_get_wishlist_results_invalid_response_type_defaults_to_all(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """Invalid response_type should default to 'all'."""
        # The function uses pattern matching, so "invalid" should fall through to default
        results = get_wishlist_results(
            db_session, response_type="invalid"
        )  # type: ignore
        assert len(results) == 4  # All books

    def test_get_wishlist_results_preserves_book_metadata(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """Results should preserve all book metadata."""
        results = get_wishlist_results(db_session, response_type="all")

        # Check that all book fields are preserved
        book = results[0].book
        assert book.asin is not None
        assert book.title is not None
        assert book.authors is not None
        assert book.narrators is not None
        assert book.release_date is not None
        assert book.runtime_length_min is not None

    def test_get_wishlist_results_wishlist_result_model_structure(
        self, db_session: Session, setup_wishlist_data: tuple
    ):
        """AudiobookWishlistResult should have correct structure."""
        results = get_wishlist_results(db_session, response_type="all")

        assert len(results) > 0
        result = results[0]

        # Check structure
        assert hasattr(result, "book")
        assert hasattr(result, "requests")
        assert isinstance(result.book, Audiobook)
        assert isinstance(result.requests, list)
        assert all(isinstance(r, AudiobookRequest) for r in result.requests)

    def test_get_wishlist_results_multiple_users_multiple_books(
        self, db_session: Session
    ):
        """Complex scenario with multiple users and books."""
        # Create more books
        books = [
            Audiobook(
                asin=f"B{i:03d}",
                title=f"Book {i}",
                subtitle=None,
                authors=[f"Author {i}"],
                narrators=[f"Narrator {i}"],
                cover_image=None,
                release_date=datetime(2020 + i, 1, 1, tzinfo=timezone.utc),
                runtime_length_min=500 + i * 100,
                downloaded=(i % 2 == 0),
            )
            for i in range(5)
        ]
        for book in books:
            db_session.add(book)
        db_session.commit()

        # Create requests from multiple users
        for user_id in range(3):
            for book_id in range(5):
                req = AudiobookRequest(
                    asin=f"B{book_id:03d}",
                    user_username=f"user_{user_id}",
                    request_date=datetime.now(timezone.utc),
                )
                db_session.add(req)
        db_session.commit()

        # Get results for all
        results = get_wishlist_results(db_session, response_type="all")
        assert len(results) == 5

        # Get results for user_0 only
        results = get_wishlist_results(db_session, username="user_0", response_type="all")
        assert len(results) == 5

        # Get only downloaded books
        results = get_wishlist_results(db_session, response_type="downloaded")
        assert len(results) == 3  # B000, B002, B004

        # Get only not downloaded books
        results = get_wishlist_results(db_session, response_type="not_downloaded")
        assert len(results) == 2  # B001, B003


class TestWishlistCountsModel:
    """Test WishlistCounts Pydantic model."""

    def test_wishlist_counts_creation(self):
        """WishlistCounts should be creatable with requests and downloaded fields."""
        counts = WishlistCounts(requests=5, downloaded=3)
        assert counts.requests == 5
        assert counts.downloaded == 3

    def test_wishlist_counts_default_values(self):
        """WishlistCounts should have proper typing."""
        counts = WishlistCounts(requests=0, downloaded=0)
        assert isinstance(counts.requests, int)
        assert isinstance(counts.downloaded, int)

    def test_wishlist_counts_serialization(self):
        """WishlistCounts should serialize to dict."""
        counts = WishlistCounts(requests=5, downloaded=3)
        data = counts.model_dump()
        assert data == {"requests": 5, "downloaded": 3}

    def test_wishlist_counts_validation(self):
        """WishlistCounts should validate positive integers."""
        # Should work with zero
        counts = WishlistCounts(requests=0, downloaded=0)
        assert counts.requests == 0

        # Should work with large numbers
        counts = WishlistCounts(requests=1000, downloaded=999)
        assert counts.requests == 1000
        assert counts.downloaded == 999


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_query_with_null_user_still_works(self, db_session: Session):
        """Passing None as user should work correctly."""
        counts = get_wishlist_counts(db_session, user=None)
        assert isinstance(counts, WishlistCounts)

    def test_query_with_deleted_user_requests_handled(self, db_session: Session):
        """Requests from deleted users should still be queryable."""
        # Create book and request
        book = Audiobook(
            asin="B999",
            title="Test",
            subtitle=None,
            authors=["Author"],
            narrators=["Narrator"],
            cover_image=None,
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()

        # Create request from a user that doesn't exist in User table
        request = AudiobookRequest(
            asin="B999", user_username="ghost_user", request_date=datetime.now(timezone.utc)
        )
        db_session.add(request)
        db_session.commit()

        # Query should still work
        results = get_wishlist_results(db_session)
        assert len(results) == 1

    def test_very_long_book_title_handled(self, db_session: Session):
        """Books with very long titles should be handled."""
        long_title = "A" * 500  # Very long title
        book = Audiobook(
            asin="LONG",
            title=long_title,
            subtitle=None,
            authors=["Author"],
            narrators=["Narrator"],
            cover_image=None,
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()

        request = AudiobookRequest(
            asin="LONG", user_username="user1", request_date=datetime.now(timezone.utc)
        )
        db_session.add(request)
        db_session.commit()

        results = get_wishlist_results(db_session)
        assert len(results) == 1
        assert results[0].book.title == long_title

    def test_special_characters_in_usernames(self, db_session: Session):
        """Usernames with special characters should work."""
        book = Audiobook(
            asin="SPECIAL",
            title="Test",
            subtitle=None,
            authors=["Author"],
            narrators=["Narrator"],
            cover_image=None,
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()

        # Username with special characters
        request = AudiobookRequest(
            asin="SPECIAL", user_username="user-name_123", request_date=datetime.now(timezone.utc)
        )
        db_session.add(request)
        db_session.commit()

        results = get_wishlist_results(db_session, username="user-name_123")
        assert len(results) == 1

    def test_unicode_characters_in_book_metadata(self, db_session: Session):
        """Unicode characters in book metadata should be preserved."""
        book = Audiobook(
            asin="UNICODE",
            title="日本語の本",  # Japanese characters
            subtitle="サブタイトル",
            authors=["著者名"],
            narrators=["ナレーター"],
            cover_image=None,
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()

        request = AudiobookRequest(
            asin="UNICODE", user_username="user1", request_date=datetime.now(timezone.utc)
        )
        db_session.add(request)
        db_session.commit()

        results = get_wishlist_results(db_session)
        assert len(results) == 1
        assert results[0].book.title == "日本語の本"
        assert results[0].book.subtitle == "サブタイトル"

    def test_many_requests_for_same_book(self, db_session: Session):
        """Many requests for the same book should be properly loaded."""
        book = Audiobook(
            asin="POPULAR",
            title="Popular Book",
            subtitle=None,
            authors=["Popular Author"],
            narrators=["Popular Narrator"],
            cover_image=None,
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()

        # Create 50 requests from different users
        for i in range(50):
            request = AudiobookRequest(
                asin="POPULAR",
                user_username=f"user_{i:03d}",
                request_date=datetime.now(timezone.utc),
            )
            db_session.add(request)
        db_session.commit()

        results = get_wishlist_results(db_session)
        assert len(results) == 1
        assert len(results[0].requests) == 50

    def test_empty_author_list(self, db_session: Session):
        """Books with empty author list should be handled."""
        book = Audiobook(
            asin="NOAUTHOR",
            title="No Author Book",
            subtitle=None,
            authors=[],  # Empty list
            narrators=["Narrator"],
            cover_image=None,
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
            downloaded=False,
        )
        db_session.add(book)
        db_session.commit()

        request = AudiobookRequest(
            asin="NOAUTHOR", user_username="user1", request_date=datetime.now(timezone.utc)
        )
        db_session.add(request)
        db_session.commit()

        results = get_wishlist_results(db_session)
        assert len(results) == 1
        assert results[0].book.authors == []
