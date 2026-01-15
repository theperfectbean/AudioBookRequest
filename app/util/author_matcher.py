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
    """Extract the first word from an author name as first name."""
    normalized = normalize_author_name(name)
    if not normalized:
        return ""
    parts = normalized.split()
    return parts[0] if len(parts) > 1 else ""


def get_author_variants(author_name: str) -> List[str]:
    """
    Generate variants of an author name for flexible matching.
    Returns list of normalized variants.
    """
    normalized = normalize_author_name(author_name)
    variants = [normalized]
    
    # Extract surname
    surname = extract_surname(author_name)
    if surname and surname not in variants:
        variants.append(surname)
    
    # Extract first name + surname
    first_name = extract_first_name(author_name)
    if first_name and surname:
        first_last = f"{first_name} {surname}"
        if first_last not in variants:
            variants.append(first_last)
    
    # Reverse (surname, first name) format
    if first_name and surname:
        reverse = f"{surname} {first_name}"
        if reverse not in variants:
            variants.append(reverse)
    
    # Initials format (e.g., "J. K. Rowling" -> "jk rowling")
    initials = "".join([part[0] for part in normalized.split() if part])
    if initials and initials not in variants:
        variants.append(initials)
    
    # First name + initial of surname
    if first_name and surname:
        first_initial = surname[0]
        first_plus_initial = f"{first_name} {first_initial}"
        if first_plus_initial not in variants:
            variants.append(first_plus_initial)
    
    return variants


def calculate_author_match_score(
    book_authors: List[str],
    search_query: str,
    threshold: float = 70.0
) -> Tuple[float, str, str]:
    """
    Calculate how well the book's authors match the search query.
    
    Returns:
        Tuple of (score, match_type, explanation)
        - score: 0-100 match score
        - match_type: "exact", "partial", "surname", "nickname", "none"
        - explanation: human-readable description of the match
    """
    if not book_authors or not search_query:
        return 0.0, "none", "No authors or query"
    
    # Normalize search query for author extraction
    # Assume query might contain author name, extract potential author parts
    query_parts = search_query.lower().split()
    
    # Filter out common stop words that might be in title
    stop_words = {"the", "a", "an", "of", "and", "or", "in", "on", "at", "to", "for", "with", "by"}
    potential_author_terms = [part for part in query_parts if part not in stop_words or len(part) > 2]
    
    if not potential_author_terms:
        potential_author_terms = query_parts
    
    best_score = 0.0
    best_match_type = "none"
    best_explanation = "No match found"
    
    for author in book_authors:
        author_variants = get_author_variants(author)
        
        # Try exact matching with variants
        for variant in author_variants:
            # Jaro-Winkler similarity for fuzzy matching
            similarity = fuzz.ratio(variant, " ".join(potential_author_terms))
            
            # Also try partial matching
            partial_similarity = fuzz.partial_ratio(variant, " ".join(potential_author_terms))
            
            # Take the better of full or partial match
            match_score = max(similarity, partial_similarity)
            
            if match_score > best_score:
                best_score = match_score
                surname = extract_surname(author)
                query_surname = extract_surname(" ".join(potential_author_terms))
                
                # Determine match type
                if match_score >= 95:
                    best_match_type = "exact"
                    best_explanation = f"Exact match: '{author}'"
                elif match_score >= threshold:
                    if surname and query_surname and surname == query_surname:
                        best_match_type = "surname"
                        best_explanation = f"Surname match: '{surname}'"
                    else:
                        best_match_type = "partial"
                        best_explanation = f"Partial match: '{author}' (score: {match_score:.1f})"
                elif match_score >= threshold * 0.7:
                    if surname and query_surname and surname == query_surname:
                        best_match_type = "surname"
                        best_explanation = f"Weak surname match: '{surname}'"
                    else:
                        best_match_type = "nickname"
                        best_explanation = f"Possible nickname/variant: '{author}'"
    
    return best_score, best_match_type, best_explanation


def calculate_secondary_score(
    book: Audiobook,
    search_query: str
) -> float:
    """
    Calculate secondary scoring based on title relevance, recency, and popularity.
    Returns score 0-100.
    """
    score = 0.0
    
    # Title similarity (40% weight)
    title_normalized = book.title.lower()
    query_normalized = search_query.lower()
    
    title_similarity = fuzz.partial_ratio(title_normalized, query_normalized)
    score += (title_similarity * 0.4)
    
    # Recency (30% weight) - newer books get slight boost
    if book.release_date:
        from datetime import datetime, timezone
        # Make datetime.now() timezone-aware to match book.release_date
        now = datetime.now(timezone.utc)
        # Ensure book.release_date is also timezone-aware
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
        score += (recency_score * 0.3)
    
    # Popularity based on runtime (30% weight) - longer books often more popular
    if book.runtime_length_min:
        if book.runtime_length_min > 600:
            popularity_score = 100
        elif book.runtime_length_min > 300:
            popularity_score = 80
        elif book.runtime_length_min > 120:
            popularity_score = 60
        else:
            popularity_score = 40
        score += (popularity_score * 0.3)
    
    return min(score, 100.0)


def partition_results_by_score(
    ranked_results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Partition ranked results into best matches and other matches.
    
    Best matches are those with:
    - Author score >= threshold
    - Match type of "exact" or "partial"
    - Overall score >= 70
    
    Returns:
        Tuple of (best_matches, other_matches)
    """
    best_matches = []
    other_matches = []
    
    for result in ranked_results:
        score = result['score']
        author_score = result['author_score']
        match_type = result['match_type']
        
        # Determine if this is a best match
        is_best = (
            author_score >= 70 and
            match_type in ['exact', 'partial'] and
            score >= 70
        )
        
        if is_best:
            best_matches.append(result)
        else:
            other_matches.append(result)
    
    return best_matches, other_matches


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
            author_score >= author_threshold and
            match_type in ['exact', 'partial', 'surname'] and
            combined_score >= 65
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
