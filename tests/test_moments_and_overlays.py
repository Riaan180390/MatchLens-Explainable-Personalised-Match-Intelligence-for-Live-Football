from pathlib import Path

from matchlens.personas import ALWAYS_SHOW, ViewerProfile, should_show
from matchlens.pipeline import viewer_cues
from matchlens.replay import read_jsonl, write_jsonl
from matchlens.schema import EventType as T, OverlayCue


def test_one_goal_moment_per_goal(match, processed):
    _, events = match
    pipe, _ = processed
    goals = [e for e in events if e.type == T.SHOT and e.outcome == "goal"]
    goal_moments = [m for m in pipe.moments if m.kind == "goal"]
    assert [m.seq for m in goal_moments] == [e.seq for e in goals]


def test_every_moment_carries_evidence(processed):
    pipe, _ = processed
    assert pipe.moments
    for m in pipe.moments:
        assert m.facts, m.kind
        assert 0 <= m.significance <= 1


def test_recaps_fire_once_each(processed):
    pipe, _ = processed
    kinds = [m.kind for m in pipe.moments]
    assert kinds.count("half_time") == 1 and kinds.count("full_time") == 1


def test_cues_are_valid_and_scheduled(processed):
    _, results = processed
    cues = [c for r in results for c in r.cues]
    assert cues
    showing: dict[tuple[str, str], OverlayCue] = {}
    for c in cues:
        OverlayCue.model_validate(c.model_dump())
        prev = showing.get((c.persona, c.region))
        if prev and c.template != "score_bug" and prev.start_s + prev.duration_s > c.start_s:
            assert c.replaces_cue_id == prev.cue_id, "overlapping cues in one region must pre-empt explicitly"
        showing[(c.persona, c.region)] = c


def test_personas_get_different_wording(processed):
    _, results = processed
    by_moment: dict[str, dict[str, str]] = {}
    for r in results:
        for c in r.cues:
            if c.persona in ("casual", "analyst") and c.template != "recap_card":
                by_moment.setdefault(c.moment_id, {})[c.persona] = c.slots["body"]
    pairs = [v for v in by_moment.values() if len(v) == 2]
    assert pairs and all(v["casual"] != v["analyst"] for v in pairs)


def test_player_focus_only_follows_that_player(processed):
    pipe, results = processed
    focus = "KBY-07"
    viewer = ViewerProfile(mode="player_focus", focus_player_id=focus)
    kinds = {m.moment_id: m for m in pipe.moments}
    for r in results:
        for c in viewer_cues(r, viewer):
            if c.persona == "all":
                continue
            m = kinds[c.moment_id]
            assert m.player_id == focus or m.kind in ALWAYS_SHOW


def test_favourite_team_raises_relevance(processed):
    pipe, _ = processed
    neutral = ViewerProfile(mode="casual")
    fan = ViewerProfile(mode="casual", favourite_team_id="KBY")
    extra = [m for m in pipe.moments if should_show(m, fan) and not should_show(m, neutral)]
    assert extra and all(m.team_id == "KBY" for m in extra)


def test_jsonl_round_trip(match, tmp_path: Path):
    _, events = match
    path = tmp_path / "m.jsonl"
    assert write_jsonl(events, path) == len(events)
    assert [e.model_dump(exclude_none=True) for e in read_jsonl(path)] == \
           [e.model_dump(exclude_none=True) for e in events]
