"""
Pytest configuration and fixtures for ABR-Dev test suite.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncGenerator, Generator
from unittest.mock import Mock, AsyncMock, patch

import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses
from sqlmodel import SQLModel, create_engine, Session

from app.internal.models import Audiobook, ProwlarrSource, TorrentSource, User, GroupEnum
from app.internal.prowlarr.search_integration import ProwlarrSearchResult


# Database fixtures
@pytest.fixture(scope="function")
def db_engine():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="function")
def db_session(db_engine) -> Generator[Session, None, None]:
    """Provide a database session for tests."""
    with Session(db_engine) as session:
        yield session
        session.rollback()


# Async event loop fixture for async tests
@pytest.fixture(scope="function")
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# Async HTTP mocking fixtures
@pytest.fixture(scope="function")
async def mock_client_session() -> AsyncGenerator[ClientSession, None]:
    """Provide a real ClientSession with aioresponses mocking for HTTP calls."""
    with aioresponses() as mocked:
        async with ClientSession() as session:
            # Attach mocked responses to session for easy access in tests
            session._mocked = mocked
            yield session


@pytest.fixture(scope="function")
def aioresponses_mocker() -> Generator[aioresponses, None, None]:
    """Provide aioresponses context manager for manual HTTP mocking."""
    with aioresponses() as mocked:
        yield mocked


# Sample data fixtures
@pytest.fixture
def sample_prowlarr_results():
    """Sample Prowlarr search results for testing."""
    return [
        ProwlarrSearchResult(
            guid="torrent1",
            indexer_id=5,
            indexer="TestIndexer",
            title="The Way of Kings",
            author="Brandon Sanderson",
            narrator="Unknown",
            size=1024000000,
            publish_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            seeders=10,
            leechers=2,
            info_url="http://example.com/torrent1",
            freeleech=False,
            protocol="torrent",
            magnet_url=None,
            download_url=None,
        ),
        ProwlarrSearchResult(
            guid="torrent2",
            indexer_id=8,
            indexer="AnotherIndexer",
            title="Mistborn: The Final Empire",
            author="Brandon Sanderson",
            narrator="Unknown",
            size=512000000,
            publish_date=datetime(2019, 1, 1, tzinfo=timezone.utc),
            seeders=5,
            leechers=1,
            info_url="http://example.com/torrent2",
            freeleech=True,
            protocol="torrent",
            magnet_url=None,
            download_url=None,
        ),
        ProwlarrSearchResult(
            guid="torrent3",
            indexer_id=3,
            indexer="UnknownIndexer",
            title="Unknown Author Book",
            author="Unknown",
            narrator="Unknown",
            size=750000000,
            publish_date=datetime(2021, 1, 1, tzinfo=timezone.utc),
            seeders=3,
            leechers=0,
            info_url="http://example.com/torrent3",
            freeleech=False,
            protocol="torrent",
            magnet_url=None,
            download_url=None,
        ),
        ProwlarrSearchResult(
            guid="torrent4",
            indexer_id=2,
            indexer="CollisionIndexer",
            title="Some Book",
            author="Robert Wright",
            narrator="Unknown",
            size=800000000,
            publish_date=datetime(2020, 6, 1, tzinfo=timezone.utc),
            seeders=8,
            leechers=3,
            info_url="http://example.com/torrent4",
            freeleech=False,
            protocol="torrent",
            magnet_url=None,
            download_url=None,
        ),
    ]


@pytest.fixture
def sample_audible_books():
    """Sample Audible book results for testing."""
    return [
        Audiobook(
            asin="B08G9PRS1K",
            title="The Way of Kings",
            subtitle="The Stormlight Archive, Book 1",
            authors=["Brandon Sanderson"],
            narrators=["Michael Kramer", "Kate Reading"],
            cover_image="https://example.com/cover1.jpg",
            release_date=datetime(2010, 8, 1, tzinfo=timezone.utc),
            runtime_length_min=1440,
        ),
        Audiobook(
            asin="B006Q9FL8Y",
            title="Mistborn: The Final Empire",
            subtitle="Mistborn Saga, Book 1",
            authors=["Brandon Sanderson"],
            narrators=["Michael Kramer"],
            cover_image="https://example.com/cover2.jpg",
            release_date=datetime(2006, 7, 1, tzinfo=timezone.utc),
            runtime_length_min=960,
        ),
        Audiobook(
            asin="B07XYZ1234",
            title="Robert Wright's Guide to Life",
            subtitle="A philosophical journey",
            authors=["Robert Wright"],
            narrators=["Robert Wright"],
            cover_image="https://example.com/cover3.jpg",
            release_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=600,
        ),
        Audiobook(
            asin="B07ABC5678",
            title="Robert Salas: My Story",
            subtitle="UFO encounters",
            authors=["Robert Salas"],
            narrators=["Robert Salas"],
            cover_image="https://example.com/cover4.jpg",
            release_date=datetime(2019, 1, 1, tzinfo=timezone.utc),
            runtime_length_min=500,
        ),
    ]


@pytest.fixture
def mock_audible_books_response(sample_audible_books):
    """Mock response for list_audible_books function."""
    async def mock_list_audible_books(*args, **kwargs):
        return sample_audible_books
    
    return mock_list_audible_books


@pytest.fixture
def mock_prowlarr_search_response(sample_prowlarr_results):
    """Mock response for search_prowlarr_available function."""
    async def mock_search_prowlarr_available(*args, **kwargs):
        return sample_prowlarr_results
    
    return mock_search_prowlarr_available


# Async test helper
def async_test(coro):
    """Decorator to run async tests."""
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro(*args, **kwargs))
        finally:
            loop.close()
    return wrapper


# Test data for edge cases
@pytest.fixture
def edge_case_prowlarr_results():
    """Edge case Prowlarr results for testing."""
    return [
        # Special characters
        ProwlarrSearchResult(
            guid="special1",
            indexer_id=1,
            indexer="Test",
            title="Book: With [Special] Characters—And More",
            author="J. R. R. Tolkien",
            narrator="Unknown",
            size=1000000,
            publish_date=datetime.now(timezone.utc),
            seeders=5,
            leechers=0,
            info_url="http://example.com/special",
            freeleech=False,
            protocol="torrent",
        ),
        # Very long title
        ProwlarrSearchResult(
            guid="long1",
            indexer_id=1,
            indexer="Test",
            title="A" * 200,  # 200 character title
            author="Some Author",
            narrator="Unknown",
            size=1000000,
            publish_date=datetime.now(timezone.utc),
            seeders=5,
            leechers=0,
            info_url="http://example.com/long",
            freeleech=False,
            protocol="torrent",
        ),
        # Empty author
        ProwlarrSearchResult(
            guid="empty1",
            indexer_id=1,
            indexer="Test",
            title="Book Without Author",
            author="",
            narrator="Unknown",
            size=1000000,
            publish_date=datetime.now(timezone.utc),
            seeders=5,
            leechers=0,
            info_url="http://example.com/empty",
            freeleech=False,
            protocol="torrent",
        ),
        # Multiple authors (comma-separated)
        ProwlarrSearchResult(
            guid="multi1",
            indexer_id=1,
            indexer="Test",
            title="Collaborative Work",
            author="Author One, Author Two",
            narrator="Unknown",
            size=1000000,
            publish_date=datetime.now(timezone.utc),
            seeders=5,
            leechers=0,
            info_url="http://example.com/multi",
            freeleech=False,
            protocol="torrent",
        ),
        # Non-ASCII characters
        ProwlarrSearchResult(
            guid="unicode1",
            indexer_id=1,
            indexer="Test",
            title="日本語の本",
            author="著者名",
            narrator="Unknown",
            size=1000000,
            publish_date=datetime.now(timezone.utc),
            seeders=5,
            leechers=0,
            info_url="http://example.com/unicode",
            freeleech=False,
            protocol="torrent",
        ),
    ]


@pytest.fixture
def mock_google_books_response():
    """Mock Google Books API response."""
    return {
        "items": [
            {
                "volumeInfo": {
                    "title": "The Way of Kings",
                    "subtitle": "The Stormlight Archive, Book 1",
                    "authors": ["Brandon Sanderson"],
                    "description": "A fantasy epic about knights and magic.",
                    "categories": ["Fiction", "Fantasy"],
                    "imageLinks": {
                        "extraLarge": "https://example.com/cover.jpg",
                        "large": "https://example.com/cover-large.jpg",
                    },
                    "publishedDate": "2010",
                    "pageCount": 1000,
                    "averageRating": 4.8,
                    "ratingsCount": 5000,
                    "industryIdentifiers": [
                        {"type": "ISBN_13", "identifier": "9780765326355"},
                        {"type": "ISBN_10", "identifier": "0765326353"},
                    ],
                }
            }
        ],
        "totalItems": 1,
    }


@pytest.fixture
def mock_google_books_empty_response():
    """Mock Google Books API empty response."""
    return {"items": [], "totalItems": 0}


# User fixtures for authorization testing
@pytest.fixture(scope="function")
def admin_user(db_session) -> User:
    """Create an admin user."""
    user = User(username="admin", password="hashed_password", group=GroupEnum.admin)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture(scope="function")
def trusted_user(db_session) -> User:
    """Create a trusted user."""
    user = User(username="trusted", password="hashed_password", group=GroupEnum.trusted)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture(scope="function")
def untrusted_user(db_session) -> User:
    """Create an untrusted user."""
    user = User(username="untrusted", password="hashed_password", group=GroupEnum.untrusted)
    db_session.add(user)
    db_session.commit()
    return user
