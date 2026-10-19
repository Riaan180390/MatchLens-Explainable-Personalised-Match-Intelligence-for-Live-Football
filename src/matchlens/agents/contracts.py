"""Structured outputs the agents must return, and the messages that flow between them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from ..schema import Moment


class Insight(BaseModel):
    """What the analyst (or recap) agent writes: the why, not just the what."""

    headline: str = Field(description="At most 8 words.")
    why_it_matters: str = Field(description="One or two sentences explaining the significance, citing the facts.")
    angle: Literal["tactical", "momentum", "individual", "milestone", "drama", "recap"] = "drama"
    cited_facts: list[str] = Field(default_factory=list, description="Names of the facts used.")
    player_of_the_match: str | None = Field(default=None, description="Recaps only.")


class Variant(BaseModel):
    persona: Literal["casual", "analyst", "player_focus"]
    locale: str = Field(description="BCP-47 language code, e.g. en, es, af.")
    title: str = Field(description="At most 5 words.")
    body: str = Field(description="casual: at most 20 words; analyst/player_focus: at most 30 words.")


class VariantBundle(BaseModel):
    variants: list[Variant]


# ---- workflow messages ---------------------------------------------------------


@dataclass
class MomentJob:
    moment: Moment
    context: dict
    targets: list[tuple[str, str]]  # (persona, locale)


@dataclass
class Draft:
    author: str  # "analyst" | "recap"
    insight: Insight
    attempt: int


@dataclass
class Revise:
    author: str
    previous: Insight
    issues: list[str]
    attempt: int


@dataclass
class Verified:
    insight: Insight
    attempts: int


@dataclass
class Variants:
    insight: Insight
    variants: list[Variant]
    attempts: int


@dataclass
class Failed:
    stage: str
    reason: str
    attempts: int = 0


@dataclass
class Outcome:
    moment_id: str
    status: Literal["published", "fallback"]
    insight: Insight | None = None
    variants: list[Variant] = field(default_factory=list)
    rejected: list[tuple[Variant, list[str]]] = field(default_factory=list)
    attempts: int = 0
    issues: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
