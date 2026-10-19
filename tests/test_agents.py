import asyncio

import pytest

from matchlens.agents import AgentEngine, make_chat_client
from matchlens.agents.factcheck import FactChecker
from matchlens.pipeline import InsightsPipeline
from matchlens.simulator import MatchSimulator

ROSTER = {"Quist": "Amari Quist", "Halvik": "Teodor Halvik"}
CONTEXT = {
    "score": "Kestrel Bay FC 1-0 Ironvale Athletic",
    "teams": {"Kestrel Bay FC": {"possession_pct": 57.3, "xg": 1.32}},
    "moment": {"player": "Amari Quist", "facts": [{"name": "top_speed_kmh", "value": 35.5, "unit": "km/h"},
                                                  {"name": "share", "value": 0.42}]},
}


def test_factcheck_accepts_supported_numbers():
    fc = FactChecker(CONTEXT, ROSTER)
    assert fc.check("Quist hit 35.5 km/h with 57% possession and 1.3 xG at 1-0.").passed
    assert fc.check("That is 42% of the touches.").passed  # 0.42 written as a percentage


def test_factcheck_rejects_invented_numbers_history_and_outsiders():
    fc = FactChecker(CONTEXT, ROSTER)
    r = fc.check("Quist hit 37.2 km/h, his fastest this season, and Halvik loved it.")
    assert not r.passed
    assert any("37.2" in i for i in r.issues)
    assert any("season" in i for i in r.issues)
    assert any("Teodor Halvik" in i for i in r.issues)


async def _run_match(seed: int = 2, limit: int | None = None, **engine_kwargs):
    client_kwargs = engine_kwargs.pop("client_kwargs", {})
    pipe = InsightsPipeline()
    outcomes = []
    engine = AgentEngine(make_chat_client(offline=True, **client_kwargs), pipe.state,
                         max_stale_s=float("inf"), on_outcome=lambda m, o, c: outcomes.append((m, o, c)),
                         **engine_kwargs)
    async with engine:
        submitted = 0
        for e in MatchSimulator(seed).run():
            engine.clock = e.elapsed_s
            for m in pipe.process(e).moments:
                if limit is None or submitted < limit:
                    submitted += engine.submit(m)
    return pipe, engine, outcomes


@pytest.fixture(scope="module")
def happy_run():
    return asyncio.run(_run_match(locales=("en", "af"), focus_player_ids=("KBY-07",)))


def test_every_submitted_moment_is_published(happy_run):
    _, engine, outcomes = happy_run
    s = engine.metrics.summary()
    assert s["submitted"] == len(outcomes) == s["published"] > 0
    assert s["fallback"] == 0


def test_agent_cues_replace_fast_cues_in_every_locale(happy_run):
    _, _, outcomes = happy_run
    for m, o, cues in outcomes:
        assert {(c.persona, c.locale) for c in cues} >= {("casual", "en"), ("analyst", "en"), ("casual", "af")}
        for c in cues:
            assert c.source == "agent" and c.slots["verified"]
            assert c.start_s >= m.elapsed_s
            if c.persona in ("casual", "analyst"):
                assert c.replaces_cue_id == f"{m.moment_id}-{c.persona}"


def test_player_focus_variants_only_for_that_player(happy_run):
    _, _, outcomes = happy_run
    focus = [(m, c) for m, _, cues in outcomes for c in cues if c.persona == "player_focus"]
    assert focus and all(m.player_id == "KBY-07" for m, _ in focus)


def test_recaps_name_a_player_of_the_match(happy_run):
    _, _, outcomes = happy_run
    recaps = [o for m, o, _ in outcomes if m.kind in ("half_time", "full_time")]
    assert len(recaps) == 2 and all(o.insight.player_of_the_match for o in recaps)


def test_hallucinated_claims_are_caught_and_revised():
    _, engine, outcomes = asyncio.run(_run_match(limit=8, client_kwargs={"hallucinate_first_draft": True}))
    s = engine.metrics.summary()
    assert s["revised_after_fact_check"] > 0
    assert s["published"] == s["submitted"]
    for _, o, cues in outcomes:
        assert all("season" not in c.slots["body"] and "17" not in c.slots["body"] for c in cues)
    revised = [o for _, o, _ in outcomes if o.attempts > 1]
    assert all(any("season" in i for i in o.issues) for o in revised)


def test_analyst_outage_falls_back_without_cues():
    _, engine, outcomes = asyncio.run(_run_match(limit=5, client_kwargs={"fail_roles": ("analyst", "recap")}))
    assert engine.metrics.published == 0 and engine.metrics.fallback == len(outcomes) > 0
    assert all(not cues for _, _, cues in outcomes)


def test_persona_outage_degrades_to_verified_english():
    _, engine, outcomes = asyncio.run(_run_match(limit=5, locales=("en", "es"),
                                                 client_kwargs={"fail_roles": ("persona",)}))
    assert engine.metrics.published == len(outcomes) > 0
    for _, o, cues in outcomes:
        assert {c.locale for c in cues} == {"en"}
        assert any("persona agent unavailable" in i for i in o.issues)


def test_slow_model_times_out_to_fallback():
    _, engine, outcomes = asyncio.run(_run_match(limit=2, timeout_s=0.05, client_kwargs={"latency_s": 0.3}))
    assert engine.metrics.fallback == len(outcomes) == 2
    assert all(any("TimeoutError" in i for i in o.issues) for _, o, _ in outcomes)


def test_stale_moments_are_dropped_but_goals_are_not():
    async def run():
        pipe = InsightsPipeline()
        engine = AgentEngine(make_chat_client(offline=True), pipe.state, max_stale_s=30)
        async with engine:
            for e in MatchSimulator(2).run():
                engine.clock = e.elapsed_s
                for m in pipe.process(e).moments:
                    engine.submit(m)
            engine.clock = 1e9  # the workers only start now, so everything is old
        return engine
    engine = asyncio.run(run())
    assert engine.metrics.dropped_stale > 0
    assert engine.metrics.published >= 3 + 2  # this match's goals plus both recaps still go out
