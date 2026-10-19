"""Stage (c), deterministic half: decide which events matter and gather the evidence.

Each Moment carries a significance score and the Facts that justify it. The
agents turn these into narrative; the facts are what the fact-checker holds
them to.
"""

from __future__ import annotations

from .schema import Event, EventType as T, Fact, Moment
from .stats import EventInsight, MatchState

SPRINT_ALERT_KMH = 33.0
ROCKET_SHOT_KMH = 100.0
ELITE_PASS_DIFFICULTY = 7.0
PASS_STREAK = (12, 20)
PASS_MILESTONES = (50, 75, 100)
DISTANCE_MILESTONES_KM = (10.0, 12.0)


class MomentDetector:
    def __init__(self, state: MatchState):
        self.state = state
        self._count = 0
        self._cooldown_until: dict[str, float] = {}
        self._fired: set[str] = set()
        self._momentum_leader: str | None = None
        self._rhythm_label: str | None = None
        self._rhythm_since = 0.0

    def observe(self, ins: EventInsight) -> list[Moment]:
        e = ins.event
        handler = getattr(self, f"_on_{e.type.value}", None)
        out = handler(e, ins) if handler else []
        out = list(out or [])
        if e.type in (T.PASS, T.CARRY, T.SHOT, T.DRIBBLE, T.TACKLE, T.INTERCEPTION, T.BALL_RECOVERY,
                      T.POSSESSION_CHANGE):
            out += self._flow_moments(e)
        return out

    # ------------------------------------------------------------- helpers

    def _moment(self, e: Event, kind: str, significance: float, headline: str, facts: list[Fact],
                team_id: str | None = None, player_id: str | None = None) -> Moment:
        self._count += 1
        team_id = team_id or e.team_id
        player_id = player_id or e.player_id
        tags = [f"kind:{kind}"]
        if team_id:
            tags.append(f"team:{team_id}")
        if player_id:
            tags.append(f"player:{player_id}")
        return Moment(
            moment_id=f"{e.match_id}-M{self._count:04d}", match_id=e.match_id, seq=e.seq,
            elapsed_s=e.elapsed_s, minute_label=e.minute_label, kind=kind,
            team_id=team_id, player_id=player_id, significance=round(min(1.0, significance), 2),
            headline=headline, facts=facts, tags=tags,
        )

    def _ready(self, key: str, cooldown_s: float) -> bool:
        if self.state.elapsed_s < self._cooldown_until.get(key, -1):
            return False
        self._cooldown_until[key] = self.state.elapsed_s + cooldown_s
        return True

    def _once(self, key: str) -> bool:
        if key in self._fired:
            return False
        self._fired.add(key)
        return True

    def _name(self, pid: str | None) -> str:
        return self.state.player_name(pid)

    def _team(self, tid: str | None) -> str:
        t = self.state.teams.get(tid or "")
        return t.name if t else "Unknown"

    def _score_line(self) -> str:
        s = self.state
        return f"{s.teams[s.home_id].short_name} {s.teams[s.home_id].goals}-{s.teams[s.away_id].goals} {s.teams[s.away_id].short_name}"

    # -------------------------------------------------------- event rules

    def _on_shot(self, e: Event, ins: EventInsight) -> list[Moment]:
        s, out = self.state, []
        xg = ins.xg or 0.0
        facts = [
            Fact(name="xg", label="Chance quality (xG)", value=round(xg, 2)),
            Fact(name="shot_distance_m", label="Shot distance", value=ins.shot_distance_m or 0, unit="m"),
        ]
        if e.ball_speed_kmh:
            facts.append(Fact(name="shot_speed_kmh", label="Shot speed", value=e.ball_speed_kmh, unit="km/h"))
        if ins.possession_passes:
            facts.append(Fact(name="passes_in_move", label="Passes in the move", value=ins.possession_passes))

        if e.outcome == "goal":
            before = ins.score_before or {}
            mine, theirs = before.get(e.team_id, 0) + 1, before.get(s.opponent(e.team_id), 0)
            state = ("equaliser" if mine == theirs else "go-ahead goal" if mine == theirs + 1
                     else "extends the lead" if mine > theirs else "pulls one back")
            p = s.players[e.player_id]
            facts += [Fact(name="score", label="Score", value=self._score_line()),
                      Fact(name="game_state", label="What it changes", value=state),
                      Fact(name="player_goals", label=f"{p.short_name} goals today", value=p.goals)]
            if ins.assist_player_id:
                facts.append(Fact(name="assist", label="Assist", value=self._name(ins.assist_player_id)))
            if e.subtype != "open_play":
                facts.append(Fact(name="situation", label="Situation", value=e.subtype.replace("_", " ")))
            label = {2: " - that's a brace", 3: " - HAT-TRICK"}.get(p.goals, "")
            out.append(self._moment(e, "goal", 1.0,
                                    f"GOAL! {self._name(e.player_id)} ({self._team(e.team_id)}){label}. {self._score_line()}",
                                    facts))
            return out

        if e.outcome == "saved" and xg >= 0.15 and e.related_player_id:
            out.append(self._moment(e, "great_save", 0.55 + 0.4 * xg,
                                    f"Big save from {self._name(e.related_player_id)} to deny {self._name(e.player_id)}",
                                    facts, team_id=s.opponent(e.team_id), player_id=e.related_player_id))
        elif xg >= 0.3:
            out.append(self._moment(e, "big_chance", 0.6 + 0.3 * xg,
                                    f"Huge chance for {self._name(e.player_id)}, {e.outcome.replace('_', ' ')}", facts))
        if (e.ball_speed_kmh or 0) >= ROCKET_SHOT_KMH:
            out.append(self._moment(e, "shot_speed", 0.4,
                                    f"{self._name(e.player_id)} hits one at {e.ball_speed_kmh:.0f} km/h", facts))
        return out

    def _on_pass(self, e: Event, ins: EventInsight) -> list[Moment]:
        if e.outcome != "complete" or ins.pass_difficulty is None:
            return []
        out = []
        p = self.state.players.get(e.player_id or "")
        if ins.pass_difficulty >= ELITE_PASS_DIFFICULTY:
            facts = [
                Fact(name="pass_difficulty", label="Pass difficulty", value=ins.pass_difficulty, unit="/10"),
                Fact(name="pass_length_m", label="Pass length", value=ins.pass_length_m, unit="m"),
                Fact(name="opponents_in_lane", label="Opponents in the passing lane", value=e.opponents_in_lane or 0),
                Fact(name="xpass", label="Chance an average pass is completed", value=round(100 * ins.xpass), unit="%"),
            ]
            if e.under_pressure:
                facts.append(Fact(name="under_pressure", label="Under pressure", value="yes",
                                  context=f"nearest opponent {e.nearest_opponent_m} m"))
            if e.ball_speed_kmh:
                facts.append(Fact(name="ball_speed_kmh", label="Ball speed", value=e.ball_speed_kmh, unit="km/h"))
            kind = e.subtype.replace("_", " ") if e.subtype and e.subtype != "ground" else "pass"
            out.append(self._moment(e, "elite_pass", 0.35 + (ins.pass_difficulty - ELITE_PASS_DIFFICULTY) * 0.1,
                                    f"Quality {kind} from {self._name(e.player_id)} to {self._name(e.recipient_id)}",
                                    facts))
        if ins.possession_passes in PASS_STREAK and self._once(f"streak:{e.possession_id}:{ins.possession_passes}"):
            team = self.state.teams[e.team_id]
            out.append(self._moment(e, "pass_streak", 0.4 if ins.possession_passes == PASS_STREAK[0] else 0.5,
                                    f"{team.name} have strung together {ins.possession_passes} passes",
                                    [Fact(name="passes_in_move", label="Consecutive passes", value=ins.possession_passes),
                                     Fact(name="possession_pct", label=f"{team.short_name} possession",
                                          value=self.state.team_summary(team.team_id)["possession_pct"], unit="%")]))
        if p and p.passes_cmp in PASS_MILESTONES and self._once(f"passes:{p.player_id}:{p.passes_cmp}"):
            out.append(self._moment(e, "milestone", 0.2,
                                    f"{p.name} completes pass number {p.passes_cmp}",
                                    [Fact(name="passes_completed", label="Passes completed", value=p.passes_cmp),
                                     Fact(name="pass_accuracy", label="Pass accuracy",
                                          value=round(100 * (p.pass_accuracy or 0)), unit="%")]))
        return out

    def _on_sprint(self, e: Event, ins: EventInsight) -> list[Moment]:
        speed = e.player_speed_kmh or 0
        s = self.state
        top = max((p.top_speed_kmh for p in s.players.values() if p.player_id != e.player_id), default=0)
        own_best = s.players[e.player_id].top_speed_kmh if e.player_id in s.players else speed
        is_record = speed > top and speed >= own_best and s.elapsed_s > 300
        if speed < SPRINT_ALERT_KMH and not is_record:
            return []
        if not self._ready(f"sprint:{e.player_id}", 600):
            return []
        facts = [Fact(name="top_speed_kmh", label="Top speed", value=speed, unit="km/h")]
        if is_record:
            facts.append(Fact(name="match_record", label="Fastest in the match", value="yes",
                              context=f"previous best {top:.1f} km/h"))
        if (e.data or {}).get("distance_m"):
            facts.append(Fact(name="sprint_distance_m", label="Sprint distance", value=e.data["distance_m"], unit="m"))
        return [self._moment(e, "speed_alert", 0.45 if is_record else 0.3,
                             f"{self._name(e.player_id)} clocks {speed:.1f} km/h", facts)]

    def _on_physical(self, e: Event, ins: EventInsight) -> list[Moment]:
        out = []
        for p in self.state.players.values():
            for km in DISTANCE_MILESTONES_KM:
                if p.distance_m >= km * 1000 and self._once(f"dist:{p.player_id}:{km}"):
                    first = self._once(f"dist-first:{km}")
                    out.append(self._moment(
                        e, "distance_milestone", 0.4 if first else 0.15,
                        f"{p.name} passes {km:.0f} km covered" + (" - first player to get there" if first else ""),
                        [Fact(name="distance_km", label="Distance covered", value=round(p.distance_m / 1000, 1), unit="km")],
                        team_id=p.team_id, player_id=p.player_id))
        return out

    def _on_card(self, e: Event, ins: EventInsight) -> list[Moment]:
        red = e.subtype != "yellow"
        return [self._moment(e, "card", 0.85 if red else 0.35,
                             f"{'Red' if red else 'Yellow'} card for {self._name(e.player_id)}",
                             [Fact(name="card", label="Card", value=e.subtype.replace("_", " ")),
                              Fact(name="team_fouls", label=f"{self.state.teams[e.team_id].short_name} fouls",
                                   value=self.state.teams[e.team_id].fouls)])]

    def _on_set_piece(self, e: Event, ins: EventInsight) -> list[Moment]:
        if e.subtype != "penalty":
            return []
        return [self._moment(e, "penalty_awarded", 0.8, f"Penalty to {self._team(e.team_id)}!",
                             [Fact(name="taker", label="Likely taker", value=self._name(e.player_id))])]

    def _on_substitution(self, e: Event, ins: EventInsight) -> list[Moment]:
        off = self.state.players.get(e.player_id or "")
        facts = [Fact(name="player_on", label="On", value=self._name(e.related_player_id)),
                 Fact(name="player_off", label="Off", value=self._name(e.player_id))]
        if off:
            facts.append(Fact(name="off_distance_km", label=f"{off.short_name} distance covered",
                              value=round(off.distance_m / 1000, 1), unit="km"))
        return [self._moment(e, "substitution", 0.2,
                             f"{self._team(e.team_id)} change: {self._name(e.related_player_id)} on for {self._name(e.player_id)}",
                             facts)]

    def _on_period_end(self, e: Event, ins: EventInsight) -> list[Moment]:
        return [self._recap_moment(e, "half_time" if e.period == 1 else "full_time")]

    def _recap_moment(self, e: Event, kind: str) -> Moment:
        s = self.state
        facts = [Fact(name="score", label="Score", value=self._score_line())]
        for tid in (s.home_id, s.away_id):
            t = s.team_summary(tid)
            facts += [
                Fact(name=f"{tid}_xg", label=f"{t['short_name']} xG", value=t["xg"]),
                Fact(name=f"{tid}_shots", label=f"{t['short_name']} shots", value=t["shots"]),
                Fact(name=f"{tid}_possession", label=f"{t['short_name']} possession", value=t["possession_pct"], unit="%"),
                Fact(name=f"{tid}_pass_accuracy", label=f"{t['short_name']} pass accuracy",
                     value=round(100 * (t["pass_accuracy"] or 0)), unit="%"),
            ]
        lead = s.leaders()
        facts.append(Fact(name="fastest", label="Fastest player", value=lead["top_speed_kmh"]["name"],
                          context=f"{lead['top_speed_kmh']['value']} km/h"))
        facts.append(Fact(name="most_passes", label="Most passes completed", value=lead["passes_completed"]["name"],
                          context=str(lead["passes_completed"]["value"])))
        title = "Half-time" if kind == "half_time" else "Full-time"
        return self._moment(e, kind, 0.9, f"{title}: {self._score_line()}", facts, team_id=None, player_id=None)

    # ------------------------------------------------- flow (state) rules

    def _flow_moments(self, e: Event) -> list[Moment]:
        s, out = self.state, []
        if s.elapsed_s < 240:
            return out

        # Momentum swing: the side generating more threat flips, decisively.
        m = s.momentum
        leader = s.home_id if m >= 0.35 else (s.away_id if m <= -0.35 else None)
        if leader and leader != self._momentum_leader:
            if self._momentum_leader is not None and self._ready("momentum", 360):
                tilt = s.field_tilt(leader)
                facts = [Fact(name="momentum", label="Momentum (home +, away -)", value=round(m, 2)),
                         Fact(name="shots_5m", label=f"{s.teams[leader].short_name} shots, last 5 min",
                              value=int(s.window(leader, "shot"))),
                         Fact(name="xg_5m", label=f"{s.teams[leader].short_name} xG, last 5 min",
                              value=round(s.window(leader, "xg"), 2))]
                if tilt is not None:
                    facts.append(Fact(name="field_tilt_5m", label="Share of final-third touches, last 5 min",
                                      value=round(100 * tilt), unit="%"))
                out.append(self._moment(e, "momentum_swing", 0.55,
                                        f"Momentum swings to {s.teams[leader].name}", facts, team_id=leader, player_id=None))
            self._momentum_leader = leader

        # Pressure spell: one side camped in the final third.
        for tid in s.teams:
            tilt = s.field_tilt(tid)
            if tilt is not None and tilt >= 0.75 and s.window(tid, "ft_touch") >= 15 and self._ready(f"spell:{tid}", 600):
                ppda = s.ppda(tid)
                facts = [Fact(name="field_tilt_5m", label="Share of final-third touches, last 5 min",
                              value=round(100 * tilt), unit="%"),
                         Fact(name="final_third_touches_5m", label="Final-third touches, last 5 min",
                              value=int(s.window(tid, "ft_touch")))]
                if ppda is not None:
                    facts.append(Fact(name="ppda_10m", label="Opponent passes allowed per defensive action (PPDA)",
                                      value=ppda, context="lower means a more aggressive press"))
                out.append(self._moment(e, "pressure_spell", 0.5,
                                        f"{s.teams[tid].name} are pinning {s.teams[s.opponent(tid)].name} back",
                                        facts, team_id=tid, player_id=None))

        # Rhythm shift: the game moves between controlled and chaotic phases.
        r = s.rhythm()
        if r["label"] != self._rhythm_label:
            if self._rhythm_label is not None and s.elapsed_s - self._rhythm_since >= 120 \
                    and self._ready("rhythm", 480):
                facts = [Fact(name="chaos_index", label="Chaos index (0 calm - 100 frantic)", value=r["chaos"]),
                         Fact(name="possession_changes_per_min", label="Turnovers per minute",
                              value=r["possession_changes_per_min"]),
                         Fact(name="pass_completion_5m", label="Pass completion, last 5 min",
                              value=round(100 * r["pass_completion"]), unit="%"),
                         Fact(name="duels_per_min", label="Duels per minute", value=r["duels_per_min"])]
                if r["controller"]:
                    facts.append(Fact(name="controller", label="Side on the ball most",
                                      value=s.teams[r["controller"]].name,
                                      context=f"{round(100 * r['controller_share'])}% of the last 5 min"))
                out.append(self._moment(e, "rhythm_shift", 0.45,
                                        f"The game turns {r['label'].lower()}", facts,
                                        team_id=r["controller"], player_id=None))
            self._rhythm_label = r["label"]
            self._rhythm_since = s.elapsed_s
        return out
