# BRIEFING — 2026-09-20T10:59:00Z

## Mission
Fix the account/session switching functionality in `agy-proxy` ("switch") via the SWE Light refinement loop.

## 🔒 My Identity
- Archetype: teamwork_preview_swe
- Roles: orchestrator, user_liaison, human_reporter, successor
- Working directory: /home/qumhab/Documents/Projects/agy-proxy/.agents/swe_3
- Original parent: parent
- Original parent conversation ID: 780a4921-b05b-4d2d-895e-0789a27a28b2

## 🔒 My Workflow
- **Pattern**: SWE Light
- **Scope document**: /home/qumhab/Documents/Projects/agy-proxy/.agents/swe_3/DISPATCH.md
1. **Decompose**: No decomposition (SWE Light: full task sent verbatim to workers).
2. **Dispatch & Execute**:
   - Direct: teamwork_preview_implementer -> teamwork_preview_reviewer (R1) -> teamwork_preview_reviewer (R2) -> teamwork_preview_reviewer (R3) -> teamwork_preview_victory_auditor
3. **On failure** (in this order):
   - Retry: nudge stuck agent or re-send task
   - Replace: spawn fresh agent with partial progress
   - Skip: proceed without (only if non-critical)
   - Redistribute: split stuck agent's remaining work
   - Redesign: re-partition decomposition
   - Escalate: report to parent (sub-orchestrators only, last resort)
4. **Succession**: At 16 spawns, write handoff.md, cancel crons, spawn successor.
- **Work items**:
  1. Fix account/session switching functionality [pending]
- **Current phase**: 2 (Dispatch & Execute)
- **Current focus**: Implementer dispatch (Round 1)

## 🔒 Key Constraints
- NEVER write, modify, or create source code files yourself. Delegate all implementation and repair to workers.
- NEVER explore or debug the codebase in order to solve the task yourself.
- Run SWE Light refinement loop: implementer -> reviewer -> reviewer -> reviewer -> victory auditor.
- Propagate user task verbatim.
- Carry open-issues ledger across all rounds.
- Verify independently: read worker's diff and re-run relevant tests.
- Never reuse a subagent after it has delivered its handoff — always spawn fresh.

## Current Parent
- Conversation ID: 780a4921-b05b-4d2d-895e-0789a27a28b2
- Updated: not yet

## Key Decisions Made
- Initial setup completed; ready for implementer dispatch.

## Team Roster
| Agent | Type | Work Item | Status | Conv ID |
|-------|------|-----------|--------|---------|

## Succession Status
- Succession required: no
- Spawn count: 0 / 16
- Pending subagents: none
- Predecessor: none
- Successor: not yet spawned

## Active Timers
- Heartbeat cron: not started
- Safety timer: none

## Artifact Index
- /home/qumhab/Documents/Projects/agy-proxy/.agents/swe_3/DISPATCH.md — Dispatch instructions
- /home/qumhab/Documents/Projects/agy-proxy/.agents/ORIGINAL_REQUEST.md — Original user request log
- /home/qumhab/Documents/Projects/agy-proxy/.agents/swe_3/BRIEFING.md — Persistent working memory
- /home/qumhab/Documents/Projects/agy-proxy/.agents/swe_3/progress.md — Progress and heartbeat
