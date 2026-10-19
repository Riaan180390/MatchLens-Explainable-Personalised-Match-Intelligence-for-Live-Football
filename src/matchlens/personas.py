"""Stage (e): who is watching, and what they should see."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .schema import Moment

Mode = Literal["casual", "analyst", "player_focus"]

# Moments every viewer gets, whatever their filters.
ALWAYS_SHOW = {"goal", "half_time", "full_time", "penalty_awarded"}


class ViewerProfile(BaseModel):
    viewer_id: str = "default"
    mode: Mode = "casual"
    locale: str = "en"
    favourite_team_id: str | None = None
    favourite_player_ids: list[str] = []
    focus_player_id: str | None = None  # player_focus mode follows this player all match
    focus_metric: str | None = None  # e.g. "top_speed_kmh", "pass_difficulty"

    @property
    def template_persona(self) -> str:
        """Which fast-path wording this viewer gets."""
        return "analyst" if self.mode == "analyst" else "casual"


MIN_SIGNIFICANCE = {"casual": 0.4, "analyst": 0.25, "player_focus": 0.5}


def relevance(moment: Moment, viewer: ViewerProfile) -> float:
    """Significance adjusted for this viewer's club, players and metric."""
    score = moment.significance
    if viewer.favourite_team_id and moment.team_id == viewer.favourite_team_id:
        score += 0.15
    if moment.player_id and moment.player_id in viewer.favourite_player_ids:
        score += 0.3
    if viewer.focus_metric and any(f.name == viewer.focus_metric for f in moment.facts):
        score += 0.3
    if viewer.mode == "player_focus" and viewer.focus_player_id:
        score += 0.6 if moment.player_id == viewer.focus_player_id else -0.2
    return min(score, 1.0)


def should_show(moment: Moment, viewer: ViewerProfile) -> bool:
    if moment.kind in ALWAYS_SHOW:
        return True
    return relevance(moment, viewer) >= MIN_SIGNIFICANCE[viewer.mode]


PRESETS = {
    "casual": ViewerProfile(viewer_id="casual", mode="casual"),
    "analyst": ViewerProfile(viewer_id="analyst", mode="analyst"),
}
