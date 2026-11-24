"""Shared fetching module: per-domain rate limit, conditional cache, SHA-256 archive, log."""

from jg.fetch.archive import Archive, ArchivedFile
from jg.fetch.client import FetchResult, HttpClient

__all__ = ["Archive", "ArchivedFile", "FetchResult", "HttpClient"]
