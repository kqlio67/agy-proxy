## 2026-09-18T18:12:27Z

<USER_REQUEST>
You are the SWE Light Orchestrator.
Your working directory is: /home/qumhab/Documents/Projects/agy-proxy/.agents/swe_1
The workspace directory is: /home/qumhab/Documents/Projects/agy-proxy
The authoritative user request is located at: /home/qumhab/Documents/Projects/agy-proxy/.agents/ORIGINAL_REQUEST.md

User Request Summary:
Fix Gemini quota display discrepancy in the Dashboard web panel where limits show as 0% / Exhausted despite CLI (`agy-proxy quota`) and Google Cloud Code showing valid non-zero remaining balances (e.g. 22.74% weekly, 100% 5h).

Requirements:
- R1: Preserve actual quota values in backend calculations (`get_quota_details` in `agy_proxy/auth/oauth.py` / `agy_proxy/auth/base.py`), never zeroing them to 0.0 due to temporary cooldowns or rate limits.
- R2: Clearly separate "Quota Exhausted" (remainingFraction <= 0) from "Temporary Cooldown / 429" (Rate Limited).
- R3: Synchronize web dashboard display (`agy_proxy/templates/dashboard.html`) to render accurate percentages, colors, and badges reflecting real quota or active cooldown.
- Ensure all tests pass (`./.venv/bin/pytest`) and add an automated test validating quota retention in `get_quota_details`.

Please execute the SWE Light loop: dispatch the implementer, run review/testing, maintain your progress.md and BRIEFING.md in your working directory, and report completion with full verification when done.
</USER_REQUEST>
