# Handoff Report: Gemini Quota Discrepancy & Exhausted/Cooldown Status Fix

## Summary
Fixed discrepancy where Gemini account quotas appeared as `0% / Exhausted ⚠️` with empty red bars in the web Dashboard when rate-limited, even though the CLI (`agy-proxy quota`) and Google CloudCode telemetry reported active remaining quotas (e.g., 22.74% weekly limit, 100% 5-hour limit).

---

## 1. What Was Changed

### Backend Quota Calculations (`agy_proxy/auth/oauth.py` & `agy_proxy/auth/base.py`)
1. **Preserved Real Quota Fractions and Percentages in `get_quota_details`**:
   - Removed rate-limit zeroing logic (`0.0 if is_gemini_limited else ...` and `fraction = 0.0 if is_limited`) from `AntigravityOAuthSession.get_quota_details`.
   - `gemini`, `3p`, `5h`, and `weekly` dictionaries now always retain genuine `fraction` and `percent` values calculated from CloudCode buckets.
   - Preserved decimal precision up to 2 decimal places (`round(fraction * 100, 2)`) to preserve accurate percentages like `22.74%`.
   - Populated `is_rate_limited` flag and added `cooldown_seconds` for model groups (`gemini`, `3p`) to explicitly reflect active temporary cooldowns without corrupting quota numbers.
2. **Maintained Traffic Dispatch Protection in `get_model_quota`**:
   - `get_model_quota(model)` still clamps `remainingFraction = 0.0` when `self.is_rate_limited(model)` is True, ensuring that proxy routing never directs live requests to an account on cooldown.
3. **Fallback Delegation in `BaseAccountSession.get_quota_details` (`agy_proxy/auth/base.py`)**:
   - Added delegation to `AntigravityOAuthSession.get_quota_details` if `self.quota_summary` with `groups` exists on any `BaseAccountSession` instance.

### Dashboard Template (`agy_proxy/templates/dashboard.html`)
1. **Decoupled Quota Exhaustion from Rate Limiting / Cooldowns**:
   - Fixed `geminiIsExhausted` and `claudeIsExhausted`: only evaluated as `true` when actual quota is depleted (`geminiWkPct <= 0 || gemini5hPct <= 0`), NOT when temporary cooldown flags (`acc.rate_limited_models`) are set.
   - When a temporary rate limit cooldown (HTTP 429) is active:
     - Card footer renders: `Cooldown (Xs)` in amber with a clock icon alongside the clean reset badge (`Wk in Xd`), instead of false `Exhausted ⚠️`.
     - Card header badge renders: `Cooldown (Xs)` in amber.
     - Table view renders: `Cooldown (Xs)` badge under account type and in quota columns.
     - Progress bars display the real non-zero percentages (e.g. 23% and 100%) with appropriate amber and cyan colors matching CLI `agy-proxy quota`.
2. **Dual Quota Telemetry Matrix Decoupling**:
   - Updated badge in Dual Quota matrix: shows `Cooldown (Xs)` when on cooldown, `Ready` when quota > 20%, and `Exhausted` only when quota <= 0.
3. **Account Filter & Counter Accuracy**:
   - Fixed `totalExhausted` counter and filter: only accounts with genuine 0% remaining quota (`geminiPct <= 0 && claudePct <= 0`) are categorized as Exhausted (`0%`). Accounts on temporary 60s cooldowns are no longer falsely tallied as 0%.

### Automated Tests (`tests/test_quota_analytics.py`)
1. Updated `test_rate_limited_override` to verify that `is_rate_limited` is True, `cooldown_seconds > 0`, genuine quota percentages are preserved (1.0 / 100.0%), and `get_model_quota` correctly sets `remainingFraction = 0.0` for routing dispatch.
2. Added `test_quota_retention_gemini_with_cloudcode_buckets` to explicitly validate that an account with real CloudCode buckets (22.74% weekly, 100% 5h) retains exact non-zero numbers in `get_quota_details` and `to_dict()`, even during an active 60s cooldown from an HTTP 429.

---

## 2. Verification Record

- **Deep Verification (ran actual tests):**
  - Ran `./.venv/bin/pytest tests/test_quota_analytics.py`: 7 passed in 0.72s.
  - Ran `./.venv/bin/pytest tests/test_ui.py`: 2 passed in 0.15s.
  - Ran full test suite `./.venv/bin/pytest`: 178 passed, 1 warning in 44.01s.
- **Shallow Verification (manual run only):**
  - Inspected template DOM structures for `renderAccountCard`, `renderAccountTableRow`, and `renderAccountsAnalytics` to verify CSS classes (`text-amber-400`, `bg-amber-500/10`, `Cooldown (Xs)`).
- **Unverified aspects:**
  - Visual pixel-rendering in a live browser window with live WebSockets / auto-refresh interval ticking countdown seconds in real time.

---

## 3. Known Issues
- Prefix: `Minor Robustness Risk` — If CloudCode returns custom buckets with non-standard naming schemes other than "weekly", "5h", "five hour", or "5-hour", the bottleneck fallback picks the minimum remaining fraction among all buckets.

---

## 4. Untested Edge Cases & Next Step
- **Untested Edge Case**: Quota groups where CloudCode returns 0 buckets but includes group-level `remainingFraction` with an active 429. Covered by fallback logic but not present in current live CloudCode responses.
- **Reviewer Next Step**: Review diff in `agy_proxy/auth/oauth.py` and `agy_proxy/templates/dashboard.html` and verify live Dashboard rendering with an account placed into temporary rate limiting.
