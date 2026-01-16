"""
Metadata enrichment module for ABR.

Provides Google Books API integration to enrich virtual/fallback books
and manual requests with cover images, descriptions, and other metadata.
"""

from .google_books import GoogleBooksProvider
from app.internal.models import MetadataCache

__all__ = ["GoogleBooksProvider", "MetadataCache"]
