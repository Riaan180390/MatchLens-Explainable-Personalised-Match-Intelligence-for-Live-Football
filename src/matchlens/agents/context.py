"""What an agent is allowed to know about a moment, captured when the moment is detected."""

from __future__ import annotations

from ..schema import Moment
from ..stats import MatchState

RECAP_KINDS = {"half_time", "full_time"}


def build_context(moment: Moment, state: MatchState, recent: list[Moment]) -> dict:
    """A compact, JSON-able view of the match for the agents (and the fact-checker's ground truth)."""
    s = state
    home, away = s.teams[s.home_id], s.teams[s.away_id]
    teams = {}
    for t in (home, away):
        summary = s.team_summary(t.team_id)
        teams[t.name] = {
            "short_name": t.short_name,
            "goals": t.goals,
            "possession_pct": summary["possession_pct"],
            "xg": summary["xg"],
            "shots": t.shots,
            "shots_on_target": t.shots_on_target,
            "pass_accuracy_pct": round(100 * (summary["pass_accuracy"] or 0)),
            "players_on_pitch": sum(1 for p in s.players.values() if p.team_id == t.team_id and p.on_pitch),
        }
    rhythm = s.rhythm()
    ctx: dict = {
        "minute": moment.minute_label,
        "score": f"{home.name} {home.goals}-{away.goals} {away.name}",
        "teams": teams,
        "momentum": {
            "value": round(s.momentum, 2),
            "meaning": f"positive favours {home.name}, negative favours {away.name}",
        },
        "rhythm": {"label": rhythm["label"], "chaos_index": rhythm["chaos"]},
        "moment": {
            "kind": moment.kind,
            "headline": moment.headline,
            "team": s.teams[moment.team_id].name if moment.team_id in s.teams else None,
            "player": s.player_name(moment.player_id) if moment.player_id else None,
            "facts": [f.model_dump(exclude_none=True) for f in moment.facts],
        },
        "recent_moments": [f"{m.minute_label} {m.headline}" for m in recent[-3:]],
    }
    if moment.player_id in s.players:
        card = s.player_card(moment.player_id)
        ctx["player_card"] = {k: card[k] for k in (
            "name", "position", "goals", "assists", "shots", "passes_cmp", "pass_accuracy",
            "top_speed_kmh", "distance_km", "xg") if card.get(k) is not None}
        if ctx["player_card"].get("pass_accuracy") is not None:
            ctx["player_card"]["pass_accuracy_pct"] = round(100 * ctx["player_card"].pop("pass_accuracy"))
    if moment.kind in RECAP_KINDS:
        leaders = s.leaders()
        ctx["leaders"] = {k: {"name": v["name"], "value": v["value"]} for k, v in leaders.items()}
        ctx["key_moments"] = [f"{m.minute_label} {m.headline}" for m in
                              sorted(recent, key=lambda m: -m.significance)[:10]]
    return ctx


def roster_names(state: MatchState) -> dict[str, str]:
    """Surname -> full name for every player in the match."""
    return {p.short_name: p.name for p in state.players.values()}
