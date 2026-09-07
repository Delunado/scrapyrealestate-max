"""Bounded generation of plausible cross-site listing pairs before scoring."""

from __future__ import annotations

from dataclasses import dataclass

from scrapyrealestate.domain.duplicate_matching import (
    areas_similar,
    bedrooms_similar,
    normalize_address_tokens,
    normalize_location_tokens,
    normalize_spanish_tokens,
    normalize_title_tokens,
    prices_similar,
    relative_difference,
    title_similarity,
)
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


@dataclass(frozen=True, slots=True)
class DuplicateCandidateScore:
    score: float
    eligible: bool
    confidence: str
    reasons: tuple[dict[str, object], ...]


class DuplicateCandidateScorer:
    """Apply fixed weights and precision-first gates to a generated pair."""

    threshold = 0.68

    def score(self, pair: DuplicateCandidatePair) -> DuplicateCandidateScore:
        first, second = pair.first, pair.second
        reasons: list[dict[str, object]] = []
        hard_conflicts: list[str] = []
        total = 0.0

        first_location = normalize_location_tokens(first.location)
        second_location = normalize_location_tokens(second.location)
        location_match = bool(first_location and first_location == second_location)
        total += _record(reasons, "location", location_match, 0.10)
        if not location_match:
            hard_conflicts.append("location_conflict")

        address_match, address_conflict = _address_evidence(first, second)
        total += _record(reasons, "exact_address", address_match, 0.30)
        if address_conflict:
            hard_conflicts.append("address_conflict")

        neighbourhood_match = _token_fields_equal(
            first.neighbourhood, second.neighbourhood
        )
        total += _record(reasons, "neighbourhood", neighbourhood_match, 0.05)

        property_match = _known_equal(
            first.property_type, second.property_type, unknown="unknown"
        )
        total += _record(reasons, "property_type", property_match, 0.05)
        if property_match is False:
            hard_conflicts.append("property_type_conflict")

        room_match = bedrooms_similar(first.rooms, second.rooms)
        total += _record(reasons, "bedrooms", room_match, 0.10)
        if room_match is False:
            hard_conflicts.append("bedroom_conflict")

        area_match = areas_similar(first.area_sqm, second.area_sqm)
        total += _record(
            reasons,
            "area",
            area_match,
            0.15,
            _difference_detail(first.area_sqm, second.area_sqm),
        )
        if area_match is False:
            hard_conflicts.append("area_conflict")

        price_match = prices_similar(first.price_euros, second.price_euros)
        total += _record(
            reasons,
            "price",
            price_match,
            0.15,
            _difference_detail(first.price_euros, second.price_euros),
        )
        if price_match is False:
            hard_conflicts.append("price_conflict")

        title_score = title_similarity(first.title, second.title)
        distinctive_title = _distinctive_title_match(first.title, second.title, title_score)
        title_contribution = 0.0 if title_score is None else 0.10 * title_score
        total += title_contribution
        reasons.append(
            {
                "code": "title",
                "matched": distinctive_title,
                "contribution": round(title_contribution, 4),
                "similarity": None if title_score is None else round(title_score, 4),
            }
        )

        complete_structure = all(
            evidence is True for evidence in (room_match, area_match, price_match)
        )
        identity_gate = address_match or (
            neighbourhood_match is True and distinctive_title and complete_structure
        )
        final_score = round(min(total, 1.0), 4)
        eligible = not hard_conflicts and identity_gate and final_score >= self.threshold
        if hard_conflicts:
            reasons.append(
                {
                    "code": "hard_conflict",
                    "matched": False,
                    "contribution": 0.0,
                    "details": tuple(hard_conflicts),
                }
            )
        if not identity_gate:
            reasons.append(
                {
                    "code": "identity_gate",
                    "matched": False,
                    "contribution": 0.0,
                    "detail": "exact address or strong address-less evidence required",
                }
            )
        confidence = "high" if eligible and final_score >= 0.85 else "review"
        return DuplicateCandidateScore(
            score=final_score,
            eligible=eligible,
            confidence=confidence,
            reasons=tuple(reasons),
        )


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


def _record(
    reasons: list[dict[str, object]],
    code: str,
    matched: bool | None,
    weight: float,
    detail: str | None = None,
) -> float:
    contribution = weight if matched is True else 0.0
    reason: dict[str, object] = {
        "code": code,
        "matched": matched,
        "contribution": contribution,
    }
    if detail is not None:
        reason["detail"] = detail
    reasons.append(reason)
    return contribution


def _address_evidence(
    first: DuplicateListingSnapshot, second: DuplicateListingSnapshot
) -> tuple[bool, bool]:
    first_street = _street_content_tokens(first.street)
    second_street = _street_content_tokens(second.street)
    first_number = normalize_spanish_tokens(first.street_number)
    second_number = normalize_spanish_tokens(second.street_number)
    if not first_street or not second_street or not first_number or not second_number:
        return False, False
    street_match = first_street == second_street
    number_match = first_number == second_number
    return street_match and number_match, not (street_match and number_match)


def _street_content_tokens(value: str | None) -> frozenset[str]:
    return normalize_address_tokens(value) - {"de", "del", "el", "la", "las", "los"}


def _token_fields_equal(first: str | None, second: str | None) -> bool | None:
    first_tokens = normalize_location_tokens(first)
    second_tokens = normalize_location_tokens(second)
    if not first_tokens or not second_tokens:
        return None
    return first_tokens == second_tokens


def _known_equal(first: str, second: str, *, unknown: str) -> bool | None:
    if first == unknown or second == unknown:
        return None
    return first == second


def _distinctive_title_match(
    first: str, second: str, similarity: float | None
) -> bool:
    return (
        similarity is not None
        and similarity >= 0.8
        and len(normalize_title_tokens(first)) >= 3
        and len(normalize_title_tokens(second)) >= 3
    )


def _difference_detail(
    first: float | int | None, second: float | int | None
) -> str | None:
    if first is None or second is None:
        return None
    return f"{relative_difference(first, second):.1%} relative difference"
