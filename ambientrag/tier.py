"""Tier definitions for AmbientRAG infrastructure."""
from __future__ import annotations

from typing import NamedTuple


class TierInfo(NamedTuple):
    name: str
    db_type: str
    description: str
    performance: str


TIERS: dict[int, TierInfo] = {
    0: TierInfo(
        name="PGLite",
        db_type="pglite",
        description="WASM in-process, ~2-3s queries. Zero external deps. Good for: trying it out, Show HN demo.",
        performance="~2-3s queries",
    ),
    1: TierInfo(
        name="Brew Postgres",
        db_type="postgresql",
        description="Real server, ~200-500ms queries. brew install postgresql@17. Good for: daily use, personal vault.",
        performance="~200-500ms queries",
    ),
    2: TierInfo(
        name="Docker",
        db_type="docker-postgresql",
        description="Postgres + reranker, ~100-200ms queries. docker compose up. Good for: power users, multi-vault.",
        performance="~100-200ms queries",
    ),
    3: TierInfo(
        name="Full",
        db_type="docker-postgresql",
        description="Local embeddings + eGPU, ~50-100ms queries. Maximum performance. Good for: production Mac Mini setup.",
        performance="~50-100ms queries",
    ),
}


def get_tier_info(tier: int) -> TierInfo | None:
    return TIERS.get(tier)


def validate_tier(tier: int) -> bool:
    return tier in TIERS


def can_upgrade(from_tier: int, to_tier: int) -> bool:
    return validate_tier(from_tier) and validate_tier(to_tier) and to_tier > from_tier
