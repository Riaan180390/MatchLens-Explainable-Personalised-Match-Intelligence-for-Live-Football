# MatchLens

**Explainable, personalised match intelligence for live football.**

MatchLens ingests a live stream of synthetic football events and turns it into insights that explain *why* a moment matters, timed overlay cues a broadcast graphics engine can render, and recaps. The same intelligence is presented differently for an analyst, a casual fan, or someone following one player all match.

Built for the *Inside the Game* developer hackathon (challenge: *The Synthetic Match Insights Engine*). All data is synthetic. The clubs and players are fictional.

## The pipeline

```mermaid
flowchart LR
    SIM[Match simulator<br/>synthetic events] -->|a. ingest| STATS[Stats engine<br/>live match state]
    STATS -->|b. interpret| DET[Moment detector<br/>significance + facts]
    DET -->|fast path, ms| FAST[Templated overlay cues]
    DET -->|agent path, seconds| AG[Agents<br/>analyst / fact-checker / persona / recap]
    AG -->|c. explain| CUES[Overlay cues<br/>JSON, timed]
    FAST -->|d. render| CUES
    CUES -->|e. personalise| VIEW[Viewer feeds<br/>casual / analyst / player focus / language]
```

| Stage | What happens | Where |
|---|---|---|
| a. Ingest | Events arrive one at a time: passes, carries, dribbles, shots, pressure, tackles, interceptions, possession changes, set pieces, sprints, physical tracking and match metadata | `simulator.py`, `replay.py` |
| b. Interpret | Deterministic stats on every event: pass length, accuracy and **0–10 pass difficulty**, expected completion, ball and shot speed, xG, threat added, possession, field tilt, PPDA, momentum and a **control-vs-chaos index** | `stats.py`, `geometry.py` |
| c. Explain | A moment detector scores significance and attaches the **facts** that justify it (for example "pass difficulty 8.6/10, 2 opponents in the lane, 34% expected completion"). Agents then explain why it matters, and a fact-checker holds them to those facts. | `moments.py`, `agents/` |
| d. Render | Timed, machine-readable overlay cues with a JSON Schema: template, screen region, start, duration, priority. A scheduler stops two cues fighting over one region. | `overlays.py`, `schemas/` |
| e. Personalise | Viewer profiles cover mode (casual, analyst, player focus), favourite club and players, focus metric and locale. Relevance scoring decides what each viewer sees. | `personas.py`, `pipeline.py` |

**Two speeds.** The templated fast path means the screen never waits on a model. The agent layer enriches the same moments a few seconds later and its cues replace the templated ones (`replaces_cue_id`). If an agent is slow, wrong or down, viewers still get the templated cue.

## The agent layer

Each significant moment runs through a workflow built with **Microsoft Agent Framework**, with models served from **Microsoft Foundry**. The graph below is generated from the code (`matchlens graph`):

```mermaid
flowchart TD
  router["router (Start)"];
  recap["recap"];
  analyst["analyst"];
  fact_checker["fact_checker"];
  fallback["fallback"];
  persona["persona"];
  publisher["publisher"];
  router --> recap;
  router --> analyst;
  analyst --> fact_checker;
  analyst --> fallback;
  recap --> fact_checker;
  recap --> fallback;
  fact_checker --> persona;
  fact_checker --> analyst;
  fact_checker --> recap;
  fact_checker --> fallback;
  persona --> publisher;
```

| Agent | Kind | Job |
|---|---|---|
| **router** | code | Puts the moment, its match context and the requested viewer targets into shared workflow state. Sends recaps to the recap agent and everything else to the analyst. |
| **analyst** | LLM | Explains *why* the moment matters (score state, momentum, control vs chaos, pressure), using only the facts and context it was given. Returns structured JSON. |
| **recap** | LLM | Writes half-time and full-time recaps and picks a player of the match. |
| **fact_checker** | code | Every number must trace back to a fact or the match context, no player outside the context may be named, and claims beyond this match ("this season", "career") are rejected. A failed draft goes back to the agent that wrote it, with the issues, for one revision. |
| **persona** | LLM | Rewrites the verified insight for each viewer target (casual, analyst, player focus) in each requested language, in one call. |
| **publisher** | code | Fact-checks every variant again (rewriting and translating can drift) and publishes the ones that pass. |
| **fallback** | code | Model error, timeout, or a draft still wrong after revision: nothing is published and the templated cue stays on screen. |

**Built for live play**

- **Priority queue.** Moments are queued by significance and handled by a small worker pool. Low-significance moments stay on the fast path.
- **Freshness.** Moments that would arrive too late are dropped, except goals and recaps.
- **Timeouts.** Every agent call has a hard timeout.
- **Graceful degradation.** If the persona agent fails, the verified English insight is published as is.
- **Metrics.** The engine reports latency (p50 and p95), time per agent, revisions, fallbacks and rejected variants.

Every agent cue carries its evidence (the facts it cites) and `verified: true`, so the screen can show *why* alongside *what*.

### Connect to Microsoft Foundry

1. Copy `.env.example` to `.env`, then set `FOUNDRY_PROJECT_ENDPOINT` (Foundry portal > project overview) and `FOUNDRY_MODEL` (your model *deployment* name).
2. Sign in: install the Azure CLI and run `az login`, or set `MATCHLENS_AUTH=browser` for an interactive browser sign-in. Your account needs access to the Foundry project (for example the *Azure AI User* role).
3. Run `matchlens live --seed 2 --speed 20 --locales en,es,af --verbose`.

Without `.env`, `matchlens live` uses a deterministic offline stand-in for the model. The tests use the same stand-in to exercise hallucinations, outages and timeouts.

## Quickstart

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"

matchlens ticker --seed 2 --mode casual                        # live overlay feed for a casual fan
matchlens ticker --seed 2 --mode analyst                       # same match, analyst wording and metrics
matchlens ticker --seed 2 --mode player_focus --player KBY-07  # follow one player all match
matchlens ticker --seed 2 --speed 60                           # replay at 60x (one match minute per second)
matchlens live --seed 2 --locales en,af --verbose             # fast cues + agent cues (offline unless .env is set)
matchlens live --seed 2 --speed 20 --mode analyst               # paced like a live match, analyst persona
matchlens graph                                                # the agent workflow as Mermaid
matchlens stats --seed 2                                       # box score and leaders
matchlens simulate --seed 2                                    # write data/samples/SYN-00002.jsonl
matchlens schema --out schemas                                 # export JSON Schemas for the contracts
matchlens calibrate                                            # realism check over 30 matches
pytest
```

Sample output (casual mode):

```
    50'  [moment_card   ] GOAL! - Zane Mbeki-Foster scores! Ironvale Athletic are in front! Set up by Felix Marangoni. KBY 0-1 IRV
    59'  [moment_card   ] GOAL! - Rafe Delacroix scores! That's the equaliser! Set up by Sid Abernethy. KBY 1-1 IRV
    61'  [player_tag    ] Okonkwo-Reyes - Brilliant pass! Threaded past 1 defender.
```

The same moments in analyst mode:

```
    18'  [player_tag    ] Thwaite - Pass difficulty 8.6/10 | 57.7 m | 34% expected completion
    28'  [stat_bar      ] Game rhythm - Chaos index 65 | 3.2 turnovers/min | 81% passing
```

## Synthetic data that behaves like football

The simulator is a possession-chain model. Whoever has the ball picks an action based on pitch position, the pressure on them (from modelled opponent positions and team pressing intensity) and their team's style. Outcomes come from player attributes and geometry. Each team's intensity drifts and reacts to the score, which produces momentum swings and pressure spells. The same seed always gives the same match.

Averaged over 30 simulated matches (`matchlens calibrate`), per team:

| Metric | Simulated mean | Typical top-flight range |
|---|---|---|
| Goals | 1.75 | 1.0–1.8 |
| Shots / on target | 14.1 / 5.1 | 10–16 / 3–6 |
| Passes | 430 | 380–620 |
| Pass accuracy | 79% | 78–88% |
| Corners | 6.4 | 4–7 |
| Fouls / yellow cards | 10.0 / 1.95 | 9–13 / 1–2.5 |
| Tackles won | 18.0 | 12–20 |
| Actions under pressure | 17% | 15–30% |
| Top distance covered | 12.2 km | 11–13 km |

Event coordinates are metres on a 105 x 68 pitch in the acting team's frame (attacking towards x = 105). `schema.to_absolute()` converts them to a fixed broadcast frame.

## Explainable models

Every number shown to a viewer comes from a transparent formula in `geometry.py` and `stats.py`:

- **xG**: logistic on shot distance and goal-mouth angle, with header and pressure adjustments (about 0.27 from the penalty spot).
- **Pass difficulty (0–10)**: pass length, opponents in the passing lane, pressure on the passer, forward progress, and pass type (through ball, cross, long ball). It maps to an expected-completion probability, giving **passes over expected** per player.
- **Momentum**: decayed attacking threat (threat added plus xG) for each side, from -1 to 1.
- **Control vs chaos**: turnovers per minute, pass completion and duels per minute over the last 5 minutes, labelled *Controlled*, *Contested* or *Chaotic*.
- **Field tilt and PPDA**: share of final-third touches, and opponent passes allowed per defensive action.

## Repository layout

```
src/matchlens/
  schema.py      data contracts: Event, Moment, Fact, OverlayCue
  squads.py      fictional clubs, players and formations
  simulator.py   synthetic match generator
  geometry.py    pitch geometry, xG, threat, pass difficulty
  stats.py       live match state and derived metrics
  moments.py     moment detection with evidence
  overlays.py    fast-path overlay cues and region scheduler
  personas.py    viewer profiles and relevance
  pipeline.py    wires the stages together
  replay.py      JSONL I/O and real-time replay
  calibrate.py   realism check against real-world ranges
  cli.py         command line
  agents/
    workflow.py  the Agent Framework workflow: router, analyst, recap, fact_checker, persona, publisher, fallback
    engine.py    priority queue, workers, freshness, timeouts, metrics, agent overlay cues
    factcheck.py deterministic verification of numbers, names and claims
    context.py   what the agents may know about a moment
    prompts.py   agent instructions
    contracts.py structured outputs and workflow messages
    config.py    Foundry settings and chat client factory
    offline.py   deterministic stand-in for the model (dev, tests, CI)
schemas/         JSON Schemas for the contracts
data/samples/    a sample match stream
tests/
```

## Roadmap (hackathon week)

- [x] Synthetic match simulator, calibrated
- [x] Stats engine, moment detector, fast-path overlay cues, viewer profiles
- [x] Agent workflow with Microsoft Agent Framework: analyst, recap, fact-checker, persona (multilingual), publisher, fallback
- [ ] First end-to-end run against a Foundry model deployment
- [ ] FastAPI service with real-time push (Azure Web PubSub) and a React overlay demo on a pitch view
- [ ] Azure deployment: Event Hubs ingest, Container Apps, Cosmos DB, Application Insights, `azd up`
- [ ] Stretch: auto-eventing from video, voice commentary with Azure AI Speech
