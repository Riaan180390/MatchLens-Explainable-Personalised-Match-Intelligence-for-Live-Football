"""Command line: simulate matches, watch the live ticker (with or without agents), print box scores, export schemas."""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import os
import sys
from pathlib import Path

from .personas import ViewerProfile, should_show
from .pipeline import InsightsPipeline, viewer_cues
from .replay import apaced, paced, read_jsonl, write_jsonl
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


def _viewer(args) -> ViewerProfile:
    return ViewerProfile(mode=args.mode, favourite_team_id=args.team,
                         favourite_player_ids=[args.player] if args.player else [],
                         focus_player_id=args.player if args.mode == "player_focus" else None)


def _cue_line(cue: OverlayCue, tag: str = "") -> str:
    s = cue.slots
    line = f"{s['minute']:>7}  {tag}[{cue.template:<14}] {s['title']}"
    return line + (f" - {s['body']}" if s.get("body") else "")


def cmd_ticker(args) -> None:
    viewer = _viewer(args)
    pipe = InsightsPipeline()
    for result in pipe.run(paced(_events(args), args.speed)):
        for cue in viewer_cues(result, viewer):
            if cue.template != "score_bug":
                print(_cue_line(cue), flush=True)
    if args.json:
        print(json.dumps(pipe.state.snapshot(), indent=1, default=str))


def cmd_live(args) -> None:
    asyncio.run(_live(args))


async def _live(args) -> None:
    from .agents import AgentEngine, FoundrySettings, make_chat_client

    settings = FoundrySettings.from_env()
    offline = args.offline or not settings.configured
    client = make_chat_client(settings, offline=offline, **({"latency_s": args.offline_latency} if offline else {}))
    if offline:
        print("agents: offline stand-in (set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL in .env to use Foundry)")
    else:
        print(f"agents: Microsoft Foundry, model {settings.model}")
    viewer = _viewer(args)
    locales = tuple(x.strip() for x in args.locales.split(",") if x.strip())
    pipe = InsightsPipeline()

    def show(moment, outcome, cues) -> None:
        if not should_show(moment, viewer):
            return
        focus = viewer.mode == "player_focus" and moment.player_id == viewer.focus_player_id
        persona = "player_focus" if focus else viewer.template_persona
        for cue in cues:
            if cue.persona == persona:
                tag = f"AGENT +{cue.slots['latency_ms'] / 1000:.1f}s {cue.locale} "
                print(_cue_line(cue, tag), flush=True)
        if args.verbose:
            for issue in outcome.issues:
                print(f"{moment.minute_label:>7}  CHECK {issue}", flush=True)

    async with AgentEngine(
        client, pipe.state, locales=locales, focus_player_ids=(args.player,) if args.player else (),
        min_significance=args.min_significance, concurrency=args.concurrency, timeout_s=args.timeout,
        max_stale_s=args.max_stale if args.speed > 0 else float("inf"), on_outcome=show,
    ) as engine:
        async for event in apaced(_events(args), args.speed):
            engine.clock = event.elapsed_s
            result = pipe.process(event)
            for cue in viewer_cues(result, viewer):
                if cue.template != "score_bug":
                    print(_cue_line(cue, "FAST  "), flush=True)
            for moment in result.moments:
                engine.submit(moment)
            await asyncio.sleep(0)
    print()
    print("agent engine:", json.dumps(engine.metrics.summary(), indent=1))


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


def cmd_graph(args) -> None:
    from agent_framework import WorkflowViz

    from .agents.factcheck import FactChecker
    from .agents.offline import OfflineChatClient
    from .agents.workflow import build_workflow

    print(WorkflowViz(build_workflow(OfflineChatClient(), FactChecker({}, {}))).to_mermaid())


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

    p = sub.add_parser("live", help="replay a match with the agent layer: fast cues now, agent cues when ready")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--file", help="replay a JSONL file instead of simulating")
    p.add_argument("--speed", type=float, default=0, help="1 = real time, 20 = 20x, 0 = instant (no staleness)")
    p.add_argument("--mode", choices=["casual", "analyst", "player_focus"], default="casual")
    p.add_argument("--team", help="favourite team id, e.g. KBY")
    p.add_argument("--player", help="favourite / focus player id, e.g. KBY-07")
    p.add_argument("--locales", default="en", help="comma-separated languages, e.g. en,es,af")
    p.add_argument("--offline", action="store_true", help="use the offline stand-in even if Foundry is configured")
    p.add_argument("--offline-latency", type=float, default=0.0, help="simulated model latency in seconds")
    p.add_argument("--min-significance", type=float, default=0.4)
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--timeout", type=float, default=20.0, help="per-agent timeout in seconds")
    p.add_argument("--max-stale", type=float, default=90.0, help="drop moments older than this (match seconds)")
    p.add_argument("--verbose", action="store_true", help="print fact-check issues and fallbacks")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("stats", help="print the box score and leaders")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--file")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("graph", help="print the agent workflow graph as Mermaid")
    p.set_defaults(func=cmd_graph)

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
