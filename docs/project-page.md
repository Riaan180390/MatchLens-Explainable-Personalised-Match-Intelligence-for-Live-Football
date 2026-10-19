# Innovation Studio project page

Keep this in sync with what is actually built before submitting (deadline 27 Oct 2026).

## Title

MatchLens: Explainable, Personalised Match Intelligence for Live Football

## Tagline

A multi-agent engine on Azure that turns live football events into explainable insights, broadcast-ready overlays and automated recaps. It explains why each moment matters, checks every number, and tells the story differently for analysts, casual fans and player-focused viewers, in their language.

## Keywords

Microsoft Foundry, Microsoft Agent Framework, Multi-Agent, Azure Container Apps, Azure Event Hubs, Real-Time Analytics, Explainable AI, Personalisation, Multilingual, Synthetic Data, Sports Analytics, Football, Broadcast Graphics

## Challenge

The Synthetic Match Insights Engine for Premier League Studio

## Writing code

Yes

## Description

## The problem
A scoreline tells you what happened, not why. Broadcasters and streaming platforms have more event data than ever, but turning it into on-screen stories in real time still takes a room full of people, and every viewer gets the same graphics whether they analyse tactics for a living or are watching their first match.

## What MatchLens does
One pipeline, five stages:

1. **Ingest:** synthetic match events (passes, shots, tackles, pressure, possession changes, sprints, match metadata) stream in as they happen.
2. **Interpret:** a deterministic stats engine keeps live match state: pass distance, accuracy and a 0–10 pass difficulty rating, ball and shot speed, xG, possession, field tilt, PPDA, momentum and a control-vs-chaos index.
3. **Explain:** a moment detector scores every event for significance, and AI agents explain why it matters: a momentum swing, a pressure spell, a change in the game's rhythm. Every claim carries the numbers behind it.
4. **Render:** insights become timed, machine-readable overlay cues (start time, duration, screen region, priority) with a published JSON Schema, ready for a graphics partner to draw over the live feed. A web overlay demo shows them on a pitch view.
5. **Personalise:** the same insight is rewritten for each viewer: Analyst mode, Casual mode, Player Focus mode, favourite club, and the viewer's language.

## How it works
- **Two speeds.** Rule-based overlays (player name tags, speed and distance triggers, pass difficulty) go out in under a second. Agent-written narrative follows a few seconds later. If an agent is slow or fails, a templated caption is shown instead, so the screen never waits on a model.
- **Specialised agents with shared match state**, orchestrated with Microsoft Agent Framework:
  - *Analyst agent:* explains why a moment matters, using live match state
  - *Fact-checker agent:* rejects any narrative whose numbers don't match the stats engine
  - *Persona agent:* adapts tone, depth and language for each viewer mode
  - *Recap agent:* writes half-time and full-time recaps and player-of-the-match notes
- **Explainable by design.** Every insight links back to the computed facts that support it.

## Synthetic data
MatchLens ships with its own match simulator built around fictional clubs and players: a possession-chain model driven by player attributes, team style, pressing and pitch geometry. Averaged over 30 simulated matches, 14 box-score metrics fall inside typical top-flight ranges (about 1.75 goals, 14 shots and 430 passes per team at 79% pass accuracy, with the hardest-working players covering around 12 km). The same seed always replays the same match, so demos are reproducible.

## Built with
Microsoft Foundry · Microsoft Agent Framework · Azure Event Hubs · Azure Container Apps · Azure Cosmos DB · Azure Web PubSub · Azure AI Speech · Application Insights · Python (FastAPI) · React

## Roadmap
Auto-eventing from a video feed (a vision model generating pass and speed metrics), more languages and voice commentary, and a fantasy-manager mode.
