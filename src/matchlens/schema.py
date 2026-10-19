"""Data contracts shared by every stage of the pipeline.

Coordinates are in metres on a 105 x 68 pitch, always in the *acting* team's
frame: that team attacks towards x = 105, and y = 0 is its right touchline.
`to_absolute()` converts to a fixed broadcast frame for rendering.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0

Point = tuple[float, float]


class EventType(StrEnum):
    MATCH_START = "match_start"
    KICK_OFF = "kick_off"
    PASS = "pass"
    CARRY = "carry"
    DRIBBLE = "dribble"
    SHOT = "shot"
    PRESSURE = "pressure"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    BALL_RECOVERY = "ball_recovery"
    POSSESSION_CHANGE = "possession_change"
    FOUL = "foul"
    CARD = "card"
    SET_PIECE = "set_piece"
    SPRINT = "sprint"
    PHYSICAL = "physical"
    SUBSTITUTION = "substitution"
    PERIOD_END = "period_end"
    MATCH_END = "match_end"


class PlayerInfo(BaseModel):
    player_id: str
    name: str
    short_name: str
    number: int
    position: str


class TeamInfo(BaseModel):
    team_id: str
    name: str
    short_name: str
    primary_color: str
    secondary_color: str
    formation: str
    starting_xi: list[PlayerInfo]
    bench: list[PlayerInfo]


class MatchMeta(BaseModel):
    match_id: str
    competition: str
    venue: str
    kickoff_utc: str
    referee: str
    weather: str
    seed: int
    home: TeamInfo
    away: TeamInfo


class Event(BaseModel):
    """One synthetic match event, as it would arrive from a live feed."""

    match_id: str
    seq: int
    type: EventType
    period: int
    clock_s: float = Field(description="Match clock in seconds; period 2 starts at 2700.")
    elapsed_s: float = Field(description="Seconds of play since kick-off; monotonic, used for replay pacing.")
    minute_label: str
    team_id: str | None = None
    player_id: str | None = None
    possession_id: int | None = None
    possession_team_id: str | None = None
    location: Point | None = None
    end_location: Point | None = None
    outcome: str | None = None
    subtype: str | None = None
    recipient_id: str | None = None
    related_player_id: str | None = None
    under_pressure: bool = False
    nearest_opponent_m: float | None = None
    opponents_in_lane: int | None = None
    ball_speed_kmh: float | None = None
    player_speed_kmh: float | None = None
    body_part: str | None = None
    data: dict[str, Any] | None = None


class Fact(BaseModel):
    """A computed, verifiable number that backs up a narrative claim."""

    name: str
    label: str
    value: float | int | str
    unit: str = ""
    context: str | None = None


class Moment(BaseModel):
    """A detected moment worth telling the viewer about, with its evidence."""

    moment_id: str
    match_id: str
    seq: int
    elapsed_s: float
    minute_label: str
    kind: str
    team_id: str | None = None
    player_id: str | None = None
    significance: float = Field(ge=0, le=1)
    headline: str
    facts: list[Fact] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


OverlayTemplate = Literal[
    "score_bug", "player_tag", "moment_card", "stat_bar", "momentum_strip", "lower_third", "recap_card"
]
OverlayRegion = Literal["top_left", "top_right", "lower_third", "player_anchor", "full"]


class OverlayCue(BaseModel):
    """A timed, machine-readable instruction for a graphics renderer.

    `start_s` is in the same timebase as `Event.elapsed_s`, so cues can be
    synchronised with the feed by whatever renders them.
    """

    cue_id: str
    match_id: str
    moment_id: str | None = None
    template: OverlayTemplate
    region: OverlayRegion
    start_s: float
    duration_s: float
    priority: int = Field(ge=0, le=100)
    persona: str = "all"
    locale: str = "en"
    source: Literal["fast_path", "agent"] = "fast_path"
    replaces_cue_id: str | None = Field(default=None, description="Cue this one pre-empts in the same region.")
    slots: dict[str, Any] = Field(default_factory=dict)


def to_absolute(point: Point, team_is_home: bool, period: int) -> Point:
    """Convert an acting-team-frame point to a fixed frame (home attacks right in period 1)."""
    attacks_right = team_is_home == (period == 1)
    x, y = point
    return (x, y) if attacks_right else (PITCH_LENGTH - x, PITCH_WIDTH - y)
