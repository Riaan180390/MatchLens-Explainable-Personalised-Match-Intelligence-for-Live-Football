"""Command line: simulate matches, watch the live ticker, print box scores, export schemas."""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
from pathlib import Path

from .personas import ViewerProfile
from .pipeline import InsightsPipeline, viewer_cues
from .replay import paced, read_jsonl, write_jsonl
from .schema import Event, Moment, OverlayCue
from .simulator import MatchSimulator


def _events(args) -> list[Event]:
    if getattr(args, "file", None):
        return list(read_jsonl(Path(args.file)))
    return list(MatchSimulator(args.seed).run())


def cmd_simulate(args) -> None:
    out = Path(args.out or f"data/samples/SYN-{args.seed:05d}.jsonl")
    n = write_jsonl(MatchSimulator(args.seed).run(), out)
    print(f"wrote {n} events to {out}")


def cmd_ticker(args) -> None:
    viewer = ViewerProfile(mode=args.mode, favourite_team_id=args.team,
                           favourite_player_ids=[args.player] if args.player else [],
                           focus_player_id=args.player if args.mode == "player_focus" else None)
    pipe = InsightsPipeline()
    for result in pipe.run(paced(_events(args), args.speed)):
        for cue in viewer_cues(result, viewer):
            if cue.template == "score_bug":
                continue
            s = cue.slots
            line = f"{s['minute']:>7}  [{cue.template:<14}] {s['title']}"
            if s.get("body"):
                line += f" - {s['body']}"
            print(line, flush=True)
    if args.json:
        print(json.dumps(pipe.state.snapshot(), indent=1, default=str))


def cmd_stats(args) -> None:
    pipe = InsightsPipeline()
    for _ in pipe.run(_events(args)):
        pass
    s = pipe.state
    rows = ["possession_pct", "goals", "xg", "shots", "shots_on_target", "big_chances", "passes_att",
            "pass_accuracy", "progressive_passes", "final_third_entries", "tackles_won", "interceptions",
            "pressures", "fouls", "yellow_cards", "corners", "sprints", "distance_km"]
    h, a = s.team_summary(s.home_id), s.team_summary(s.away_id)
    print(f"{'':<22}{h['short_name']:>8}{a['short_name']:>8}")
    for r in rows:
        print(f"{r:<22}{h[r]!s:>8}{a[r]!s:>8}")
    print("\nleaders:")
    for k, v in s.leaders().items():
        print(f"  {k:<22}{v['name']} ({v['team_id']}) {v['value']}")
    print(f"\nmoments detected: {len(pipe.moments)}")


def cmd_schema(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, model in (("event", Event), ("moment", Moment), ("overlay_cue", OverlayCue),
                        ("viewer_profile", ViewerProfile)):
        (out / f"{name}.schema.json").write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
        print(f"wrote {out / f'{name}.schema.json'}")


def cmd_calibrate(args) -> None:
    from .calibrate import report
    print(report(args.n))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="matchlens", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("simulate", help="generate a synthetic match as JSONL")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out")
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("ticker", help="replay a match and print the overlay feed for one viewer")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--file", help="replay a JSONL file instead of simulating")
    p.add_argument("--speed", type=float, default=0, help="1 = real time, 60 = a minute per second, 0 = instant")
    p.add_argument("--mode", choices=["casual", "analyst", "player_focus"], default="casual")
    p.add_argument("--team", help="favourite team id, e.g. KBY")
    p.add_argument("--player", help="favourite / focus player id, e.g. KBY-10")
    p.add_argument("--json", action="store_true", help="print the final match state")
    p.set_defaults(func=cmd_ticker)

    p = sub.add_parser("stats", help="print the box score and leaders")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--file")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("schema", help="export JSON Schemas for the data contracts")
    p.add_argument("--out", default="schemas")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("calibrate", help="compare simulated box scores with real-world ranges")
    p.add_argument("--n", type=int, default=30)
    p.set_defaults(func=cmd_calibrate)

    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        args.func(args)
    except OSError as e:  # output piped into `head` etc. and closed early
        if e.errno not in (errno.EPIPE, errno.EINVAL):
            raise
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


if __name__ == "__main__":
    main()
