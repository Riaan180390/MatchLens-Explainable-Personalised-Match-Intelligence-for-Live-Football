import pytest

from matchlens import geometry as geo
from matchlens.schema import EventType as T


def test_xg_rises_closer_to_goal():
    assert geo.xg((99, 34)) > geo.xg((94, 34)) > geo.xg((85, 34)) > geo.xg((75, 34))
    assert 0.2 < geo.xg((94, 34)) < 0.35  # penalty-spot range for open play
    assert geo.xg((94, 34), header=True) < geo.xg((94, 34))


def test_pass_difficulty_components():
    easy = geo.pass_difficulty(10, 0, False, 8, 0, "ground")
    harder = geo.pass_difficulty(10, 2, False, 8, 0, "ground")
    pressured = geo.pass_difficulty(10, 2, True, 1.5, 0, "ground")
    through = geo.pass_difficulty(35, 2, True, 1.5, 25, "through_ball")
    assert easy < harder < pressured < through <= 10
    assert geo.expected_pass_completion(easy) > geo.expected_pass_completion(through)


def test_team_totals_match_raw_events(match, processed):
    _, events = match
    pipe, _ = processed
    for tid, team in pipe.state.teams.items():
        passes = [e for e in events if e.type == T.PASS and e.team_id == tid]
        assert team.passes_att == len(passes)
        assert team.passes_cmp == sum(e.outcome == "complete" for e in passes)
        assert team.shots == sum(e.type == T.SHOT and e.team_id == tid for e in events)
        assert team.goals == events[-1].data["score"][tid]


def test_possession_adds_up(processed):
    pipe, _ = processed
    total = sum(pipe.state.team_summary(t)["possession_pct"] for t in pipe.state.teams)
    assert total == pytest.approx(100, abs=0.2)


def test_player_goals_sum_to_team_goals(processed):
    pipe, _ = processed
    for tid, team in pipe.state.teams.items():
        assert sum(p.goals for p in pipe.state.players.values() if p.team_id == tid) == team.goals


def test_rhythm_and_momentum_bounded(processed):
    pipe, _ = processed
    r = pipe.state.rhythm()
    assert 0 <= r["chaos"] <= 100
    assert r["label"] in ("Controlled", "Contested", "Chaotic")
    assert all(-1 <= m <= 1 for _, m in pipe.state.momentum_history)
