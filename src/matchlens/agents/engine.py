"""Runs the agent workflow alongside a live match without ever blocking the fast path.

Moments are queued by significance and handled by a small pool of workers.
Anything that would arrive too late to be useful is dropped (except goals and
recaps), every run has a hard timeout, and failures fall back to the templated
cues already on screen.
"""

from __future__ import annotations

import asyncio
import itertools
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agent_framework import BaseChatClient

from ..overlays import DURATION, PRIORITY, REGION, template_for
from ..personas import ALWAYS_SHOW
from ..schema import Moment, OverlayCue
from ..stats import MatchState
from .context import build_context, roster_names
from .contracts import MomentJob, Outcome
from .factcheck import FactChecker
from .workflow import build_workflow

OutcomeCallback = Callable[[Moment, Outcome, list[OverlayCue]], Awaitable[None] | None]


@dataclass
class EngineMetrics:
    submitted: int = 0
    filtered: int = 0
    dropped_stale: int = 0
    published: int = 0
    fallback: int = 0
    revised: int = 0
    rejected_variants: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    stage_ms: dict[str, list[float]] = field(default_factory=dict)

    def summary(self) -> dict:
        lat = sorted(self.latencies_ms)
        pct = lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 1) if lat else None  # noqa: E731
        return {
            "submitted": self.submitted, "filtered_low_significance": self.filtered,
            "dropped_stale": self.dropped_stale, "published": self.published, "fallback": self.fallback,
            "revised_after_fact_check": self.revised, "rejected_variants": self.rejected_variants,
            "latency_ms_p50": pct(0.5), "latency_ms_p95": pct(0.95),
            "stage_ms_mean": {k: round(statistics.mean(v), 1) for k, v in self.stage_ms.items() if v},
        }


class AgentEngine:
    def __init__(self, client: BaseChatClient, state: MatchState, *,
                 personas: tuple[str, ...] = ("casual", "analyst"), locales: tuple[str, ...] = ("en",),
                 focus_player_ids: tuple[str, ...] = (), min_significance: float = 0.4,
                 concurrency: int = 3, timeout_s: float = 20.0, max_stale_s: float = 90.0,
                 on_outcome: OutcomeCallback | None = None):
        self.client, self.state = client, state
        self.personas, self.locales = personas, locales
        self.focus_player_ids = set(focus_player_ids)
        self.min_significance = min_significance
        self.concurrency, self.timeout_s, self.max_stale_s = concurrency, timeout_s, max_stale_s
        self.on_outcome = on_outcome
        self.metrics = EngineMetrics()
        self.clock = 0.0  # current match time (Event.elapsed_s), set by the caller
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._workers: list[asyncio.Task] = []
        self._recent: list[Moment] = []

    async def __aenter__(self) -> AgentEngine:
        self._workers = [asyncio.create_task(self._worker()) for _ in range(self.concurrency)]
        return self

    async def __aexit__(self, *exc) -> None:
        await self.drain()
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    async def drain(self) -> None:
        await self._queue.join()

    # ---------------------------------------------------------------- input

    def targets_for(self, moment: Moment) -> list[tuple[str, str]]:
        personas = list(self.personas)
        if moment.player_id in self.focus_player_ids:
            personas.append("player_focus")
        return [(p, loc) for p in personas for loc in self.locales]

    def submit(self, moment: Moment) -> bool:
        """Capture the match context now (it keeps changing) and queue the moment for the agents."""
        always = moment.kind in ALWAYS_SHOW
        self._recent.append(moment)
        if not always and moment.significance < self.min_significance:
            self.metrics.filtered += 1
            return False
        job = MomentJob(moment=moment, context=build_context(moment, self.state, self._recent[:-1]),
                        targets=self.targets_for(moment))
        self.metrics.submitted += 1
        priority = -(1.0 if always else moment.significance)
        self._queue.put_nowait((priority, next(self._seq), job, time.perf_counter()))
        return True

    # --------------------------------------------------------------- worker

    async def _worker(self) -> None:
        roster = roster_names(self.state)
        while True:
            _, _, job, queued_at = await self._queue.get()
            try:
                m = job.moment
                if m.kind not in ALWAYS_SHOW and self.clock - m.elapsed_s > self.max_stale_s:
                    self.metrics.dropped_stale += 1
                    continue
                checker = FactChecker(job.context, roster)
                try:
                    result = await asyncio.wait_for(build_workflow(self.client, checker, self.timeout_s).run(job),
                                                    self.timeout_s * 3)
                    outputs = result.get_outputs()
                    outcome = outputs[-1] if outputs else Outcome(m.moment_id, "fallback", issues=["no output"])
                except Exception as e:  # including timeouts: the fast-path cue simply stays up
                    outcome = Outcome(m.moment_id, "fallback", issues=[f"engine: {type(e).__name__}: {e}"[:300]])
                latency_ms = round((time.perf_counter() - queued_at) * 1000, 1)
                self._record(outcome, latency_ms)
                cues = self.cues_for(m, outcome, latency_ms)
                if self.on_outcome:
                    r = self.on_outcome(m, outcome, cues)
                    if asyncio.iscoroutine(r):
                        await r
            finally:
                self._queue.task_done()

    def _record(self, outcome: Outcome, latency_ms: float) -> None:
        mt = self.metrics
        if outcome.status == "published":
            mt.published += 1
            mt.latencies_ms.append(latency_ms)
        else:
            mt.fallback += 1
        if outcome.attempts > 1:
            mt.revised += 1
        mt.rejected_variants += len(outcome.rejected)
        for stage, ms in outcome.timings_ms.items():
            mt.stage_ms.setdefault(stage, []).append(ms)

    # --------------------------------------------------------------- output

    def cues_for(self, m: Moment, outcome: Outcome, latency_ms: float) -> list[OverlayCue]:
        if outcome.status != "published" or not outcome.insight:
            return []
        template = template_for(m.kind)
        team = self.state.teams.get(m.team_id or "")
        player = self.state.players.get(m.player_id or "")
        cited = set(outcome.insight.cited_facts)
        evidence = [f.model_dump(exclude_none=True) for f in m.facts if not cited or f.name in cited]
        cues = []
        for v in outcome.variants:
            cues.append(OverlayCue(
                cue_id=f"{m.moment_id}-{v.persona}-{v.locale}-agent", match_id=m.match_id, moment_id=m.moment_id,
                template=template, region=REGION[template], start_s=round(max(m.elapsed_s, self.clock), 2),
                duration_s=DURATION[template] + (0 if template == "recap_card" else 3),
                priority=PRIORITY.get(m.kind, 20), persona=v.persona, locale=v.locale, source="agent",
                replaces_cue_id=f"{m.moment_id}-{v.persona}" if v.persona in ("casual", "analyst") else None,
                slots={
                    "kind": m.kind, "minute": m.minute_label, "title": v.title, "body": v.body,
                    "why": outcome.insight.why_it_matters, "angle": outcome.insight.angle,
                    "evidence": evidence, "verified": True, "latency_ms": latency_ms,
                    "team_id": m.team_id, "team_name": team.name if team else None,
                    "team_color": team.color if team else None,
                    "player_id": m.player_id, "player_name": player.name if player else None,
                    "number": player.number if player else None,
                    "player_of_the_match": outcome.insight.player_of_the_match,
                },
            ))
        return cues
