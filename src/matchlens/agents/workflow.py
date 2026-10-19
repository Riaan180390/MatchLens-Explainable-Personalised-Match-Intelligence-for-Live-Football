r"""The per-moment multi-agent workflow, built with Microsoft Agent Framework.

    router --(recap kinds)--> recap ---+
       \--(everything else)--> analyst-+--> fact_checker --(verified)--> persona --> publisher
                                 ^   ^          |
                                 |   +-(revise)-+   (one revision, sent back to whichever agent drafted)
                                 +-------(failed: model error, timeout, or still wrong)--> fallback

Agents share the job (moment, context, targets) through workflow state. The
fact-checker is deliberately not an LLM: it holds every number and name to the
match data, so a wrong claim never reaches the screen.
"""

import asyncio
import json
import time
from typing import Never

from agent_framework import Agent, BaseChatClient, Case, Default, Executor, WorkflowBuilder, WorkflowContext, handler

from . import prompts
from .context import RECAP_KINDS
from .contracts import (Draft, Failed, Insight, MomentJob, Outcome, Revise, Variant, VariantBundle, Variants,
                        Verified)
from .factcheck import FactChecker

MAX_REVISIONS = 1


# Workflow state hands out copies, so every update is written back with set_state.
def _record_ms(ctx: WorkflowContext, stage: str, start: float) -> None:
    timings = dict(ctx.get_state("timings") or {})
    timings[stage] = round(timings.get(stage, 0.0) + (time.perf_counter() - start) * 1000, 1)
    ctx.set_state("timings", timings)


def _note(ctx: WorkflowContext, issues: list[str]) -> None:
    ctx.set_state("issues", list(ctx.get_state("issues") or []) + issues)


class Router(Executor):
    @handler
    async def route(self, job: MomentJob, ctx: WorkflowContext[MomentJob]) -> None:
        ctx.set_state("job", job)
        ctx.set_state("timings", {})
        ctx.set_state("issues", [])
        await ctx.send_message(job)


class DraftingAgent(Executor):
    """Wraps an Agent Framework agent that drafts an Insight (the analyst or the recap writer)."""

    def __init__(self, id: str, client: BaseChatClient, instructions: str, timeout_s: float):
        super().__init__(id=id)
        self.agent = Agent(client=client, name=id, instructions=instructions,
                           default_options={"response_format": Insight})
        self.timeout_s = timeout_s

    async def _draft(self, payload: dict, attempt: int, ctx: WorkflowContext[Draft | Failed]) -> None:
        start = time.perf_counter()
        try:
            response = await asyncio.wait_for(self.agent.run(json.dumps(payload, default=str)), self.timeout_s)
            insight = response.value if isinstance(response.value, Insight) else Insight.model_validate_json(response.text)
        except Exception as e:  # model outage, timeout, malformed output: all end in the fallback
            _record_ms(ctx, self.id, start)
            await ctx.send_message(Failed(stage=self.id, reason=f"{type(e).__name__}: {e}"[:300], attempts=attempt))
            return
        _record_ms(ctx, self.id, start)
        await ctx.send_message(Draft(author=self.id, insight=insight, attempt=attempt))

    @handler
    async def on_job(self, job: MomentJob, ctx: WorkflowContext[Draft | Failed]) -> None:
        await self._draft({"context": job.context}, 1, ctx)

    @handler
    async def on_revise(self, rev: Revise, ctx: WorkflowContext[Draft | Failed]) -> None:
        job: MomentJob = ctx.get_state("job")
        await self._draft({"context": job.context, "previous_draft": rev.previous.model_dump(),
                           "revision_issues": rev.issues}, rev.attempt, ctx)


class FactCheckerExecutor(Executor):
    def __init__(self, checker: FactChecker):
        super().__init__(id="fact_checker")
        self.checker = checker

    @handler
    async def check(self, draft: Draft, ctx: WorkflowContext[Verified | Revise | Failed]) -> None:
        ins = draft.insight
        result = self.checker.check(ins.headline, ins.why_it_matters, ins.player_of_the_match or "")
        if result.passed:
            await ctx.send_message(Verified(insight=ins, attempts=draft.attempt))
            return
        _note(ctx, [f"draft {draft.attempt}: {i}" for i in result.issues])
        if draft.attempt <= MAX_REVISIONS:
            await ctx.send_message(Revise(author=draft.author, previous=ins, issues=result.issues,
                                          attempt=draft.attempt + 1))
        else:
            await ctx.send_message(Failed(stage="fact_checker", reason="; ".join(result.issues),
                                          attempts=draft.attempt))


class PersonaAgent(Executor):
    def __init__(self, client: BaseChatClient, timeout_s: float):
        super().__init__(id="persona")
        self.agent = Agent(client=client, name="persona", instructions=prompts.PERSONA,
                           default_options={"response_format": VariantBundle})
        self.timeout_s = timeout_s

    @handler
    async def adapt(self, verified: Verified, ctx: WorkflowContext[Variants]) -> None:
        job: MomentJob = ctx.get_state("job")
        payload = {
            "insight": verified.insight.model_dump(),
            "facts": job.context["moment"]["facts"],
            "player": job.context["moment"].get("player"),
            "targets": [{"persona": p, "locale": loc} for p, loc in job.targets],
        }
        start = time.perf_counter()
        try:
            response = await asyncio.wait_for(self.agent.run(json.dumps(payload, default=str)), self.timeout_s)
            bundle = (response.value if isinstance(response.value, VariantBundle)
                      else VariantBundle.model_validate_json(response.text))
            variants = bundle.variants
        except Exception as e:
            # Degrade rather than drop: the verified English insight still goes out.
            _note(ctx, [f"persona agent unavailable ({type(e).__name__}); using the verified insight"])
            variants = [Variant(persona=p, locale=loc, title=verified.insight.headline[:60],
                                body=verified.insight.why_it_matters)
                        for p, loc in job.targets if loc.startswith("en")]
        _record_ms(ctx, self.id, start)
        await ctx.send_message(Variants(insight=verified.insight, variants=variants, attempts=verified.attempts))


class Publisher(Executor):
    """Final gate: every variant is fact-checked again, since rewriting and translating can drift."""

    def __init__(self, checker: FactChecker):
        super().__init__(id="publisher")
        self.checker = checker

    @handler
    async def publish(self, v: Variants, ctx: WorkflowContext[Never, Outcome]) -> None:
        job: MomentJob = ctx.get_state("job")
        wanted = set(job.targets)
        ok, rejected = [], []
        for var in v.variants:
            if (var.persona, var.locale) not in wanted:
                continue
            result = self.checker.check(var.title, var.body)
            (ok if result.passed else rejected).append(var if result.passed else (var, result.issues))
        await ctx.yield_output(Outcome(
            moment_id=job.moment.moment_id, status="published" if ok else "fallback", insight=v.insight,
            variants=ok, rejected=rejected, attempts=v.attempts, issues=list(ctx.get_state("issues")),
            timings_ms=dict(ctx.get_state("timings"))))


class Fallback(Executor):
    @handler
    async def fallback(self, failed: Failed, ctx: WorkflowContext[Never, Outcome]) -> None:
        job: MomentJob = ctx.get_state("job")
        await ctx.yield_output(Outcome(
            moment_id=job.moment.moment_id, status="fallback", attempts=failed.attempts,
            issues=list(ctx.get_state("issues")) + [f"{failed.stage}: {failed.reason}"],
            timings_ms=dict(ctx.get_state("timings"))))


def build_workflow(client: BaseChatClient, checker: FactChecker, timeout_s: float = 15.0):
    """A fresh workflow per moment (workflow instances run one job at a time; agents are cheap)."""
    router = Router(id="router")
    analyst = DraftingAgent("analyst", client, prompts.ANALYST, timeout_s)
    recap = DraftingAgent("recap", client, prompts.RECAP, timeout_s)
    fact_checker = FactCheckerExecutor(checker)
    persona = PersonaAgent(client, timeout_s)
    publisher = Publisher(checker)
    fallback = Fallback(id="fallback")

    is_ = lambda t: (lambda m: isinstance(m, t))  # noqa: E731
    return (
        WorkflowBuilder(name="matchlens-moment", start_executor=router)
        .add_switch_case_edge_group(router, [
            Case(condition=lambda job: job.moment.kind in RECAP_KINDS, target=recap),
            Default(target=analyst),
        ])
        .add_switch_case_edge_group(analyst, [Case(condition=is_(Draft), target=fact_checker), Default(target=fallback)])
        .add_switch_case_edge_group(recap, [Case(condition=is_(Draft), target=fact_checker), Default(target=fallback)])
        .add_switch_case_edge_group(fact_checker, [
            Case(condition=is_(Verified), target=persona),
            Case(condition=lambda m: isinstance(m, Revise) and m.author == "analyst", target=analyst),
            Case(condition=lambda m: isinstance(m, Revise) and m.author == "recap", target=recap),
            Default(target=fallback),
        ])
        .add_edge(persona, publisher)
        .build()
    )
