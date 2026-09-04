---
name: systemic-project-planning
description: Turn vague "from A to B" goals into a verifiable project system with goal definition, problem discovery, requirements and constraints, phase/interface breakdowns, quality gates, acceptance criteria, KPI/SLO/DORA-style progress metrics, postmortems, and rule feedback loops. Use when planning projects, clarifying ambiguous outcomes, creating project briefs, defining validation matrices or quality gates, setting progress/risk metrics, or converting failures into reusable checklists and rules.
---

# Systemic Project Planning

## Overview

Use this skill to turn a vague current-state-to-target-state request into a project plan that can be decomposed, verified, measured, reviewed, and improved. Keep the main flow compact; read `references/systemic-planning-framework.md` when the user needs copyable templates, checklist tables, KPI definitions, or postmortem templates.

## Core Workflow

Follow this sequence unless the user asks for only one specific artifact:

1. Define `B`: State the target as an outcome, not a deliverable. Identify who judges success, deadline, and 3-5 success criteria.
2. Discover the real problem: Separate symptoms from root causes. Record evidence sources, affected users or systems, constraints, and the cost of doing nothing.
3. Write requirements and constraints: Convert the target into numbered, one-meaning requirements with boundaries, non-goals, risks, owners, and validation methods.
4. Break phases and interfaces down: Define each phase's input, action, output, owner, dependency, and handoff format. Name what must run serially and what can run in parallel.
5. Set quality gates: Define what must be true before moving forward. Prefer observable thresholds, verification methods, reviewers, and failure handling over subjective review-only gates.
6. Execute in small loops: Pick the smallest verifiable unit that tests the highest-risk assumption first. Record experiment scope, version, result, and decision.
7. Monitor metrics: Track result, quality, speed, stability, and risk together. Calibrate example thresholds to the project instead of treating defaults as universal.
8. Review and feed rules back: Convert failures into facts, root causes, triggers, prevention actions, and updates to templates, checklists, tests, monitoring, or rules.

## Output Pattern

When creating a plan, include these sections by default:

- Goal: current state `A`, target state `B`, owner, success criteria, non-goals.
- Problem and constraints: evidence, root causes or hypotheses, dependencies, risks, boundaries.
- Phases and gates: phase table with inputs, outputs, owners, dependencies, quality gates, validation methods, and failure handling.
- Metrics: small KPI set covering result, efficiency, quality, stability, and learning.
- Review loop: postmortem schedule, rule updates, and signals that should trigger replanning.

Keep thresholds explicit but mark them as project-calibrated examples when the user has not provided industry, team size, timeline, toolchain, or regulatory constraints.

## Reference Use

Read `references/systemic-planning-framework.md` when any of these are needed:

- The user asks for a complete project template, phase acceptance template, KPI dashboard template, or postmortem template.
- The plan needs checklist-style acceptance criteria across all eight steps.
- The user asks how to turn a failure, delay, defect, or repeated issue into reusable process rules.
- The user wants a more detailed explanation of Double Diamond, PDCA, V&V, SLO/DORA-style metrics, or AI risk governance in the planning loop.

## Guardrails

- Do not treat the delivered artifact itself as the goal unless the user explicitly defines success that way.
- Do not invent precise thresholds when context is missing; provide example thresholds and require calibration.
- Do not skip validation design. Every critical requirement should map to at least one test, demo, inspection, analysis, review, or data check.
- Do not make the process heavier than the risk demands. Use the full template for complex or cross-team work; use a compact version for small tasks.
