"""Synthetic, football-realistic match generator.

A possession-chain model: whoever has the ball picks an action (pass, carry,
dribble, shot) based on where they are, how much pressure they're under and
their team's style. Outcomes come from player attributes and pitch geometry,
and each team's intensity drifts and reacts to the score, so matches have
momentum swings and pressure spells worth explaining. No real match data is
used; the same seed always produces the same match.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass, field

from . import geometry as geo
from .schema import Event, EventType, MatchMeta, Point
from .squads import FORMATIONS, PlayerSpec, TeamSpec, ironvale_athletic, kestrel_bay

HALF_S = 45 * 60
PHYSICAL_EVERY_S = 60.0
SPRINT_THRESHOLD_KMH = 25.0

# Average metres covered per second of match time, by role.
ROLE_RUN_RATE = {
    "GK": 0.90, "CB": 1.75, "RB": 1.90, "LB": 1.90, "DM": 1.98, "CM": 2.00,
    "AM": 1.95, "RW": 1.90, "LW": 1.90, "ST": 1.80,
}
ROLE_GROUP = {
    "GK": "GK", "CB": "DF", "RB": "DF", "LB": "DF",
    "DM": "MF", "CM": "MF", "AM": "MF", "RW": "FW", "LW": "FW", "ST": "FW",
}
ATTACKERS = {"ST", "RW", "LW", "AM"}


@dataclass
class _Side:
    spec: TeamSpec
    on_pitch: list[PlayerSpec]
    slots: list[tuple[str, float, float]]  # formation slot per on-pitch player, same order
    bench: list[PlayerSpec]
    sub_minutes: list[int]
    intensity: float = 0.0
    score: int = 0
    yellows: set[str] = field(default_factory=set)
    distance_m: dict[str, float] = field(default_factory=dict)
    top_speed_kmh: dict[str, float] = field(default_factory=dict)
    entered_s: dict[str, float] = field(default_factory=dict)

    @property
    def roles(self) -> list[str]:
        return [s[0] for s in self.slots]

    def index_of_role(self, *roles: str) -> int | None:
        for i, r in enumerate(self.roles):
            if r in roles:
                return i
        return None


class MatchSimulator:
    def __init__(self, seed: int = 7, home: TeamSpec | None = None, away: TeamSpec | None = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.match_id = f"SYN-{seed:05d}"
        self.sides = [self._make_side(home or kestrel_bay()), self._make_side(away or ironvale_athletic())]
        self.period = 1
        self.clock_s = 0.0
        self.elapsed_s = 0.0
        self.period_end_s = float(HALF_S)
        self.poss = 0
        self.possession_id = 0
        self.holder: PlayerSpec = self.sides[0].on_pitch[0]
        self.ball: Point = (52.5, 34.0)
        self.hint: str | None = None
        self.seq = 0
        self.next_physical_s = PHYSICAL_EVERY_S
        self._pending: list[Event] = []
        self._meta = self._build_meta()

    # ------------------------------------------------------------------ setup

    def _make_side(self, spec: TeamSpec) -> _Side:
        n_subs = self.rng.choice([3, 4, 4, 5])
        minutes = sorted(self.rng.randint(55, 84) for _ in range(n_subs))
        side = _Side(spec=spec, on_pitch=list(spec.starters), slots=list(FORMATIONS[spec.formation]),
                     bench=list(spec.bench), sub_minutes=minutes)
        for p in side.on_pitch:
            side.distance_m[p.player_id] = 0.0
            side.entered_s[p.player_id] = 0.0
        return side

    def _build_meta(self) -> MatchMeta:
        weather = self.rng.choice(["Clear, 14°C", "Light rain, 9°C", "Overcast, 11°C", "Windy, 8°C"])
        return MatchMeta(
            match_id=self.match_id,
            competition="Synthetic Football League (demo data)",
            venue="The Lighthouse Ground",
            kickoff_utc="2026-10-24T15:00:00Z",
            referee="Ana Whitlock",
            weather=weather,
            seed=self.seed,
            home=self.sides[0].spec.info(),
            away=self.sides[1].spec.info(),
        )

    @property
    def meta(self) -> MatchMeta:
        return self._meta

    # ------------------------------------------------------------- main loop

    def run(self) -> Iterator[Event]:
        self._emit(EventType.MATCH_START, data=self._meta.model_dump())
        yield from self._flush()
        for period in (1, 2):
            self._start_period(period)
            yield from self._flush()
            while self.clock_s < self.period_end_s:
                self._step()
                yield from self._flush()
            self._emit(EventType.PERIOD_END, data=self._score_data())
            yield from self._flush()
        self._emit(EventType.MATCH_END, data=self._score_data())
        yield from self._flush()

    def _flush(self) -> Iterator[Event]:
        pending, self._pending = self._pending, []
        yield from pending

    def _start_period(self, period: int) -> None:
        self.period = period
        if period == 1:
            self.clock_s = 0.0
            self.period_end_s = HALF_S + self.rng.uniform(60, 240)
            kicker = 0
        else:
            self.clock_s = float(HALF_S)
            self.period_end_s = 2 * HALF_S + self.rng.uniform(150, 420)
            kicker = 1
        self._kick_off(kicker)

    def _kick_off(self, side_idx: int) -> None:
        if self.poss != side_idx:
            self.poss = side_idx
        self.possession_id += 1
        side = self.sides[side_idx]
        i = side.index_of_role("ST", "AM")
        self.holder = side.on_pitch[i if i is not None else len(side.on_pitch) - 1]
        self.ball = (52.5, 34.0)
        self._emit(EventType.KICK_OFF, side_idx=side_idx, player=self.holder, location=self.ball)
        self.hint = "kick_off"

    # ------------------------------------------------------------ positions

    def _positions(self, side_idx: int, ball: Point, in_possession: bool) -> list[Point]:
        """Where each on-pitch player of a side is, in that side's frame."""
        side = self.sides[side_idx]
        bx, by = ball
        pts: list[Point] = []
        advance = 7.0 if in_possession else -5.0
        width = 1.05 if in_possession else 0.72
        for role, x, y in side.slots:
            if role == "GK":
                pts.append((geo.clamp(4 + max(0.0, bx - 40) * 0.12, 2, 18),
                            geo.clamp(34 + (by - 34) * 0.15, 28, 40)))
                continue
            px = geo.clamp(x + (bx - 52.5) * 0.45 + advance, 8, 91)
            py = 34 + ((y + (by - 34) * 0.25) - 34) * width
            pts.append(geo.clamp_point((px + self.rng.gauss(0, 2.5), py + self.rng.gauss(0, 2.5))))
        return pts

    def _opponents(self) -> list[Point]:
        """Defending side's players in the attacking frame, with the nearest one pressing the ball."""
        d = 1 - self.poss
        pts = [geo.mirror(p) for p in self._positions(d, geo.mirror(self.ball), False)]
        press = self.sides[d].spec.press * (1 + 0.35 * self.sides[d].intensity)
        zone = 1.0 if self.ball[0] < 35 else (0.85 if self.ball[0] < 70 else 1.0)
        if geo.in_box(self.ball):
            zone = 1.35  # packed box: someone is always close
        outfield = [i for i, r in enumerate(self.sides[d].roles) if r != "GK"]
        i = min(outfield, key=lambda k: geo.dist(pts[k], self.ball))
        pull = geo.clamp((0.30 + 0.40 * press) * zone * self.rng.uniform(0.4, 1.0), 0.05, 0.92)
        px, py = pts[i]
        pts[i] = (px + (self.ball[0] - px) * pull, py + (self.ball[1] - py) * pull)
        return pts

    # ---------------------------------------------------------------- step

    @property
    def att(self) -> _Side:
        return self.sides[self.poss]

    @property
    def dfn(self) -> _Side:
        return self.sides[1 - self.poss]

    def _step(self) -> None:
        self._maybe_substitute()
        hint, self.hint = self.hint, None
        opps = self._opponents()
        ni = min(range(len(opps)), key=lambda k: geo.dist(opps[k], self.ball))
        nearest = geo.dist(opps[ni], self.ball)
        under = hint is None and nearest < 3.0

        if under:
            presser = self.dfn.on_pitch[ni]
            self._emit(EventType.PRESSURE, side_idx=1 - self.poss, player=presser,
                       location=geo.mirror(self.ball), related_player_id=self.holder.player_id,
                       nearest_opponent_m=round(nearest, 1))
            r = self.rng.random()
            if r < 0.07 * self._box_foul_factor():
                self._foul(presser, self.holder)
                return
            if r < 0.07 + 0.02 + 0.04 * self.dfn.spec.press:
                self._emit(EventType.TACKLE, side_idx=1 - self.poss, player=presser,
                           location=geo.mirror(self.ball), outcome="won", related_player_id=self.holder.player_id)
                self._advance(self.rng.uniform(1.0, 2.0))
                self._turnover(presser, geo.mirror(self.ball), "tackle")
                return

        if hint in ("corner",):
            self._do_corner(opps)
        elif hint == "header":
            self._do_shot(under_pressure=True, nearest=nearest, header=True)
        elif hint == "penalty":
            self._do_shot(under_pressure=False, nearest=11.0, subtype="penalty")
        elif hint == "free_kick" and self.ball[0] > 78 and 18 < self.ball[1] < 50 and self.rng.random() < 0.4:
            self._do_shot(under_pressure=False, nearest=9.0, subtype="free_kick")
        elif hint is not None:
            self._do_pass(opps, nearest, under_pressure=False, hint=hint)
        else:
            action = self._choose_action(nearest)
            if action == "pass":
                self._do_pass(opps, nearest, under)
            elif action == "carry":
                self._do_carry(nearest)
            elif action == "dribble":
                self._do_dribble(opps, ni)
            else:
                self._do_shot(under, nearest)

    def _box_foul_factor(self) -> float:
        """Defenders are far more careful in their own box, so fouls (penalties) there are rarer."""
        return 0.08 if geo.in_box(self.ball) else 1.0

    def _role(self, p: PlayerSpec) -> str:
        side = self.att if p in self.att.on_pitch else self.dfn
        return side.roles[side.on_pitch.index(p)]

    def _choose_action(self, nearest: float) -> str:
        role = self._role(self.holder)
        if role == "GK":
            return "pass"
        x = self.ball[0]
        w = {"pass": 1.0, "carry": 0.22, "dribble": 0.03, "shot": 0.0}
        if nearest > 5:
            w["carry"] += 0.25
        if nearest < 3:
            w["dribble"] += 0.015 + self.holder.dribbling / 100 * 0.045
        if x > 60:
            w["dribble"] += 0.005 + self.holder.dribbling / 100 * 0.035
        if x > 75:
            q = geo.xg(self.ball)
            w["shot"] = (q * (0.7 if geo.in_box(self.ball) else 1.9)
                         * (1.3 if role in ATTACKERS else 0.6) * (1 + 0.3 * self.att.intensity))
        return self.rng.choices(list(w), weights=list(w.values()))[0]

    # -------------------------------------------------------------- actions

    def _do_pass(self, opps: list[Point], nearest: float, under_pressure: bool, hint: str | None = None) -> None:
        side, holder, start = self.att, self.holder, self.ball
        mates = self._positions(self.poss, start, True)
        hi = side.on_pitch.index(holder)
        directness = geo.clamp(side.spec.directness * (1 + 0.25 * side.intensity), 0.05, 1.0)
        if hint == "goal_kick":
            directness = 0.95 if self.rng.random() < side.spec.directness * 0.8 else 0.05

        candidates: list[tuple[float, int, Point]] = []
        for i, mp in enumerate(mates):
            role = side.roles[i]
            d = geo.dist(start, mp)
            if i == hi or d < 4 or (role == "GK" and start[0] > 35):
                continue
            if hint == "throw_in" and d > 22:
                continue
            if hint == "kick_off" and mp[0] > 52.5:
                continue
            progress = mp[0] - start[0]
            space = min(geo.dist(mp, o) for o in opps)
            lane = geo.opponents_in_lane(start, mp, opps)
            score = (0.02 + 0.04 * directness) * progress
            score -= 0.06 * max(0.0, d - 15) * (1.0 - 0.5 * directness)
            score -= 0.9 * lane * (0.6 + 0.6 * side.spec.possession)
            score += 0.12 * min(space, 10.0)
            score += 3.0 * geo.threat(mp)
            candidates.append((score, i, mp))
        if not candidates:
            self._do_carry(nearest)
            return
        top = max(c[0] for c in candidates)
        weights = [math.exp(c[0] - top) for c in candidates]
        _, ri, target = self.rng.choices(candidates, weights=weights)[0]
        receiver = side.on_pitch[ri]
        rrole = side.roles[ri]

        subtype = "ground"
        end = target
        if (hint is None and rrole in ATTACKERS and target[0] > 55 and target[0] - start[0] > 5
                and self.rng.random() < 0.08 + directness * 0.15):
            run = (min(target[0] + self.rng.uniform(6, 14), 96.0), target[1] + self.rng.gauss(0, 3))
            if run[0] > target[0] + 3:
                end, subtype = geo.clamp_point(run), "through_ball"
        if subtype == "ground":
            if start[0] > 72 and (start[1] < 16 or start[1] > 52) and geo.in_box(end):
                subtype = "cross"
            elif geo.dist(start, end) > 32:
                subtype = "long_ball"

        d = geo.dist(start, end)
        lane = geo.opponents_in_lane(start, end, opps)
        p = 0.965 - 0.0035 * max(0.0, d - 12) - 0.07 * lane
        if under_pressure:
            p -= 0.09
        elif nearest < 5:
            p -= 0.03
        p -= {"through_ball": 0.16, "cross": 0.45, "long_ball": 0.10}.get(subtype, 0.0)
        p += (holder.passing - 75) / 100 * 0.35 + 0.03 * side.intensity - 0.04 * self._fatigue(holder)
        p = geo.clamp(p, 0.12, 0.98)

        speed = geo.clamp(28 + d * 1.15 + self.rng.gauss(0, 5)
                          + {"through_ball": 6, "cross": 8}.get(subtype, 0), 18, 98)
        common = dict(side_idx=self.poss, player=holder, location=start, subtype=subtype,
                      under_pressure=under_pressure, nearest_opponent_m=round(nearest, 1),
                      opponents_in_lane=lane, ball_speed_kmh=round(speed, 1))
        travel = d / (speed / 3.6)

        if self.rng.random() < p:
            self._emit(EventType.PASS, end_location=end, outcome="complete",
                       recipient_id=receiver.player_id, **common)
            if subtype == "through_ball":
                self._sprint(self.poss, receiver, target, d_m=geo.dist(target, end) + 8)
            self._advance(travel + self.rng.uniform(1.0, 3.2))
            self.holder, self.ball = receiver, end
            return

        r = self.rng.random()
        if subtype == "through_ball" and r < 0.25:
            self._emit(EventType.PASS, end_location=end, outcome="offside",
                       recipient_id=receiver.player_id, **common)
            self._advance(travel + 2)
            self._set_piece("free_kick", 1 - self.poss, geo.mirror(end), reason="offside")
        elif r < 0.82:
            oi = min(range(len(opps)), key=lambda k: geo.point_segment_distance(opps[k], start, end))
            t = self.rng.uniform(0.35, 0.95)
            at = geo.clamp_point((start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t))
            self._emit(EventType.PASS, end_location=at, outcome="incomplete",
                       recipient_id=receiver.player_id, **common)
            self._advance(travel * t + 0.5)
            interceptor = self.dfn.on_pitch[oi]
            in_lane = geo.point_segment_distance(opps[oi], start, end) <= 2.5
            self._emit(EventType.INTERCEPTION if in_lane else EventType.BALL_RECOVERY,
                       side_idx=1 - self.poss, player=interceptor,
                       location=geo.mirror(at), related_player_id=holder.player_id)
            if geo.in_box(at) and self.rng.random() < 0.2:
                self._set_piece("corner", self.poss, (105.0, 0.5 if at[1] < 34 else 67.5))
            else:
                self._turnover(interceptor, geo.mirror(at), "interception" if in_lane else "recovery")
        else:
            out = geo.clamp_point((end[0] + self.rng.uniform(2, 10), end[1] + self.rng.gauss(0, 6)))
            self._emit(EventType.PASS, end_location=out, outcome="out_of_play",
                       recipient_id=receiver.player_id, **common)
            self._advance(travel + 1)
            if out[0] > 100:
                if subtype == "cross" and self.rng.random() < 0.35:
                    self._set_piece("corner", self.poss, (105.0, 0.5 if out[1] < 34 else 67.5))
                else:
                    self._set_piece("goal_kick", 1 - self.poss, (5.5, 34.0))
            else:
                y = 0.0 if out[1] < 34 else 68.0
                self._set_piece("throw_in", 1 - self.poss, geo.mirror((out[0], y)))

    def _do_carry(self, nearest: float) -> None:
        holder, (x, y) = self.holder, self.ball
        sprint = nearest > 7 and x > 40 and self.rng.random() < 0.3 + (holder.pace_kmh - 31) / 20
        dx = self.rng.uniform(3, 9) + (6 if nearest > 6 else 0) + (8 if sprint else 0)
        dy = self.rng.gauss(0, 4) + ((34 - y) * 0.15 if x > 75 else 0)
        end = geo.clamp_point((min(x + dx, max(x, 97.0)), y + dy))
        d = geo.dist(self.ball, end)
        speed = holder.pace_kmh * self.rng.uniform(0.84, 0.99) if sprint else self.rng.uniform(11, 20)
        self._emit(EventType.CARRY, side_idx=self.poss, player=holder, location=self.ball,
                   end_location=end, player_speed_kmh=round(speed, 1), nearest_opponent_m=round(nearest, 1))
        if sprint:
            self._sprint(self.poss, holder, self.ball, d_m=d, speed=speed)
        self._advance(d / (speed / 3.6) + 0.3)
        self.ball = end

    def _do_dribble(self, opps: list[Point], ni: int) -> None:
        holder, defender = self.holder, self.dfn.on_pitch[ni]
        p = geo.clamp(0.48 + (holder.dribbling - defender.tackling) / 180 + 0.05 * self.att.intensity, 0.2, 0.8)
        if self.rng.random() < p:
            end = geo.clamp_point((min(self.ball[0] + self.rng.uniform(4, 8), 102.0),
                                   self.ball[1] + self.rng.gauss(0, 3)))
            self._emit(EventType.DRIBBLE, side_idx=self.poss, player=holder, location=self.ball,
                       end_location=end, outcome="complete", related_player_id=defender.player_id)
            self._advance(self.rng.uniform(1.5, 3.0))
            self.ball = end
            return
        self._emit(EventType.DRIBBLE, side_idx=self.poss, player=holder, location=self.ball,
                   outcome="incomplete", related_player_id=defender.player_id)
        self._advance(self.rng.uniform(1.0, 2.0))
        if self.rng.random() < 0.25 * self._box_foul_factor():
            self._foul(defender, holder)
            return
        self._emit(EventType.TACKLE, side_idx=1 - self.poss, player=defender,
                   location=geo.mirror(self.ball), outcome="won", related_player_id=holder.player_id)
        self._turnover(defender, geo.mirror(self.ball), "tackle")

    def _do_shot(self, under_pressure: bool, nearest: float, header: bool = False,
                 subtype: str = "open_play") -> None:
        side, holder = self.att, self.holder
        if subtype == "penalty":
            self.ball = (94.0, 34.0)
        q = geo.xg(self.ball, header=header, under_pressure=under_pressure)
        if subtype == "penalty":
            q = 0.78
        elif subtype == "free_kick":
            q = 0.06
        gi = self.dfn.index_of_role("GK")
        keeper = self.dfn.on_pitch[gi] if gi is not None else None
        keeper_skill = keeper.goalkeeping if keeper else 40
        p_goal = geo.clamp(q * (0.7 + holder.finishing / 200) * (1.05 - keeper_skill / 100 * 0.3), 0.005, 0.9)
        dgoal = geo.goal_distance(self.ball)
        if header:
            speed = self.rng.uniform(38, 60)
        else:
            speed = geo.clamp(58 + min(dgoal, 32) * 1.45 + self.rng.gauss(0, 9), 45, 128)

        if self.rng.random() < p_goal:
            outcome = "goal"
        else:
            blocked = 0.05 if header or subtype == "penalty" else (0.32 if under_pressure else 0.18)
            r = self.rng.random()
            if r < blocked:
                outcome = "blocked"
            elif r < blocked + (1 - blocked) * (0.28 + min(q, 0.4) * 0.5):
                outcome = "saved"
            else:
                outcome = "off_target"

        if outcome in ("goal", "saved"):
            end = (105.0, 34 + geo.clamp(self.rng.gauss(0, 2), -3.4, 3.4))
        elif outcome == "off_target":
            end = (105.0, 34 + self.rng.choice([-1, 1]) * self.rng.uniform(4.2, 12))
        else:
            end = geo.clamp_point((self.ball[0] + self.rng.uniform(1, 4), self.ball[1] + self.rng.gauss(0, 1.5)))

        self._emit(EventType.SHOT, side_idx=self.poss, player=holder, location=self.ball, end_location=end,
                   outcome=outcome, subtype=subtype, body_part="head" if header else "foot",
                   under_pressure=under_pressure, nearest_opponent_m=round(nearest, 1),
                   ball_speed_kmh=round(speed, 1),
                   related_player_id=keeper.player_id if keeper and outcome == "saved" else None)
        self._advance(self.rng.uniform(1.0, 2.0))

        if outcome == "goal":
            side.score += 1
            side.intensity = geo.clamp(side.intensity + 0.2, -1, 1)
            self.dfn.intensity = geo.clamp(self.dfn.intensity + 0.35, -1, 1)
            self._advance(self.rng.uniform(55, 90))
            self._kick_off(1 - self.poss)
        elif outcome == "saved":
            if self.rng.random() < 0.35:
                self._set_piece("corner", self.poss, (105.0, 0.5 if self.ball[1] < 34 else 67.5))
            elif keeper:
                self._turnover(keeper, (5.0, 34.0), "save")
        elif outcome == "blocked":
            r = self.rng.random()
            if r < 0.25:
                self._set_piece("corner", self.poss, (105.0, 0.5 if self.ball[1] < 34 else 67.5))
            elif r < 0.65:
                mates = [p for i, p in enumerate(side.on_pitch) if side.roles[i] != "GK" and p is not holder]
                self.holder = self.rng.choice(mates)
                self.ball = geo.clamp_point((self.ball[0] - self.rng.uniform(3, 8), self.ball[1] + self.rng.gauss(0, 5)))
            else:
                ci = self.dfn.index_of_role("CB") or 0
                self._turnover(self.dfn.on_pitch[ci], geo.mirror(self.ball), "blocked_shot")
        else:
            self._set_piece("goal_kick", 1 - self.poss, (5.5, 34.0))

    def _do_corner(self, opps: list[Point]) -> None:
        side = self.att
        targets = [i for i, r in enumerate(side.roles) if r in ("CB", "ST", "AM", "DM")]
        ri = self.rng.choice(targets)
        receiver = side.on_pitch[ri]
        end = (self.rng.uniform(94, 100), self.rng.uniform(26, 42))
        d = geo.dist(self.ball, end)
        lane = geo.opponents_in_lane(self.ball, end, opps)
        speed = round(geo.clamp(55 + self.rng.gauss(0, 6), 40, 80), 1)
        common = dict(side_idx=self.poss, player=self.holder, location=self.ball, subtype="cross",
                      opponents_in_lane=lane, ball_speed_kmh=speed, nearest_opponent_m=9.0)
        if self.rng.random() < 0.32:
            self._emit(EventType.PASS, end_location=end, outcome="complete",
                       recipient_id=receiver.player_id, **common)
            self._advance(d / (speed / 3.6) + 0.2)
            self.holder, self.ball = receiver, end
            self.hint = "header" if self.rng.random() < 0.65 else None
            return
        self._emit(EventType.PASS, end_location=end, outcome="incomplete",
                   recipient_id=receiver.player_id, **common)
        self._advance(d / (speed / 3.6) + 0.5)
        if self.rng.random() < 0.15:
            self._set_piece("corner", self.poss, self.ball)
            return
        cb = self.dfn.index_of_role("CB") or 1
        clearer = self.dfn.on_pitch[cb]
        self._emit(EventType.INTERCEPTION, side_idx=1 - self.poss, player=clearer,
                   location=geo.mirror(end), subtype="clearance", related_player_id=receiver.player_id)
        self._turnover(clearer, geo.mirror(end), "clearance")

    # ----------------------------------------------------------- dead balls

    def _foul(self, fouler: PlayerSpec, victim: PlayerSpec) -> None:
        fside = 1 - self.poss
        self._emit(EventType.FOUL, side_idx=fside, player=fouler, location=geo.mirror(self.ball),
                   related_player_id=victim.player_id)
        side = self.sides[fside]
        if self.rng.random() < 0.15 + (0.08 if self.ball[0] > 70 else 0):
            if fouler.player_id in side.yellows:
                self._emit(EventType.CARD, side_idx=fside, player=fouler, subtype="second_yellow")
                i = side.on_pitch.index(fouler)
                side.on_pitch.pop(i)
                side.slots.pop(i)
            else:
                side.yellows.add(fouler.player_id)
                self._emit(EventType.CARD, side_idx=fside, player=fouler, subtype="yellow")
        if geo.in_box(self.ball):
            self.holder = max((p for p in self.att.on_pitch), key=lambda p: p.finishing)
            self._set_piece("penalty", self.poss, (94.0, 34.0), taker=self.holder)
        else:
            self._set_piece("free_kick", self.poss, self.ball, taker=victim)

    def _set_piece(self, kind: str, side_idx: int, at: Point, taker: PlayerSpec | None = None,
                   reason: str = "out_of_play") -> None:
        if side_idx != self.poss:
            self.poss = side_idx
            self.possession_id += 1
            self._emit(EventType.POSSESSION_CHANGE, side_idx=side_idx, location=at, data={"reason": reason})
        side = self.sides[side_idx]
        if taker is None or taker not in side.on_pitch:
            if kind == "goal_kick":
                gi = side.index_of_role("GK")
                taker = side.on_pitch[gi if gi is not None else 0]
            elif kind == "corner":
                taker = max((p for i, p in enumerate(side.on_pitch) if side.roles[i] != "GK"),
                            key=lambda p: p.passing)
            elif kind == "throw_in":
                fi = side.index_of_role("RB" if at[1] < 34 else "LB", "CM", "DM")
                taker = side.on_pitch[fi if fi is not None else 1]
            else:
                taker = max(side.on_pitch, key=lambda p: p.passing)
        self.holder, self.ball = taker, at
        self._emit(EventType.SET_PIECE, side_idx=side_idx, player=taker, location=at, subtype=kind)
        self._advance({"throw_in": self.rng.uniform(10, 22), "goal_kick": self.rng.uniform(15, 28),
                       "corner": self.rng.uniform(25, 40), "free_kick": self.rng.uniform(18, 35),
                       "penalty": self.rng.uniform(45, 70)}.get(kind, 5))
        self.hint = kind

    def _turnover(self, new_holder: PlayerSpec, at: Point, reason: str) -> None:
        losing = self.poss
        self.poss = 1 - self.poss
        self.possession_id += 1
        self.holder, self.ball = new_holder, at
        self._emit(EventType.POSSESSION_CHANGE, side_idx=self.poss, player=new_holder, location=at,
                   data={"reason": reason})
        if at[0] < 60 and self.rng.random() < 0.3:
            runners = [i for i, r in enumerate(self.att.roles) if r in ATTACKERS]
            if runners:
                i = self.rng.choice(runners)
                self._sprint(self.poss, self.att.on_pitch[i], (at[0] + 25, 34.0), d_m=self.rng.uniform(15, 40))
        if self.rng.random() < 0.2:
            back = [i for i, r in enumerate(self.sides[losing].roles) if ROLE_GROUP[r] in ("DF", "MF")]
            i = self.rng.choice(back)
            self._sprint(losing, self.sides[losing].on_pitch[i], geo.mirror(at), d_m=self.rng.uniform(12, 30))

    def _sprint(self, side_idx: int, player: PlayerSpec, at: Point, d_m: float, speed: float | None = None) -> None:
        speed = speed or player.pace_kmh * self.rng.uniform(0.83, 0.98)
        if speed < SPRINT_THRESHOLD_KMH:
            return
        side = self.sides[side_idx]
        side.top_speed_kmh[player.player_id] = max(side.top_speed_kmh.get(player.player_id, 0.0), speed)
        self._emit(EventType.SPRINT, side_idx=side_idx, player=player, location=geo.clamp_point(at),
                   player_speed_kmh=round(speed, 1), data={"distance_m": round(d_m, 1)})

    def _maybe_substitute(self) -> None:
        if self.period != 2:
            return
        minute = self.clock_s / 60
        for si, side in enumerate(self.sides):
            while side.sub_minutes and minute >= side.sub_minutes[0] and side.bench:
                side.sub_minutes.pop(0)
                outfield = [i for i, r in enumerate(side.roles)
                            if r != "GK" and side.on_pitch[i] is not self.holder]
                outfield.sort(key=lambda i: (ROLE_GROUP[side.roles[i]] == "DF",
                                             -self._fatigue(side.on_pitch[i]) - self.rng.random() * 0.3))
                i = outfield[0]
                group = ROLE_GROUP[side.roles[i]]
                bench = [p for p in side.bench if p.position != "GK"]
                same = [p for p in bench if ROLE_GROUP.get(p.position) == group]
                incoming = (same or bench)[0]
                side.bench.remove(incoming)
                outgoing = side.on_pitch[i]
                side.on_pitch[i] = incoming
                side.distance_m[incoming.player_id] = 0.0
                side.entered_s[incoming.player_id] = self.elapsed_s
                self._emit(EventType.SUBSTITUTION, side_idx=si, player=outgoing,
                           related_player_id=incoming.player_id, data={"role": side.roles[i]})
                self._advance(self.rng.uniform(20, 35))

    # ------------------------------------------------------- clock & output

    def _fatigue(self, p: PlayerSpec) -> float:
        for side in self.sides:
            if p.player_id in side.entered_s:
                on_for = self.elapsed_s - side.entered_s[p.player_id]
                return geo.clamp((on_for - 55 * 60) / (40 * 60), 0.0, 1.0)
        return 0.0

    def _advance(self, dt: float) -> None:
        dt = max(dt, 0.0)
        self.clock_s += dt
        self.elapsed_s += dt
        for side in self.sides:
            for i, p in enumerate(side.on_pitch):
                rate = ROLE_RUN_RATE[side.roles[i]] * p.work_rate * (1 - 0.06 * self._fatigue(p))
                side.distance_m[p.player_id] = side.distance_m.get(p.player_id, 0.0) + rate * dt
        while self.elapsed_s >= self.next_physical_s:
            self.next_physical_s += PHYSICAL_EVERY_S
            self._physical_tick()

    def _physical_tick(self) -> None:
        for side in self.sides:
            drift = self.rng.gauss(0, 0.10) - 0.08 * side.intensity
            other = self.sides[1] if side is self.sides[0] else self.sides[0]
            if self.period == 2 and self.clock_s > 70 * 60 and side.score < other.score:
                drift += 0.05
            side.intensity = geo.clamp(side.intensity + drift, -1, 1)
        players = {}
        for side in self.sides:
            for p in side.on_pitch:
                players[p.player_id] = {
                    "distance_m": round(side.distance_m.get(p.player_id, 0.0), 1),
                    "top_speed_kmh": round(side.top_speed_kmh.get(p.player_id, 0.0), 1),
                }
        self._emit(EventType.PHYSICAL, data={"players": players})

    def _score_data(self) -> dict:
        return {"score": {self.sides[0].spec.team_id: self.sides[0].score,
                          self.sides[1].spec.team_id: self.sides[1].score}}

    def _minute_label(self) -> str:
        m = int(self.clock_s // 60) + 1
        base = 45 if self.period == 1 else 90
        return f"{base}+{m - base}'" if m > base else f"{m}'"

    def _emit(self, type_: EventType, side_idx: int | None = None, player: PlayerSpec | None = None,
              location: Point | None = None, end_location: Point | None = None, **kw) -> None:
        def r(p: Point | None) -> Point | None:
            return None if p is None else (round(p[0], 1), round(p[1], 1))

        self._pending.append(Event(
            match_id=self.match_id, seq=self.seq, type=type_, period=self.period,
            clock_s=round(self.clock_s, 2), elapsed_s=round(self.elapsed_s, 2),
            minute_label=self._minute_label(),
            team_id=self.sides[side_idx].spec.team_id if side_idx is not None else None,
            player_id=player.player_id if player else None,
            possession_id=self.possession_id,
            possession_team_id=self.sides[self.poss].spec.team_id,
            location=r(location), end_location=r(end_location), **kw,
        ))
        self.seq += 1


def simulate(seed: int = 7) -> tuple[MatchMeta, list[Event]]:
    sim = MatchSimulator(seed)
    events = list(sim.run())
    return sim.meta, events
