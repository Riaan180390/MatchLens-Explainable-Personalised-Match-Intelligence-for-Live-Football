"""Stage (d), fast path: deterministic overlay cues that go out within milliseconds.

Agent-written cues (source="agent") arrive a few seconds later and can replace
these; if an agent is slow or fails, these templated cues are what viewers see.
"""

from __future__ import annotations

from typing import Any

from .schema import Fact, Moment, OverlayCue
from .stats import MatchState

REGION = {
    "score_bug": "top_left", "player_tag": "player_anchor", "moment_card": "lower_third",
    "lower_third": "lower_third", "stat_bar": "top_right", "momentum_strip": "top_right",
    "recap_card": "full",
}
TEMPLATE_BY_KIND = {
    "goal": "moment_card", "great_save": "moment_card", "big_chance": "moment_card",
    "penalty_awarded": "moment_card", "shot_speed": "player_tag", "elite_pass": "player_tag",
    "speed_alert": "player_tag", "distance_milestone": "player_tag", "milestone": "player_tag",
    "pass_streak": "stat_bar", "pressure_spell": "stat_bar", "rhythm_shift": "stat_bar",
    "momentum_swing": "momentum_strip", "card": "lower_third", "substitution": "lower_third",
    "half_time": "recap_card", "full_time": "recap_card",
}
PRIORITY = {
    "goal": 100, "penalty_awarded": 95, "full_time": 90, "half_time": 90, "card": 80,
    "great_save": 75, "big_chance": 70, "momentum_swing": 60, "pressure_spell": 55,
    "rhythm_shift": 50, "speed_alert": 45, "shot_speed": 45, "elite_pass": 40,
    "pass_streak": 40, "substitution": 35, "distance_milestone": 30, "milestone": 25,
}


def template_for(kind: str) -> str:
    return TEMPLATE_BY_KIND.get(kind, "lower_third")


def _facts(m: Moment) -> dict[str, Fact]:
    return {f.name: f for f in m.facts}


def _v(f: dict[str, Fact], name: str, default: Any = "") -> Any:
    return f[name].value if name in f else default


def _metrics(m: Moment, names: list[str]) -> list[dict]:
    f = _facts(m)
    return [{"label": f[n].label, "value": f[n].value, "unit": f[n].unit} for n in names if n in f]


def _texts(m: Moment, state: MatchState) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Return ({persona: (title, body)}, analyst metric names) for a moment."""
    f = _facts(m)
    p = state.players.get(m.player_id or "")
    name = p.name if p else ""
    short = p.short_name if p else ""
    team = state.teams.get(m.team_id or "")
    tname = team.name if team else ""
    k = m.kind

    if k == "goal":
        assist = f" Set up by {_v(f, 'assist')}." if "assist" in f else ""
        swing = {"equaliser": "That's the equaliser!", "go-ahead goal": f"{tname} are in front!",
                 "extends the lead": f"{tname} extend the lead!", "pulls one back": f"{tname} pull one back!"}
        return {
            "casual": ("GOAL!", f"{name} scores! {swing.get(_v(f, 'game_state'), '')}{assist} {_v(f, 'score')}"),
            "analyst": (f"GOAL - {name}",
                        f"xG {_v(f, 'xg')} from {_v(f, 'shot_distance_m')} m at {_v(f, 'shot_speed_kmh', '?')} km/h. "
                        f"{_v(f, 'passes_in_move', 0)} passes in the move. {_v(f, 'score')}"),
        }, ["xg", "shot_distance_m", "shot_speed_kmh", "passes_in_move"]
    if k == "great_save":
        return {
            "casual": ("What a save!", f"{name} keeps it out."),
            "analyst": (f"Save - {name}", f"Denied a {_v(f, 'xg')} xG chance from {_v(f, 'shot_distance_m')} m."),
        }, ["xg", "shot_distance_m", "shot_speed_kmh"]
    if k == "big_chance":
        return {
            "casual": ("So close!", f"{name} should have scored there."),
            "analyst": (f"Big chance - {name}", f"{_v(f, 'xg')} xG from {_v(f, 'shot_distance_m')} m, not converted."),
        }, ["xg", "shot_distance_m"]
    if k == "shot_speed":
        return {
            "casual": (short, f"Rocket! {_v(f, 'shot_speed_kmh')} km/h"),
            "analyst": (short, f"{_v(f, 'shot_speed_kmh')} km/h from {_v(f, 'shot_distance_m')} m (xG {_v(f, 'xg')})"),
        }, ["shot_speed_kmh", "shot_distance_m", "xg"]
    if k == "elite_pass":
        lane = _v(f, "opponents_in_lane", 0)
        past = f"Threaded past {lane} defender{'s' if lane != 1 else ''}" if lane else "Perfectly weighted"
        return {
            "casual": (short, f"Brilliant pass! {past}."),
            "analyst": (short, f"Pass difficulty {_v(f, 'pass_difficulty')}/10 | {_v(f, 'pass_length_m')} m | "
                               f"{_v(f, 'xpass')}% expected completion"),
        }, ["pass_difficulty", "pass_length_m", "opponents_in_lane", "xpass"]
    if k == "speed_alert":
        record = " - fastest in the match" if "match_record" in f else ""
        return {
            "casual": (short, f"Flying! {_v(f, 'top_speed_kmh')} km/h{record}"),
            "analyst": (short, f"Top speed {_v(f, 'top_speed_kmh')} km/h{record}"),
        }, ["top_speed_kmh", "sprint_distance_m"]
    if k == "distance_milestone":
        return {
            "casual": (short, f"{_v(f, 'distance_km')} km covered already!"),
            "analyst": (short, f"Distance covered {_v(f, 'distance_km')} km"),
        }, ["distance_km"]
    if k == "milestone":
        return {
            "casual": (short, f"{_v(f, 'passes_completed')} passes completed"),
            "analyst": (short, f"{_v(f, 'passes_completed')} completed | {_v(f, 'pass_accuracy')}% accuracy"),
        }, ["passes_completed", "pass_accuracy"]
    if k == "pass_streak":
        return {
            "casual": (tname, f"Keep-ball! {_v(f, 'passes_in_move')} passes in a row."),
            "analyst": (tname, f"{_v(f, 'passes_in_move')}-pass sequence | possession {_v(f, 'possession_pct')}%"),
        }, ["passes_in_move", "possession_pct"]
    if k == "momentum_swing":
        return {
            "casual": (tname, "On top now."),
            "analyst": (tname, f"{_v(f, 'shots_5m')} shots, {_v(f, 'xg_5m')} xG in the last 5 min"
                               + (f" | field tilt {_v(f, 'field_tilt_5m')}%" if "field_tilt_5m" in f else "")),
        }, ["momentum", "shots_5m", "xg_5m", "field_tilt_5m"]
    if k == "pressure_spell":
        opp = state.teams[state.opponent(m.team_id)].name if m.team_id else ""
        return {
            "casual": (tname, f"Camped in {opp}'s half."),
            "analyst": (tname, f"Field tilt {_v(f, 'field_tilt_5m')}% (5 min)"
                               + (f" | PPDA {_v(f, 'ppda_10m')}" if "ppda_10m" in f else "")),
        }, ["field_tilt_5m", "final_third_touches_5m", "ppda_10m"]
    if k == "rhythm_shift":
        label = m.headline.rsplit(" ", 1)[-1]
        casual = {"controlled": "Things have calmed down.", "chaotic": "It's end to end now!",
                  "contested": "It's a real battle in midfield."}.get(label, m.headline)
        if label == "controlled" and tname:
            casual = f"Things have calmed down - {tname} are in control."
        return {
            "casual": ("Game rhythm", casual),
            "analyst": ("Game rhythm", f"Chaos index {_v(f, 'chaos_index')} | {_v(f, 'possession_changes_per_min')} "
                                       f"turnovers/min | {_v(f, 'pass_completion_5m')}% passing"),
        }, ["chaos_index", "possession_changes_per_min", "pass_completion_5m", "duels_per_min"]
    if k == "card":
        card = str(_v(f, "card")).capitalize()
        return {
            "casual": (f"{card} card", name),
            "analyst": (f"{card} card", f"{name} | {tname} fouls: {_v(f, 'team_fouls')}"),
        }, ["team_fouls"]
    if k == "penalty_awarded":
        return {
            "casual": ("PENALTY!", f"Penalty to {tname}!"),
            "analyst": ("Penalty", f"{tname} | taker: {_v(f, 'taker')} | xG 0.78"),
        }, []
    if k == "substitution":
        return {
            "casual": ("Substitution", f"ON {_v(f, 'player_on')} | OFF {_v(f, 'player_off')}"),
            "analyst": ("Substitution", f"ON {_v(f, 'player_on')} | OFF {_v(f, 'player_off')} "
                                        f"({_v(f, 'off_distance_km', '?')} km covered)"),
        }, ["off_distance_km"]
    if k in ("half_time", "full_time"):
        return {
            "casual": (m.headline, ""),
            "analyst": (m.headline, ""),
        }, [x.name for x in m.facts]
    return {"casual": (m.headline, ""), "analyst": (m.headline, "")}, []


DURATION = {"moment_card": 10.0, "player_tag": 5.0, "stat_bar": 7.0, "momentum_strip": 8.0,
            "lower_third": 6.0, "recap_card": 20.0, "score_bug": 3600.0}


def cues_for_moment(m: Moment, state: MatchState, personas: tuple[str, ...] = ("casual", "analyst"),
                    locale: str = "en") -> list[OverlayCue]:
    template = template_for(m.kind)
    texts, analyst_metrics = _texts(m, state)
    p = state.players.get(m.player_id or "")
    team = state.teams.get(m.team_id or "")
    base_slots: dict[str, Any] = {
        "kind": m.kind, "minute": m.minute_label,
        "team_id": m.team_id, "team_name": team.name if team else None,
        "team_color": team.color if team else None,
        "player_id": m.player_id, "player_name": p.name if p else None, "number": p.number if p else None,
    }
    if template == "momentum_strip":
        base_slots["momentum"] = round(state.momentum, 3)
        base_slots["history"] = state.momentum_history[-20:]
    cues = []
    for persona in personas:
        title, body = texts[persona]
        slots = dict(base_slots, title=title, body=body)
        if persona == "analyst":
            slots["metrics"] = _metrics(m, analyst_metrics)
        elif template == "recap_card":
            slots["metrics"] = _metrics(m, [x.name for x in m.facts if x.name.endswith(("_shots", "_possession"))])
        cues.append(OverlayCue(
            cue_id=f"{m.moment_id}-{persona}", match_id=m.match_id, moment_id=m.moment_id,
            template=template, region=REGION[template], start_s=m.elapsed_s,
            duration_s=DURATION[template], priority=PRIORITY.get(m.kind, 20),
            persona=persona, locale=locale, slots=slots,
        ))
    if m.kind == "goal":
        cues.append(score_bug(m, state))
    return cues


def score_bug(m: Moment, state: MatchState) -> OverlayCue:
    h, a = state.teams[state.home_id], state.teams[state.away_id]
    return OverlayCue(
        cue_id=f"{m.moment_id}-score", match_id=m.match_id, moment_id=m.moment_id,
        template="score_bug", region="top_left", start_s=m.elapsed_s, duration_s=DURATION["score_bug"],
        priority=100, persona="all",
        slots={"home": h.short_name, "away": a.short_name, "home_goals": h.goals, "away_goals": a.goals,
               "home_color": h.color, "away_color": a.color, "minute": m.minute_label},
    )


class CueScheduler:
    """One cue per screen region per persona: higher priority pre-empts, lower waits briefly or is dropped."""

    def __init__(self, max_delay_s: float = 6.0):
        self.max_delay_s = max_delay_s
        self._showing: dict[tuple[str, str], OverlayCue] = {}

    def place(self, cue: OverlayCue) -> OverlayCue | None:
        key = (cue.persona, cue.region)
        cur = self._showing.get(key)
        if cur and cur.start_s + cur.duration_s > cue.start_s and cue.template != "score_bug":
            if cue.priority > cur.priority:
                cue = cue.model_copy(update={"replaces_cue_id": cur.cue_id})
            else:
                delay = cur.start_s + cur.duration_s - cue.start_s
                if delay > self.max_delay_s:
                    return None
                cue = cue.model_copy(update={"start_s": round(cue.start_s + delay, 2)})
        self._showing[key] = cue
        return cue
