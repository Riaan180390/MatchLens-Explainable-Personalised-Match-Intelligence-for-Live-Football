"""Stage (b): turn the raw event stream into football statistics and live match state.

Everything here is deterministic and cheap enough to run on every event. The
agents read this state instead of raw events, and the fact-checker verifies
their claims against it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field

from . import geometry as geo
from .schema import Event, EventType as T, MatchMeta

WINDOW_S = 300.0  # rolling window for rhythm, field tilt and momentum context
PPDA_WINDOW_S = 600.0
MOMENTUM_HALF_LIFE_S = 180.0
TOUCH_TYPES = {T.PASS, T.CARRY, T.DRIBBLE, T.SHOT}
BIG_CHANCE_XG = 0.3


@dataclass
class EventInsight:
    """Metrics derived from a single event."""

    event: Event
    pass_length_m: float | None = None
    pass_difficulty: float | None = None
    xpass: float | None = None
    progress_m: float | None = None
    threat_added: float | None = None
    xg: float | None = None
    shot_distance_m: float | None = None
    possession_passes: int = 0
    assist_player_id: str | None = None
    score_before: dict[str, int] | None = None


@dataclass
class PlayerStats:
    player_id: str
    name: str
    short_name: str
    number: int
    position: str
    team_id: str
    on_pitch: bool = False
    touches: int = 0
    passes_att: int = 0
    passes_cmp: int = 0
    pass_length_sum: float = 0.0
    passes_over_expected: float = 0.0
    hardest_pass: float = 0.0
    progressive_passes: int = 0
    key_passes: int = 0
    assists: int = 0
    shots: int = 0
    shots_on_target: int = 0
    goals: int = 0
    xg: float = 0.0
    fastest_shot_kmh: float = 0.0
    tackles_won: int = 0
    interceptions: int = 0
    recoveries: int = 0
    pressures: int = 0
    dribbles_att: int = 0
    dribbles_won: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    saves: int = 0
    sprints: int = 0
    top_speed_kmh: float = 0.0
    distance_m: float = 0.0
    threat_added: float = 0.0

    @property
    def pass_accuracy(self) -> float | None:
        return self.passes_cmp / self.passes_att if self.passes_att else None


@dataclass
class TeamStats:
    team_id: str
    name: str
    short_name: str
    color: str
    is_home: bool
    goals: int = 0
    shots: int = 0
    shots_on_target: int = 0
    xg: float = 0.0
    big_chances: int = 0
    passes_att: int = 0
    passes_cmp: int = 0
    pass_length_sum: float = 0.0
    progressive_passes: int = 0
    final_third_entries: int = 0
    touches: int = 0
    final_third_touches: int = 0
    box_touches: int = 0
    tackles_won: int = 0
    interceptions: int = 0
    pressures: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    corners: int = 0
    possession_s: float = 0.0
    recoveries: int = 0
    sprints: int = 0
    distance_m: float = 0.0


@dataclass
class _Possession:
    team_id: str | None = None
    started_s: float = 0.0
    completed_passes: list[Event] = field(default_factory=list)


class MatchState:
    def __init__(self, meta: MatchMeta | None = None):
        self.meta: MatchMeta | None = None
        self.teams: dict[str, TeamStats] = {}
        self.players: dict[str, PlayerStats] = {}
        self.home_id = self.away_id = ""
        self.elapsed_s = 0.0
        self.period = 1
        self.minute_label = "0'"
        self.finished = False
        self._last: Event | None = None
        self._window: deque[tuple[float, str, str, float]] = deque()
        self._threat = {}  # decayed attacking threat per team, drives momentum
        self.momentum_history: list[tuple[float, float]] = []
        self._poss = _Possession()
        if meta:
            self._init_meta(meta)

    # ----------------------------------------------------------------- setup

    def _init_meta(self, meta: MatchMeta) -> None:
        self.meta = meta
        self.home_id, self.away_id = meta.home.team_id, meta.away.team_id
        for team, is_home in ((meta.home, True), (meta.away, False)):
            self.teams[team.team_id] = TeamStats(team.team_id, team.name, team.short_name,
                                                 team.primary_color, is_home)
            self._threat[team.team_id] = 0.0
            for plist, starting in ((team.starting_xi, True), (team.bench, False)):
                for p in plist:
                    self.players[p.player_id] = PlayerStats(
                        p.player_id, p.name, p.short_name, p.number, p.position, team.team_id, on_pitch=starting)

    def opponent(self, team_id: str) -> str:
        return self.away_id if team_id == self.home_id else self.home_id

    def player_name(self, player_id: str | None) -> str:
        p = self.players.get(player_id or "")
        return p.name if p else "Unknown"

    @property
    def score(self) -> dict[str, int]:
        return {tid: t.goals for tid, t in self.teams.items()}

    # ----------------------------------------------------------------- apply

    def apply(self, e: Event) -> EventInsight:
        if e.type == T.MATCH_START and self.meta is None and e.data:
            self._init_meta(MatchMeta.model_validate(e.data))
        ins = EventInsight(event=e)
        self._tick(e)

        team = self.teams.get(e.team_id or "")
        player = self.players.get(e.player_id or "")
        if e.type in TOUCH_TYPES and team and player:
            team.touches += 1
            player.touches += 1
            if e.location and geo.in_final_third(e.location):
                team.final_third_touches += 1
                self._push(e.elapsed_s, team.team_id, "ft_touch")
            if e.location and geo.in_box(e.location):
                team.box_touches += 1
            self._push(e.elapsed_s, team.team_id, "touch")

        handler = getattr(self, f"_on_{e.type.value}", None)
        if handler:
            handler(e, ins, team, player)
        self._last = e
        return ins

    def _tick(self, e: Event) -> None:
        """Advance clocks: possession time, decay of momentum, window pruning."""
        if self._last is not None:
            dt = max(0.0, e.elapsed_s - self._last.elapsed_s)
            pt = self._last.possession_team_id
            if pt in self.teams and dt > 0:
                self.teams[pt].possession_s += dt
                self._push(e.elapsed_s, pt, "poss_s", dt)
            if dt > 0:
                decay = 0.5 ** (dt / MOMENTUM_HALF_LIFE_S)
                for k in self._threat:
                    self._threat[k] *= decay
        self.elapsed_s, self.period, self.minute_label = e.elapsed_s, e.period, e.minute_label
        if not self.momentum_history or e.elapsed_s - self.momentum_history[-1][0] >= 30:
            self.momentum_history.append((e.elapsed_s, round(self.momentum, 3)))
        while self._window and e.elapsed_s - self._window[0][0] > PPDA_WINDOW_S:
            self._window.popleft()
        if e.possession_id is not None and e.possession_team_id and (
                self._last is None or e.possession_id != self._last.possession_id):
            self._poss = _Possession(e.possession_team_id, e.elapsed_s)

    def _push(self, t: float, team_id: str, kind: str, value: float = 1.0) -> None:
        self._window.append((t, team_id, kind, value))

    def _add_threat(self, team_id: str, value: float) -> None:
        if value > 0 and team_id in self._threat:
            self._threat[team_id] += value

    # -------------------------------------------------------------- handlers

    def _on_pass(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if not (team and p and e.location and e.end_location):
            return
        length = geo.dist(e.location, e.end_location)
        progress = e.end_location[0] - e.location[0]
        difficulty = geo.pass_difficulty(length, e.opponents_in_lane or 0, e.under_pressure,
                                         e.nearest_opponent_m if e.nearest_opponent_m is not None else 10.0,
                                         progress, e.subtype)
        xpass = geo.expected_pass_completion(difficulty)
        complete = e.outcome == "complete"
        ins.pass_length_m, ins.progress_m = round(length, 1), round(progress, 1)
        ins.pass_difficulty, ins.xpass = difficulty, round(xpass, 3)

        team.passes_att += 1
        p.passes_att += 1
        p.passes_over_expected += (1.0 if complete else 0.0) - xpass
        self._push(e.elapsed_s, team.team_id, "pass_att")
        if e.location[0] < 63:
            self._push(e.elapsed_s, team.team_id, "pass_own60")
        if not complete:
            return
        team.passes_cmp += 1
        team.pass_length_sum += length
        p.passes_cmp += 1
        p.pass_length_sum += length
        p.hardest_pass = max(p.hardest_pass, difficulty)
        self._push(e.elapsed_s, team.team_id, "pass_cmp")
        if (progress >= 10 and e.end_location[0] >= 52.5
                and geo.goal_distance(e.end_location) <= 0.75 * geo.goal_distance(e.location)):
            team.progressive_passes += 1
            p.progressive_passes += 1
        if e.location[0] < 70 <= e.end_location[0]:
            team.final_third_entries += 1
        added = geo.threat(e.end_location) - geo.threat(e.location)
        ins.threat_added = round(added, 4)
        p.threat_added += added
        self._add_threat(team.team_id, added)
        self._poss.completed_passes.append(e)
        ins.possession_passes = len(self._poss.completed_passes)

    def _on_carry(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if team and p and e.location and e.end_location:
            added = geo.threat(e.end_location) - geo.threat(e.location)
            ins.threat_added, ins.progress_m = round(added, 4), round(e.end_location[0] - e.location[0], 1)
            p.threat_added += added
            self._add_threat(team.team_id, added)
            if e.location[0] < 70 <= e.end_location[0]:
                team.final_third_entries += 1

    def _on_dribble(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if not (team and p):
            return
        p.dribbles_att += 1
        self._push(e.elapsed_s, team.team_id, "duel")
        if e.outcome == "complete":
            p.dribbles_won += 1
            if e.location and e.end_location:
                added = geo.threat(e.end_location) - geo.threat(e.location)
                p.threat_added += added
                self._add_threat(team.team_id, added)

    def _on_shot(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if not (team and p and e.location):
            return
        if e.subtype == "penalty":
            q = 0.78
        elif e.subtype == "free_kick":
            q = 0.06
        else:
            q = geo.xg(e.location, header=e.body_part == "head", under_pressure=e.under_pressure)
        ins.xg = round(q, 3)
        ins.shot_distance_m = round(geo.goal_distance(e.location), 1)
        ins.score_before = dict(self.score)
        ins.possession_passes = len(self._poss.completed_passes)
        team.shots += 1
        team.xg += q
        p.shots += 1
        p.xg += q
        if e.ball_speed_kmh:
            p.fastest_shot_kmh = max(p.fastest_shot_kmh, e.ball_speed_kmh)
        if q >= BIG_CHANCE_XG:
            team.big_chances += 1
        self._push(e.elapsed_s, team.team_id, "shot")
        self._push(e.elapsed_s, team.team_id, "xg", q)
        self._add_threat(team.team_id, q)

        last_pass = self._poss.completed_passes[-1] if self._poss.completed_passes else None
        if last_pass and last_pass.recipient_id == p.player_id and last_pass.player_id in self.players:
            ins.assist_player_id = last_pass.player_id
            self.players[last_pass.player_id].key_passes += 1
        if e.outcome in ("goal", "saved"):
            team.shots_on_target += 1
            p.shots_on_target += 1
        if e.outcome == "saved" and e.related_player_id in self.players:
            self.players[e.related_player_id].saves += 1
        if e.outcome == "goal":
            team.goals += 1
            p.goals += 1
            if ins.assist_player_id:
                self.players[ins.assist_player_id].assists += 1

    def _on_pressure(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if team and p:
            team.pressures += 1
            p.pressures += 1

    def _defensive_action(self, e: Event, team: TeamStats) -> None:
        self._push(e.elapsed_s, team.team_id, "duel")
        if e.location and e.location[0] > 42:  # inside the opponent's 60%: counts towards PPDA
            self._push(e.elapsed_s, team.team_id, "def_high")

    def _on_tackle(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if team and p:
            team.tackles_won += 1
            p.tackles_won += 1
            self._defensive_action(e, team)

    def _on_interception(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if team and p:
            if e.subtype != "clearance":
                team.interceptions += 1
                p.interceptions += 1
            self._defensive_action(e, team)

    def _on_ball_recovery(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if team and p:
            p.recoveries += 1

    def _on_foul(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if team and p:
            team.fouls += 1
            p.fouls += 1
            self._defensive_action(e, team)

    def _on_card(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if not (team and p):
            return
        if e.subtype == "yellow":
            team.yellow_cards += 1
            p.yellow_cards += 1
        else:
            team.red_cards += 1
            p.red_cards += 1
            p.on_pitch = False

    def _on_possession_change(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if team:
            self._push(e.elapsed_s, team.team_id, "poss_change")
            if (e.data or {}).get("reason") in ("interception", "recovery", "tackle", "blocked_shot", "clearance", "save"):
                team.recoveries += 1

    def _on_set_piece(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if team and e.subtype == "corner":
            team.corners += 1

    def _on_sprint(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if team and p and e.player_speed_kmh:
            team.sprints += 1
            p.sprints += 1
            p.top_speed_kmh = max(p.top_speed_kmh, e.player_speed_kmh)

    def _on_physical(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        for pid, v in ((e.data or {}).get("players") or {}).items():
            if pid in self.players:
                self.players[pid].distance_m = v["distance_m"]
                self.players[pid].top_speed_kmh = max(self.players[pid].top_speed_kmh, v["top_speed_kmh"])
        for tid, t in self.teams.items():
            t.distance_m = sum(pl.distance_m for pl in self.players.values() if pl.team_id == tid)

    def _on_substitution(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        if p:
            p.on_pitch = False
        if e.related_player_id in self.players:
            self.players[e.related_player_id].on_pitch = True

    def _on_match_end(self, e: Event, ins: EventInsight, team: TeamStats | None, p: PlayerStats | None) -> None:
        self.finished = True

    # -------------------------------------------------------- derived views

    def window(self, team_id: str, kind: str, seconds: float = WINDOW_S) -> float:
        return sum(v for t, tid, k, v in self._window
                   if tid == team_id and k == kind and self.elapsed_s - t <= seconds)

    @property
    def momentum(self) -> float:
        """-1..1 from the home side's perspective, from decayed attacking threat."""
        h, a = self._threat.get(self.home_id, 0.0), self._threat.get(self.away_id, 0.0)
        return (h - a) / (h + a + 0.05)

    def field_tilt(self, team_id: str, seconds: float = WINDOW_S) -> float | None:
        mine = self.window(team_id, "ft_touch", seconds)
        total = mine + self.window(self.opponent(team_id), "ft_touch", seconds)
        return mine / total if total >= 6 else None

    def ppda(self, team_id: str, seconds: float = PPDA_WINDOW_S) -> float | None:
        """Opponent passes in their own 60% per defensive action by team_id there; lower = more intense press."""
        actions = self.window(team_id, "def_high", seconds)
        opp_passes = self.window(self.opponent(team_id), "pass_own60", seconds)
        return round(opp_passes / actions, 1) if actions else None

    def rhythm(self, seconds: float = WINDOW_S) -> dict:
        """Control vs chaos: how often the ball changes hands, how clean passing is, how many duels."""
        span = max(60.0, min(seconds, self.elapsed_s))
        mins = span / 60
        changes = sum(self.window(t, "poss_change", span) for t in self.teams)
        att = sum(self.window(t, "pass_att", span) for t in self.teams)
        cmp_ = sum(self.window(t, "pass_cmp", span) for t in self.teams)
        duels = sum(self.window(t, "duel", span) for t in self.teams)
        completion = cmp_ / att if att else 1.0
        cpm_n = geo.clamp((changes / mins - 1.2) / 2.8, 0, 1)
        inc_n = geo.clamp(((1 - completion) - 0.08) / 0.27, 0, 1)
        duel_n = geo.clamp((duels / mins - 0.5) / 2.5, 0, 1)
        chaos = round(100 * (0.5 * cpm_n + 0.3 * inc_n + 0.2 * duel_n))
        label = "Controlled" if chaos < 35 else ("Contested" if chaos < 65 else "Chaotic")
        poss = {t: self.window(t, "poss_s", span) for t in self.teams}
        total = sum(poss.values()) or 1
        controller = max(poss, key=poss.get) if poss else None
        return {
            "chaos": chaos, "label": label,
            "possession_changes_per_min": round(changes / mins, 2),
            "pass_completion": round(completion, 3),
            "duels_per_min": round(duels / mins, 2),
            "tempo_passes_per_min": round(att / mins, 1),
            "controller": controller,
            "controller_share": round(poss[controller] / total, 3) if controller else None,
        }

    def team_summary(self, team_id: str) -> dict:
        t = self.teams[team_id]
        total_poss = sum(x.possession_s for x in self.teams.values()) or 1
        d = asdict(t)
        d.update(
            possession_pct=round(100 * t.possession_s / total_poss, 1),
            pass_accuracy=round(t.passes_cmp / t.passes_att, 3) if t.passes_att else None,
            xg=round(t.xg, 2),
            field_tilt_5m=self.field_tilt(team_id),
            ppda_10m=self.ppda(team_id),
            distance_km=round(t.distance_m / 1000, 1),
        )
        return d

    def player_card(self, player_id: str) -> dict:
        p = self.players[player_id]
        d = asdict(p)
        d.update(
            pass_accuracy=round(p.pass_accuracy, 3) if p.pass_accuracy is not None else None,
            xg=round(p.xg, 2),
            passes_over_expected=round(p.passes_over_expected, 1),
            distance_km=round(p.distance_m / 1000, 2),
            threat_added=round(p.threat_added, 3),
        )
        return d

    def leaders(self) -> dict:
        def top(key):
            p = max(self.players.values(), key=key)
            return {"player_id": p.player_id, "name": p.name, "team_id": p.team_id, "value": round(key(p), 2)}

        return {
            "top_speed_kmh": top(lambda p: p.top_speed_kmh),
            "distance_km": top(lambda p: p.distance_m / 1000),
            "passes_completed": top(lambda p: p.passes_cmp),
            "passes_over_expected": top(lambda p: p.passes_over_expected),
            "xg": top(lambda p: p.xg),
            "threat_added": top(lambda p: p.threat_added),
        }

    def snapshot(self) -> dict:
        """The shared match state that agents and the UI read."""
        return {
            "match_id": self.meta.match_id if self.meta else None,
            "minute_label": self.minute_label,
            "elapsed_s": self.elapsed_s,
            "period": self.period,
            "finished": self.finished,
            "score": self.score,
            "home_id": self.home_id,
            "away_id": self.away_id,
            "teams": {tid: self.team_summary(tid) for tid in self.teams},
            "momentum": round(self.momentum, 3),
            "rhythm": self.rhythm(),
            "leaders": self.leaders(),
        }
