---
name: technical-docs
description: Writes technical documentation, specs and design docs (context, goals, ADR-style decisions, mermaid diagrams, rollout plan) and suggests where to store them. Use when asked to "document this", for a design doc, a spec, an ADR, a technical proposal or an architecture README.
metadata:
  hub-owner: shared
  version: "1"
---

# Technical documentation

## Before writing
1. `hub_whoami` for the context and `memory_search` on the topic (previous decisions, existing docs, system names).
   Don't contradict a recorded decision without flagging it.
2. Read the real code you are documenting (models, endpoints, flows). Don't invent class, table or endpoint names:
   if you haven't verified it, mark it `TODO: verify`.
3. Pick the type: **design doc/spec** (something to build), **ADR** (one specific decision), **system doc** (how
   something that already exists works).

## Design doc / spec structure
```
# Design: <title>
## Overview            (3-5 lines: what and why)
## Goals / Non-goals   (bullets; non-goals prevent scope creep)
## Current State       (how it works today, with file paths)
## Problem Statement
## Proposed Architecture (+ mermaid diagram)
## Decisions           (ADR format below, one per decision)
## Rollout / Migration (steps, feature flags, data migrations, rollback)
## Testing             (unit, integration, e2e; what gets measured)
## Open Questions      (with an owner if known)
```

## Decisions (short ADR)
```
### D1: <decision in one line>
- Status: proposed | accepted | superseded by Dn
- Context: what forces the decision
- Options: A (pros/cons), B (pros/cons)
- Decision and consequences
```
Real pros and cons; if an option has no downsides, don't force a trade-off.

## Diagrams
Use mermaid in a ```mermaid block: `flowchart LR` for pipelines and components, `sequenceDiagram` for
requests/webhooks/MCP, `erDiagram` for data models. At most ~15 nodes per diagram; split it if it grows.

## Where to store it
- Follow the repo's convention if there is one (`docs/`, `design_docs/`, `adr/`). Otherwise: design docs in
  `docs/design/<topic>.md`, ADRs in `docs/adr/NNNN-<title>.md`, system docs in `docs/<TOPIC>.md`, short ones in `README.md`.
- Docs that must be visible outside the repo (wiki, task tracker) only if the user asks.
- Never paste secret values or `.env` contents; name the variable and say it lives in the secret manager (the hub
  injects it with `secret_http_request`).

## When done
- Check: would a newcomer understand the *why* without asking you? Is every claim about the code verified?
- `memory_save` a self-contained note: date, doc path, main decision and status (proposed vs accepted). Pass
  `project` where required.
