"""
Author relevance ranking and matching utilities for audiobook search results.
"""
import re
from typing import List, Dict, Any, Tuple
from rapidfuzz import fuzz

from app.internal.models import Audiobook


def normalize_author_name(name: str) -> str:
    """
    Normalize author name for matching by:
    - Converting to lowercase
    - Removing common prefixes/suffixes
    - Removing punctuation and extra spaces
    - Handling common name variations
    """
    if not name:
        return ""
    
    # Convert to lowercase
    normalized = name.lower().strip()
    
    # Remove common prefixes and suffixes
    prefixes = ["dr ", "prof ", "mr ", "mrs ", "ms ", "sir ", "lord "]
    suffixes = [" md", " phd", " jr", " sr", " ii", " iii", " iv"]
    
    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
    
    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)].strip()
    
    # Remove punctuation and special characters
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    
    # Remove extra whitespace
    normalized = re.sub(r"\s+", " ", normalized).strip()
    
    return normalized


def extract_surname(name: str) -> str:
    """Extract the last word from an author name as surname."""
    normalized = normalize_author_name(name)
    if not normalized:
        return ""
    parts = normalized.split()
    return parts[-1] if parts else ""


def extract_first_name(name: str) -> str:
    """Extract the first name(s) from an author name (everything except surname)."""
    normalized = normalize_author_name(name)
    if not normalized:
        return ""
    parts = normalized.split()
    return " ".join(parts[:-1]) if len(parts) > 1 else ""


def extract_search_author_components(query: str) -> Tuple[str, str]:
    """
    Extract first and last name components from search query.
    
    Returns:
        Tuple of (first_name, last_name)
    """
    # Clean and split query
    normalized = normalize_author_name(query)
    if not normalized:
        return "", ""
    
    parts = normalized.split()
    if len(parts) == 1:
        # Only one word - treat as surname
        return "", parts[0]
    elif len(parts) >= 2:
        # Multiple words - last is surname, rest is first name
        return " ".join(parts[:-1]), parts[-1]
    
    return "", ""


def calculate_author_match_score(
    book_authors: List[str],
    search_query: str,
    threshold: float = 70.0
) -> Tuple[float, str, str]:
    """
    Calculate how well the book's authors match the search query using semantic matching.
    
    Returns:
        Tuple of (score, match_type, explanation)
        - score: 0-100 match score
        - match_type: "exact", "surname_only", "weak", "none"
        - explanation: human-readable description of the match
    """
    if not book_authors or not search_query:
        return 0.0, "none", "No authors or query"
    
    # Extract search components
    search_first, search_last = extract_search_author_components(search_query)
    
    if not search_last:
        return 0.0, "none", "No surname found in query"
    
    best_score = 0.0
    best_match_type = "none"
    best_explanation = "No match found"
    
    for author in book_authors:
        author_first = extract_first_name(author)
        author_last = extract_surname(author)
        
        if not author_last:
            continue
        
        # Check for exact match (both first and last name match)
        if (search_first and author_first and 
            search_first == author_first and search_last == author_last):
            score = 100.0  # CHANGED FROM 95.0
            match_type = "exact"
            explanation = f"Exact match: '{author}'"
            
            if score > best_score:
                best_score = score
                best_match_type = match_type
                best_explanation = explanation
        
        # Check for surname-only match (same surname, different first name)
        elif search_last == author_last:
            if search_first and author_first and search_first != author_first:
                score = 30.0
                match_type = "surname_only"
                explanation = f"Surname match: '{author_last}' (different first name)"
            elif not search_first or not author_first:
                # One of the names doesn't have a first name
                score = 35.0
                match_type = "surname_only"
                explanation = f"Surname match: '{author_last}'"
            else:
                continue
            
            if score > best_score:
                best_score = score
                best_match_type = match_type
                best_explanation = explanation
        
        # Check for weak partial match (some word overlap)
        else:
            # Check if any words from search appear in author name
            search_words = set(search_query.lower().split())
            author_words = set(normalize_author_name(author).split())
            
            # Remove common stop words
            stop_words = {"the", "a", "an", "of", "and", "or", "in", "on", "at", "to", "for", "with", "by"}
            search_words = {w for w in search_words if w not in stop_words and len(w) > 2}
            author_words = {w for w in author_words if w not in stop_words and len(w) > 2}
            
            overlap = search_words.intersection(author_words)
            if overlap and len(overlap) >= 1:
                score = 10.0
                match_type = "weak"
                explanation = f"Weak match: common words {list(overlap)}"
                
                if score > best_score:
                    best_score = score
                    best_match_type = match_type
                    best_explanation = explanation
    
    return best_score, best_match_type, best_explanation


def calculate_secondary_score(
    book: Audiobook,
    search_query: str
) -> float:
    """
    Calculate secondary scoring based on title relevance and recency.
    Returns score 0-100.
    """
    score = 0.0
    
    # Title similarity (60% weight - INCREASED from 40%)
    title_normalized = book.title.lower()
    query_normalized = search_query.lower()
    
    title_similarity = fuzz.partial_ratio(title_normalized, query_normalized)
    score += (title_similarity * 0.6)
    
    # Recency (40% weight - INCREASED from 30%)
    if book.release_date:
        from datetime import datetime, timezone
        # Make datetime.now() timezone-aware to match book.release_date
        now = datetime.now(timezone.utc)
        # FIXED: Handle both naive and aware datetimes
        if book.release_date.tzinfo is None:
            release_date = book.release_date.replace(tzinfo=timezone.utc)
        else:
            release_date = book.release_date
        
        years_old = (now - release_date).days / 365.25
        if years_old < 1:
            recency_score = 100
        elif years_old < 5:
            recency_score = 80
        elif years_old < 10:
            recency_score = 60
        else:
            recency_score = 40
        score += (recency_score * 0.4)
    
    # REMOVED: Runtime-based "popularity" scoring (was incorrect)
    
    return min(score, 100.0)


def rank_search_results(
    books: List[Audiobook],
    search_query: str,
    author_threshold: float = 70.0,
    enable_secondary_scoring: bool = True
) -> List[Dict[str, Any]]:
    """
    Rank audiobook search results by author relevance and secondary factors.
    
    Args:
        books: List of audiobooks to rank
        search_query: The original search query
        author_threshold: Minimum author match score to be considered "best match"
        enable_secondary_scoring: Whether to apply secondary scoring factors
    
    Returns:
        List of dictionaries containing ranked results with scores and metadata
    """
    if not books or not search_query:
        return []
    
    ranked_results = []
    
    for book in books:
        # Calculate author match score
        author_score, match_type, explanation = calculate_author_match_score(
            book_authors=book.authors,
            search_query=search_query,
            threshold=author_threshold
        )
        
        # Calculate secondary score if enabled
        secondary_score = 0.0
        if enable_secondary_scoring:
            secondary_score = calculate_secondary_score(book, search_query)
        
        # Combine scores (author score is weighted more heavily)
        if enable_secondary_scoring:
            combined_score = (author_score * 0.7) + (secondary_score * 0.3)
        else:
            combined_score = author_score
        
        # Determine if this is a best match
        is_best_match = (
            author_score >= 95 and  # CHANGED FROM 90
            match_type == 'exact' and
            combined_score >= 75  # CHANGED FROM 70
        )
        
        ranked_results.append({
            'book': book,
            'score': combined_score,
            'author_score': author_score,
            'secondary_score': secondary_score,
            'match_type': match_type,
            'explanation': explanation,
            'is_best_match': is_best_match
        })
    
    # Sort by overall score (descending)
    ranked_results.sort(key=lambda x: x['score'], reverse=True)

    return ranked_results


def partition_results_by_score(
    ranked_results: List[Dict[str, Any]],
    author_threshold: float = 95.0
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Partition ranked results into best matches and others.

    Args:
        ranked_results: List of ranked result dictionaries
        author_threshold: Minimum author score to be considered best match

    Returns:
        Tuple of (best_matches, other_matches)
    """
    best_matches: List[Dict[str, Any]] = []
    other_matches: List[Dict[str, Any]] = []

    for result in ranked_results:
        author_score = result.get('author_score', 0)
        match_type = result.get('match_type', 'none')

        if author_score >= author_threshold and match_type == 'exact':
            best_matches.append(result)
        else:
            other_matches.append(result)

    return best_matches, other_matches
