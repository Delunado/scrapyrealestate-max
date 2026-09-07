"""Shared bounded pagination state for portal spiders."""

from __future__ import annotations

import os
from typing import Any


PAGE_LIMIT_ENV = "SCRAPYREALESTATE_PAGE_LIMIT"
RESULT_LIMIT_ENV = "SCRAPYREALESTATE_RESULT_LIMIT"
DEFAULT_PAGE_LIMIT = 5
DEFAULT_RESULT_LIMIT = 150
MAX_PAGE_LIMIT = 20
MAX_RESULT_LIMIT = 600


def _limit(value: Any, *, default: int, maximum: int, name: str) -> int:
    candidate = default if value in (None, "") else value
    try:
        parsed = int(candidate)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if not 1 <= parsed <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return parsed


class BoundedPaginationMixin:
    """Track pages and unique results without allowing an unbounded crawl.

    Limits can be set with Scrapy spider arguments or the two documented
    environment variables. Spider arguments take precedence.
    """

    def __init__(
        self,
        *args,
        page_limit: int | str | None = None,
        result_limit: int | str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.page_limit = _limit(
            page_limit or os.environ.get(PAGE_LIMIT_ENV),
            default=DEFAULT_PAGE_LIMIT,
            maximum=MAX_PAGE_LIMIT,
            name="page_limit",
        )
        self.result_limit = _limit(
            result_limit or os.environ.get(RESULT_LIMIT_ENV),
            default=DEFAULT_RESULT_LIMIT,
            maximum=MAX_RESULT_LIMIT,
            name="result_limit",
        )
        self._visited_page_urls: set[str] = set()
        self._seen_result_keys: set[str] = set()
        self._result_count = 0

    def _begin_page(self, response) -> bool:
        if response.url in self._visited_page_urls:
            return False
        if len(self._visited_page_urls) >= self.page_limit:
            return False
        self._visited_page_urls.add(response.url)
        return self._result_count < self.result_limit

    def _accept_result(self, key: object) -> bool:
        if self._result_count >= self.result_limit:
            return False
        normalized = str(key).strip()
        if normalized and normalized in self._seen_result_keys:
            return False
        if normalized:
            self._seen_result_keys.add(normalized)
        self._result_count += 1
        return True

    def _can_follow(self, next_url: str | None) -> bool:
        return bool(
            next_url
            and self._result_count < self.result_limit
            and len(self._visited_page_urls) < self.page_limit
            and next_url not in self._visited_page_urls
        )
