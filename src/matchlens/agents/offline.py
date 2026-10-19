"""A deterministic stand-in for the Foundry model.

Lets the full agent workflow run without Azure (local dev, tests, CI) and can
inject latency, outages and hallucinations to exercise the failure paths.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, FunctionInvocationLayer, Message


def _role(options: dict | None) -> str:
    first = str((options or {}).get("instructions") or "").strip().splitlines()[:1]
    return first[0].split(":", 1)[1].strip() if first and first[0].startswith("ROLE:") else "unknown"


def _fact_text(f: dict) -> str:
    unit = f.get("unit") or ""
    return f"{f['value']}{unit if unit in ('%', '/10') else (' ' + unit if unit else '')}"


def _words(text: str, n: int) -> str:
    w = text.split()
    return text if len(w) <= n else " ".join(w[:n]).rstrip(",;:") + "..."


def _insight(ctx: dict) -> dict:
    m = ctx["moment"]
    facts = {f["name"]: f for f in m["facts"]}
    who = m.get("player") or m.get("team") or "The match"
    team = m.get("team") or "They"
    get = lambda n: _fact_text(facts[n]) if n in facts else None  # noqa: E731
    k = m["kind"]
    if k == "goal":
        why = f"{who} delivers the {facts.get('game_state', {}).get('value', 'goal')} from a {get('xg')} xG chance, and the game state flips: {ctx['score']}."
        angle = "drama"
    elif k == "momentum_swing":
        why = f"{team} have taken over: {get('shots_5m')} shots and {get('xg_5m')} xG in the last five minutes."
        angle = "momentum"
    elif k == "pressure_spell":
        why = f"{team} are pinning their opponents back with {get('field_tilt_5m')} of final-third touches."
        angle = "tactical"
    elif k == "rhythm_shift":
        why = f"The rhythm has changed: chaos index {get('chaos_index')} with {get('possession_changes_per_min')} turnovers per minute."
        angle = "tactical"
    elif k == "elite_pass":
        why = f"An average pass like this is completed only {get('xpass')} of the time; {who} made it look simple."
        angle = "individual"
    elif k == "speed_alert":
        why = f"{who} hit {get('top_speed_kmh')}, a constant threat in behind for the rest of the match."
        angle = "individual"
    elif k in ("big_chance", "great_save"):
        why = f"A {get('xg')} xG chance that could have changed the game at {ctx['score']}."
        angle = "drama"
    else:
        numeric = [f for f in m["facts"] if isinstance(f.get("value"), (int, float))][:2]
        bits = " and ".join(f"{f['label']} {_fact_text(f)}" for f in numeric)
        why = f"{m['headline']}" + (f", with {bits}." if bits else ".")
        angle = "milestone"
    return {"headline": _words(m["headline"], 8), "why_it_matters": why, "angle": angle,
            "cited_facts": list(facts)[:3]}


def _recap(ctx: dict) -> dict:
    teams = ctx["teams"]
    (n1, t1), (n2, t2) = list(teams.items())
    ctrl, other = ((n1, t1), (n2, t2)) if t1["possession_pct"] >= t2["possession_pct"] else ((n2, t2), (n1, t1))
    leaders = ctx.get("leaders", {})
    potm = (leaders.get("threat_added") or leaders.get("xg") or {}).get("name")
    title = "Full-time" if ctx["moment"]["kind"] == "full_time" else "Half-time"
    why = (f"{ctrl[0]} had {ctrl[1]['possession_pct']}% of the ball and {ctrl[1]['xg']} xG against "
           f"{other[1]['xg']} for {other[0]}. {ctx['score']}.")
    return {"headline": f"{title}: {ctx['score']}", "why_it_matters": why, "angle": "recap",
            "cited_facts": [], "player_of_the_match": potm}


def _variants(payload: dict) -> dict:
    ins = payload["insight"]
    out = []
    for t in payload["targets"]:
        persona, locale = t["persona"], t["locale"]
        tag = "" if locale.startswith("en") else f"[{locale}] "
        if persona == "casual":
            body = _words(ins["why_it_matters"].split(". ")[0].rstrip("."), 20) + "."
        else:
            body = _words(ins["why_it_matters"], 30)
        out.append({"persona": persona, "locale": locale, "title": tag + _words(ins["headline"], 5),
                    "body": tag + body})
    return {"variants": out}


class OfflineChatClient(FunctionInvocationLayer, BaseChatClient):
    OTEL_PROVIDER_NAME = "matchlens-offline"

    def __init__(self, latency_s: float = 0.0, fail_roles: tuple[str, ...] = (),
                 hallucinate_first_draft: bool = False, **kwargs: Any):
        super().__init__(**kwargs)
        self.latency_s = latency_s
        self.fail_roles = set(fail_roles)
        self.hallucinate_first_draft = hallucinate_first_draft
        self.calls: dict[str, int] = {}

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        role = _role(options)
        self.calls[role] = self.calls.get(role, 0) + 1
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if role in self.fail_roles:
            raise RuntimeError(f"simulated model outage for {role}")
        payload = json.loads(messages[-1].text)
        if role == "analyst":
            body = _insight(payload["context"])
            if self.hallucinate_first_draft and "revision_issues" not in payload:
                body["why_it_matters"] += " It is the 17th time this season."
        elif role == "recap":
            body = _recap(payload["context"])
        elif role == "persona":
            body = _variants(payload)
        else:
            raise RuntimeError(f"offline client has no behaviour for role {role!r}")
        return ChatResponse(messages=[Message(role="assistant", contents=[json.dumps(body)])],
                            response_id=f"offline-{role}-{self.calls[role]}")
