"""Average simulated box scores over many seeds and compare them with typical top-flight ranges."""

from __future__ import annotations

import statistics
from collections import defaultdict

from .schema import EventType as T
from .simulator import simulate

# Per team per match, roughly what a top-flight league looks like.
TARGETS = {
    "goals": (1.0, 1.8),
    "shots": (10, 16),
    "on_target": (3, 6),
    "passes": (380, 620),
    "pass_acc": (0.78, 0.88),
    "possession": (0.35, 0.65),
    "corners": (4, 7),
    "fouls": (9, 13),
    "yellows": (1.0, 2.5),
    "tackles": (12, 20),
    "dribbles": (8, 22),
    "under_pressure_share": (0.15, 0.30),
    "sprints": (40, 140),
    "top_distance_km": (11.0, 13.0),
}


def box_scores(seed: int) -> list[dict[str, float]]:
    meta, events = simulate(seed)
    teams = [meta.home.team_id, meta.away.team_id]
    poss_time: dict[str, float] = defaultdict(float)
    last = None
    for e in events:
        if last is not None and last.possession_team_id:
            poss_time[last.possession_team_id] += e.elapsed_s - last.elapsed_s
        last = e
    total_poss = sum(poss_time.values()) or 1
    phys = [e for e in events if e.type == T.PHYSICAL][-1].data["players"]
    rows = []
    for t in teams:
        mine = [e for e in events if e.team_id == t]
        passes = [e for e in mine if e.type == T.PASS]
        shots = [e for e in mine if e.type == T.SHOT]
        actions = [e for e in mine if e.type in (T.PASS, T.CARRY, T.DRIBBLE, T.SHOT)]
        rows.append({
            "goals": sum(s.outcome == "goal" for s in shots),
            "shots": len(shots),
            "on_target": sum(s.outcome in ("goal", "saved") for s in shots),
            "passes": len(passes),
            "pass_acc": sum(p.outcome == "complete" for p in passes) / max(1, len(passes)),
            "possession": poss_time[t] / total_poss,
            "corners": sum(e.type == T.SET_PIECE and e.subtype == "corner" for e in mine),
            "fouls": sum(e.type == T.FOUL for e in mine),
            "yellows": sum(e.type == T.CARD for e in mine),
            "tackles": sum(e.type == T.TACKLE for e in mine),
            "dribbles": sum(e.type == T.DRIBBLE for e in mine),
            "under_pressure_share": sum(a.under_pressure for a in actions) / max(1, len(actions)),
            "sprints": sum(e.type == T.SPRINT for e in mine),
            "top_distance_km": max(v["distance_m"] for k, v in phys.items() if k.startswith(t)) / 1000,
        })
    return rows


def report(n: int = 30) -> str:
    rows = [r for seed in range(1, n + 1) for r in box_scores(seed)]
    lines = [f"{'metric':<22}{'mean':>8}{'min':>8}{'max':>8}   target"]
    for k, (lo, hi) in TARGETS.items():
        vals = [r[k] for r in rows]
        mean = statistics.mean(vals)
        flag = "" if lo <= mean <= hi else "  <-- out of range"
        lines.append(f"{k:<22}{mean:>8.2f}{min(vals):>8.2f}{max(vals):>8.2f}   {lo}-{hi}{flag}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
