"""Wires the deterministic stages together: event -> insight -> moments -> overlay cues."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from .moments import MomentDetector
from .overlays import CueScheduler, cues_for_moment
from .personas import MIN_SIGNIFICANCE, ViewerProfile, should_show
from .schema import Event, Moment, OverlayCue
from .stats import EventInsight, MatchState


@dataclass
class StepResult:
    event: Event
    insight: EventInsight
    moments: list[Moment] = field(default_factory=list)
    cues: list[OverlayCue] = field(default_factory=list)


class InsightsPipeline:
    """Fast path of the engine. Agents subscribe to `moments` and enrich them asynchronously."""

    def __init__(self) -> None:
        self.state = MatchState()
        self.detector = MomentDetector(self.state)
        self.scheduler = CueScheduler()
        self.moments: list[Moment] = []
        self._floor = min(MIN_SIGNIFICANCE.values())

    def process(self, event: Event) -> StepResult:
        insight = self.state.apply(event)
        result = StepResult(event, insight, self.detector.observe(insight))
        self.moments += result.moments
        for m in result.moments:
            for cue in cues_for_moment(m, self.state):
                placed = self.scheduler.place(cue)
                if placed:
                    result.cues.append(placed)
        return result

    def run(self, events: Iterable[Event]) -> Iterator[StepResult]:
        for e in events:
            yield self.process(e)


def viewer_cues(result: StepResult, viewer: ViewerProfile) -> list[OverlayCue]:
    """The cues one viewer should see for this step."""
    shown = {m.moment_id for m in result.moments if should_show(m, viewer)}
    return [c for c in result.cues
            if c.persona in ("all", viewer.template_persona) and (c.moment_id in shown or c.persona == "all")]
