"""Pitch geometry and the open, explainable models (xG, threat, pass difficulty)."""

from __future__ import annotations

import math

from .schema import PITCH_LENGTH, PITCH_WIDTH, Point

GOAL: Point = (PITCH_LENGTH, PITCH_WIDTH / 2)
GOAL_HALF_WIDTH = 3.66
BOX_DEPTH = 16.5
BOX_HALF_WIDTH = 20.16


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def clamp_point(p: Point) -> Point:
    return (clamp(p[0], 0.0, PITCH_LENGTH), clamp(p[1], 0.0, PITCH_WIDTH))


def mirror(p: Point) -> Point:
    """Flip a point into the opposing team's frame."""
    return (PITCH_LENGTH - p[0], PITCH_WIDTH - p[1])


def in_box(p: Point) -> bool:
    return p[0] >= PITCH_LENGTH - BOX_DEPTH and abs(p[1] - PITCH_WIDTH / 2) <= BOX_HALF_WIDTH


def in_final_third(p: Point) -> bool:
    return p[0] >= PITCH_LENGTH * 2 / 3


def goal_distance(p: Point) -> float:
    return dist(p, GOAL)


def goal_angle(p: Point) -> float:
    """Angle in radians subtended by the goal mouth from p."""
    x, y = p
    dx = max(PITCH_LENGTH - x, 0.1)
    a1 = math.atan2(PITCH_WIDTH / 2 + GOAL_HALF_WIDTH - y, dx)
    a2 = math.atan2(PITCH_WIDTH / 2 - GOAL_HALF_WIDTH - y, dx)
    return abs(a1 - a2)


def xg(p: Point, *, header: bool = False, under_pressure: bool = False) -> float:
    """Logistic expected-goals model on distance and angle (roughly 0.27 from the penalty spot)."""
    z = -0.6 - 0.13 * goal_distance(p) + 1.6 * goal_angle(p)
    if header:
        z -= 0.8
    if under_pressure:
        z -= 0.4
    return 1 / (1 + math.exp(-z))


def threat(p: Point) -> float:
    """How dangerous it is to have the ball at p (an expected-threat style surface)."""
    return 0.35 * math.exp(-goal_distance(p) / 12)


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0:
        return dist(p, a)
    t = clamp(((p[0] - ax) * dx + (p[1] - ay) * dy) / seg2, 0.0, 1.0)
    return dist(p, (ax + t * dx, ay + t * dy))


def opponents_in_lane(start: Point, end: Point, opponents: list[Point], width: float = 2.5) -> int:
    """Opponents close enough to the pass line to contest it (ignoring ones right at either end)."""
    count = 0
    for o in opponents:
        if dist(o, start) < 2.0 or dist(o, end) < 2.0:
            continue
        if point_segment_distance(o, start, end) <= width:
            count += 1
    return count


def pass_difficulty(distance: float, lane_opponents: int, under_pressure: bool,
                    nearest_opponent_m: float, progress: float, subtype: str | None) -> float:
    """0-10 rating of how hard a pass is, built from transparent components."""
    s = min(distance, 60.0) / 60.0 * 3.5
    s += min(lane_opponents, 3) * 0.9
    if under_pressure:
        s += 1.4
    elif nearest_opponent_m < 5:
        s += 0.6
    s += clamp(progress / 30.0, 0.0, 1.0) * 1.6
    s += {"through_ball": 1.2, "cross": 1.0, "long_ball": 0.5}.get(subtype or "", 0.0)
    return round(clamp(s, 0.0, 10.0), 1)


def expected_pass_completion(difficulty: float) -> float:
    return clamp(0.98 - 0.075 * difficulty, 0.15, 0.98)
