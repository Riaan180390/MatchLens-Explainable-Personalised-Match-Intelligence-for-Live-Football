import pytest

from matchlens.pipeline import InsightsPipeline
from matchlens.simulator import MatchSimulator


@pytest.fixture(scope="session")
def match():
    sim = MatchSimulator(seed=2)
    return sim.meta, list(sim.run())


@pytest.fixture(scope="session")
def processed(match):
    _, events = match
    pipe = InsightsPipeline()
    results = list(pipe.run(events))
    return pipe, results
