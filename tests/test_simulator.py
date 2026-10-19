from matchlens.schema import PITCH_LENGTH, PITCH_WIDTH, EventType as T
from matchlens.simulator import simulate


def test_same_seed_same_match():
    _, a = simulate(11)
    _, b = simulate(11)
    assert [e.model_dump() for e in a] == [e.model_dump() for e in b]


def test_different_seeds_differ():
    _, a = simulate(11)
    _, b = simulate(12)
    assert [e.model_dump() for e in a] != [e.model_dump() for e in b]


def test_stream_is_ordered(match):
    _, events = match
    assert [e.seq for e in events] == list(range(len(events)))
    assert all(b.elapsed_s >= a.elapsed_s for a, b in zip(events, events[1:]))
    assert events[0].type == T.MATCH_START and events[-1].type == T.MATCH_END
    assert [e.period for e in events if e.type == T.PERIOD_END] == [1, 2]


def test_score_matches_goal_events(match):
    meta, events = match
    goals = {meta.home.team_id: 0, meta.away.team_id: 0}
    for e in events:
        if e.type == T.SHOT and e.outcome == "goal":
            goals[e.team_id] += 1
    assert events[-1].data["score"] == goals


def test_locations_on_pitch_and_players_known(match):
    meta, events = match
    known = {p.player_id for t in (meta.home, meta.away) for p in t.starting_xi + t.bench}
    for e in events:
        for pt in (e.location, e.end_location):
            if pt is not None:
                assert 0 <= pt[0] <= PITCH_LENGTH and 0 <= pt[1] <= PITCH_WIDTH, e
        for pid in (e.player_id, e.recipient_id, e.related_player_id):
            assert pid is None or pid in known, e


def test_box_score_is_football_realistic():
    """Averaged over several seeds, the simulator stays inside broad real-world ranges."""
    from matchlens.calibrate import box_scores

    rows = [r for seed in range(1, 9) for r in box_scores(seed)]
    mean = {k: sum(r[k] for r in rows) / len(rows) for k in rows[0]}
    assert 0.8 <= mean["goals"] <= 2.2
    assert 8 <= mean["shots"] <= 18
    assert 330 <= mean["passes"] <= 650
    assert 0.74 <= mean["pass_acc"] <= 0.9
    assert 10.5 <= mean["top_distance_km"] <= 13.5
