"""Bounded generation of plausible cross-site listing pairs before scoring."""

from __future__ import annotations

from dataclasses import dataclass

from scrapyrealestate.domain.duplicate_matching import normalize_location_tokens
from scrapyrealestate.persistence.duplicates import (
    DuplicateCandidateRepository,
    DuplicateListingSnapshot,
)


@dataclass(frozen=True, slots=True)
class DuplicateCandidatePair:
    first: DuplicateListingSnapshot
    second: DuplicateListingSnapshot

    @property
    def key(self) -> tuple[int, int]:
        return tuple(sorted((self.first.id, self.second.id)))


@dataclass(frozen=True, slots=True)
class CandidateGenerationResult:
    pairs: tuple[DuplicateCandidatePair, ...]
    sources_considered: int
    truncated_sources: bool


class DuplicateCandidateGenerator:
    """Generate only bounded pairs with location and structural evidence."""

    def __init__(
        self,
        repository: DuplicateCandidateRepository,
        *,
        max_sources: int = 100,
        max_candidates_per_source: int = 50,
    ) -> None:
        if not 1 <= max_sources <= 1_000:
            raise ValueError("max_sources must be between 1 and 1000")
        if not 1 <= max_candidates_per_source <= 500:
            raise ValueError("max_candidates_per_source must be between 1 and 500")
        self.repository = repository
        self.max_sources = max_sources
        self.max_candidates_per_source = max_candidates_per_source

    def generate(self, listing_ids: list[int] | tuple[int, ...]) -> CandidateGenerationResult:
        unique_ids = tuple(dict.fromkeys(listing_ids))
        selected_ids = unique_ids[: self.max_sources]
        pairs: dict[tuple[int, int], DuplicateCandidatePair] = {}
        for listing_id in selected_ids:
            source = self.repository.listing_snapshot(listing_id)
            pool = self.repository.potential_matches(
                listing_id, limit=self.max_candidates_per_source
            )
            for candidate in pool:
                if not _location_matches(source, candidate):
                    continue
                if _known_structural_matches(source, candidate) < 2:
                    continue
                pair = DuplicateCandidatePair(source, candidate)
                pairs.setdefault(pair.key, pair)
        return CandidateGenerationResult(
            pairs=tuple(pairs[key] for key in sorted(pairs)),
            sources_considered=len(selected_ids),
            truncated_sources=len(unique_ids) > len(selected_ids),
        )


def _location_matches(
    first: DuplicateListingSnapshot, second: DuplicateListingSnapshot
) -> bool:
    first_tokens = normalize_location_tokens(first.location)
    second_tokens = normalize_location_tokens(second.location)
    return bool(first_tokens and second_tokens and first_tokens == second_tokens)


def _known_structural_matches(
    first: DuplicateListingSnapshot, second: DuplicateListingSnapshot
) -> int:
    matches = 0
    if (
        first.property_type != "unknown"
        and second.property_type != "unknown"
        and first.property_type == second.property_type
    ):
        matches += 1
    if first.rooms is not None and second.rooms is not None and first.rooms == second.rooms:
        matches += 1
    if first.area_sqm is not None and second.area_sqm is not None:
        matches += 1
    if first.price_euros is not None and second.price_euros is not None:
        matches += 1
    return matches
