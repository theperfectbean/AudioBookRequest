import json
import re
from typing import Literal, Protocol

from rapidfuzz import fuzz, utils
from sqlmodel import Session

from app.internal.models import Indexer, ProwlarrSource
from app.util.cache import SimpleCache, StringConfigCache
from app.util.log import logger


class ProwlarrMisconfigured(ValueError):
    pass


ProwlarrConfigKey = Literal[
    "prowlarr_api_key",
    "prowlarr_base_url",
    "prowlarr_source_ttl",
    "prowlarr_categories",
    "prowlarr_indexers",
]


class ProwlarrConfig(StringConfigCache[ProwlarrConfigKey]):
    def raise_if_invalid(self, session: Session):
        if not self.get_base_url(session):
            raise ProwlarrMisconfigured("Prowlarr base url not set")
        if not self.get_api_key(session):
            raise ProwlarrMisconfigured("Prowlarr base url not set")

    def is_valid(self, session: Session) -> bool:
        return (
            self.get_base_url(session) is not None
            and self.get_api_key(session) is not None
        )

    def get_api_key(self, session: Session) -> str | None:
        return self.get(session, "prowlarr_api_key")

    def set_api_key(self, session: Session, api_key: str):
        self.set(session, "prowlarr_api_key", api_key)

    def get_base_url(self, session: Session) -> str | None:
        path = self.get(session, "prowlarr_base_url")
        if path:
            return path.rstrip("/")
        return None

    def set_base_url(self, session: Session, base_url: str):
        self.set(session, "prowlarr_base_url", base_url)

    def get_source_ttl(self, session: Session) -> int:
        return self.get_int(session, "prowlarr_source_ttl", 24 * 60 * 60)

    def set_source_ttl(self, session: Session, source_ttl: int):
        self.set_int(session, "prowlarr_source_ttl", source_ttl)

    def get_categories(self, session: Session) -> list[int]:
        categories = self.get(session, "prowlarr_categories")
        if categories is None:
            return [3030]
        return json.loads(categories)  # pyright: ignore[reportAny]

    def set_categories(self, session: Session, categories: list[int]):
        self.set(session, "prowlarr_categories", json.dumps(categories))

    def get_indexers(self, session: Session) -> list[int]:
        indexers = self.get(session, "prowlarr_indexers")
        if indexers is None:
            return []
        return json.loads(indexers)  # pyright: ignore[reportAny]

    def set_indexers(self, session: Session, indexers: list[int]):
        self.set(session, "prowlarr_indexers", json.dumps(indexers))


prowlarr_config = ProwlarrConfig()
prowlarr_source_cache = SimpleCache[list[ProwlarrSource], str]()
prowlarr_indexer_cache = SimpleCache[Indexer, str]()


def flush_prowlarr_cache():
    logger.info("Flushing prowlarr caches")
    prowlarr_source_cache.flush()
    prowlarr_indexer_cache.flush()


def normalize_text(text: str | None, primary_only: bool = False) -> str:
    if not text:
        return ""
    text_str: str = text  # Type narrowing
    if primary_only:
        # Extract text before common subtitle/metadata delimiters
        text_str = re.split(r"[:\(\[—]", text_str)[0]
    # Apply rapidfuzz normalization (removes extra spaces, punctuation, etc.)
    normalized = str(utils.default_process(text_str))
    # Collapse multiple spaces into single space
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


class ProwlarrSearchResultProtocol(Protocol):
    title: str
    author: str


class AudiobookProtocol(Protocol):
    title: str
    authors: list[str]


def verify_match(
    p_result: ProwlarrSearchResultProtocol,
    a_result: AudiobookProtocol,
    search_query: str | None = None,
) -> bool:
    # 1. Normalize
    p_title_norm = normalize_text(p_result.title)
    p_author_norm = normalize_text(p_result.author)

    a_title_norm = normalize_text(a_result.title)
    a_authors_norm = normalize_text(" ".join(a_result.authors))

    logger.info(
        f"VERIFY_MATCH START | "
        f"Prowlarr: '{p_result.title}' by '{p_result.author}' | "
        f"Audible: '{a_result.title}' by {a_result.authors} | "
        f"Search Query: '{search_query}'"
    )

    # Tier 0: Fast path - exact match
    if p_title_norm == a_title_norm and p_author_norm == a_authors_norm:
        logger.info(
            f"✅ ACCEPTED | "
            f"P: '{p_result.title}' | "
            f"A: '{a_result.title}' by {a_result.authors} | "
            f"Scores: title=100.0, author=100.0"
        )
        return True

    # 2. Score Title
    # Pass 1: Try primary titles (before colons/brackets)
    p_title_primary = normalize_text(p_result.title, primary_only=True)
    a_title_primary = normalize_text(a_result.title, primary_only=True)
    title_score = fuzz.token_set_ratio(p_title_primary, a_title_primary)

    # Pass 2: If primary titles don't match well, try full titles
    if title_score < 85:
        title_score = max(
            title_score,
            fuzz.token_set_ratio(p_title_norm, a_title_norm),
        )

    # Handle Short Titles
    if len(p_title_norm) < 10:
        title_match = title_score >= 95
    else:
        title_match = title_score >= 85

    # 3. Score Author with adaptive scoring
    author_match = False
    author_score = 0.0

    # Empty/Unknown Author Fallback
    if not p_author_norm or len(p_author_norm) < 3 or p_author_norm.lower() == "unknown":
        # FIXED: Only check title match, don't try to verify against search query
        # This prevents false negatives when users search by title-only
        author_match = True
        author_score = 100.0 if title_score >= 95 else 0.0
    else:
        # Known author - do fuzzy matching
        author_tokens = p_author_norm.split()
        if len(author_tokens) >= 2:
            # For multi-token author names ("FirstName LastName"), use stricter fuzz.ratio
            author_score = fuzz.ratio(p_author_norm, a_authors_norm)
            author_threshold = 85
        else:
            # For single tokens ("Sanderson"), use more permissive token_set_ratio
            author_score = fuzz.token_set_ratio(p_author_norm, a_authors_norm)
            author_threshold = 80

        author_match = author_score >= author_threshold

    # Determine final match
    is_match = title_match and author_match

    # REMOVED: Search intent check that caused false negatives
    # Users often search by title only, so we shouldn't reject based on author-vs-query
    if is_match:
        logger.info(
            f"✅ ACCEPTED | "
            f"P: '{p_result.title}' | "
            f"A: '{a_result.title}' by {a_result.authors} | "
            f"Scores: title={title_score:.1f}, author={author_score:.1f}"
        )
        return True
    else:
        logger.warning(
            f"❌ REJECTED | "
            f"P: '{p_result.title}' | "
            f"A: '{a_result.title}' by {a_result.authors} | "
            f"Reason: {'title mismatch' if not title_match else 'author mismatch'} | "
            f"Scores: title={title_score:.1f}, author={author_score:.1f}"
        )
        return False


def verify_match_relaxed(
    p_result: ProwlarrSearchResultProtocol,
    a_result: AudiobookProtocol,
    search_query: str | None = None,
) -> bool:
    """
    Relaxed version of verify_match with lower thresholds.
    Used as fallback when strict matching fails.
    """
    # Normalize
    p_title_norm = normalize_text(p_result.title)
    p_author_norm = normalize_text(p_result.author)
    a_title_norm = normalize_text(a_result.title)
    a_authors_norm = normalize_text(" ".join(a_result.authors))

    # Exact match (fast path)
    if p_title_norm == a_title_norm and p_author_norm == a_authors_norm:
        return True

    # Score title with relaxed threshold
    p_title_primary = normalize_text(p_result.title, primary_only=True)
    a_title_primary = normalize_text(a_result.title, primary_only=True)
    title_score = fuzz.token_set_ratio(p_title_primary, a_title_primary)

    if title_score < 75:  # Relaxed from 85
        title_score = max(
            title_score,
            fuzz.token_set_ratio(p_title_norm, a_title_norm),
        )

    # Relaxed thresholds
    if len(p_title_norm) < 10:
        title_match = title_score >= 90  # Relaxed from 95
    else:
        title_match = title_score >= 75  # Relaxed from 85

    # Score author with relaxed threshold
    author_match = False
    author_score = 0.0

    if not p_author_norm or len(p_author_norm) < 3 or p_author_norm.lower() == "unknown":
        author_match = True
        author_score = 100.0 if title_score >= 90 else 0.0
    else:
        author_tokens = p_author_norm.split()
        if len(author_tokens) >= 2:
            author_score = fuzz.ratio(p_author_norm, a_authors_norm)
            author_threshold = 75  # Relaxed from 85
        else:
            author_score = fuzz.token_set_ratio(p_author_norm, a_authors_norm)
            author_threshold = 70  # Relaxed from 80
        
        author_match = author_score >= author_threshold

    is_match = title_match and author_match

    if is_match:
        logger.info(
            f"✅ RELAXED MATCH ACCEPTED | "
            f"P: '{p_result.title}' | "
            f"A: '{a_result.title}' by {a_result.authors} | "
            f"Scores: title={title_score:.1f}, author={author_score:.1f}"
        )
        return True
    else:
        logger.debug(
            f"❌ RELAXED MATCH REJECTED | "
            f"P: '{p_result.title}' | "
            f"A: '{a_result.title}' by {a_result.authors} | "
            f"Scores: title={title_score:.1f}, author={author_score:.1f}"
        )
        return False

