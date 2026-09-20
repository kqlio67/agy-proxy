# Adversarial Reviewer 2 Handoff Report: Gemini Quota Discrepancy & Exhausted/Cooldown Fix

## 1. What the Prior Attempt Got Wrong

### Finding 1: Single Window Fallback For Zero-Bucket Groups & Custom Tiers Left the Other Window at Phantom 100%
- **Input**: CloudCode returns group-level remaining fraction with 0 buckets (e.g. 35.4%) or a custom bucket tier without explicit 5h/weekly IDs (e.g. 12.0%).
- **Expected**: Both `5h` and `weekly` window dictionaries in `quota_details` are populated with the group's true remaining quota, and the Dashboard bars reflect 35.4% (or 12.0%).
- **Actual**: Prior attempt only set `primary_win` (`5h` for Gemini, `weekly` for Claude), leaving the other window at default `100.0%`. In the Dashboard, Weekly Limit for Gemini falsely rendered as 100% (indigo) instead of 35.4% (amber), and 5-Hour Limit for Claude falsely rendered as 100% (fuchsia) instead of 35.4% (amber).
- **Root cause**: `get_quota_details` only populated `res[key][primary_win]`.
- **Fix**: Populated both `5h` and `weekly` dictionaries with `win_payload` whenever a group has zero buckets or only custom unclassified bucket IDs.

### Finding 2: Model-Specific Rate Limit Keys Failed Cooldown Lookup and Rate Limit Checks
- **Input**: An account has model-specific rate limits in `rate_limited_models`, e.g. `acc.rate_limited_models["gemini-2.5-pro"] = time.time() + 45` or `acc.rate_limited_models["claude-3-7-sonnet"] = time.time() + 50`.
- **Expected**: `acc.is_rate_limited("gemini-2.5-pro")` returns `True`, `acc.is_rate_limited("gemini")` returns `True`, and `get_quota_details()["gemini"]["cooldown_seconds"]` returns 45s.
- **Actual**: `is_rate_limited` returned `False` because it only checked key `"gemini"` or `"3p"`/`"claude"`. `cooldown_seconds` was `0` and `is_rate_limited` remained `False`.
- **Root cause**: Key mismatch when rate limits are marked under exact model IDs instead of group aliases.
- **Fix**: Updated `BaseAccountSession.is_rate_limited` to check the direct model key, group aliases, and any active limit in `self.rate_limited_models` matching the model family. Updated `AntigravityOAuthSession.get_quota_details` to aggregate maximum cooldown across all matching keys in the family.

### Finding 3: 3P Group Name Variations ("anthropic", "sonnet", "opus") Miscategorized Under Gemini
- **Input**: CloudCode returns quota groups named "Anthropic Claude Sonnet & Opus" or similar.
- **Expected**: Categorized under key `"3p"`.
- **Actual**: Categorized under `"gemini"` because the string matching only looked for `"claude"`, `"gpt"`, or `"3p"`.
- **Root cause**: Narrow substring check `("claude" in g_name or "gpt" in g_name or "3p" in g_name)`.
- **Fix**: Expanded substring matching to include `"anthropic"`, `"sonnet"`, `"opus"` in `oauth.py` (`fetch_quota` and `get_quota_details`).

### Finding 4: Dual Quota Telemetry Matrix & Table View Cooldown Inconsistency
- **Input**: Accounts rendered in Dual Quota Telemetry Matrix or Table View when `acc.rate_limited_models` is empty but `quota_details.*.cooldown_seconds` is active.
- **Expected**: Dual Quota Matrix and Table View render amber `Cooldown (Xs)` badges.
- **Actual**: Dual Quota Matrix only checked `acc.rate_limited_models` and ignored `quota_details.*.cooldown_seconds`, showing "Constrained" instead of "Cooldown (Xs)". Table View lacked an "Exhausted" badge for fully depleted accounts.
- **Root cause**: Incomplete cooldown aggregation in Dual Quota Matrix and missing exhausted badge in table row view.
- **Fix**: Updated Dual Quota Matrix to incorporate `geminiCooldown` and `claudeCooldown` into `maxCooldown`, and added the `Exhausted` badge fallback to table view.

### Finding 5: Missing `cooldown_seconds` and Schema Discrepancies in API Key and Gemini Web Sessions
- **Input**: AI Studio API Key and Gemini Web accounts calling `get_quota_details()`.
- **Expected**: Consistent schema with `cooldown_seconds` present for both `"gemini"` and `"3p"`.
- **Actual**: `cooldown_seconds` was missing from `get_quota_details()` in `api_key.py` and `gemini_web.py`.
- **Root cause**: Omission of cooldown calculation in non-OAuth subclasses.
- **Fix**: Added `cooldown_seconds` to `AIStudioApiKeySession.get_quota_details()` and `GeminiWebSession.get_quota_details()`.

---

## 2. What Was Changed
- `agy_proxy/auth/base.py`:
  - Added `**kwargs` and `self.quota_summary` initialization to `BaseAccountSession.__init__`.
  - Updated `BaseAccountSession.is_rate_limited` to check direct model keys, group aliases, and family model keys.
  - Delegated `BaseAccountSession.get_quota_details` directly to `AntigravityOAuthSession.get_quota_details`.
- `agy_proxy/auth/oauth.py`:
  - In `fetch_quota` and `get_quota_details`, expanded 3P group matching to include `"anthropic"`, `"sonnet"`, `"opus"`.
  - Populated both `5h` and `weekly` window dictionaries when groups lack sub-buckets or have custom unclassified tiers.
  - Aggregated model-specific cooldowns across family keys for `cooldown_seconds`.
- `agy_proxy/auth/api_key.py`:
  - Added `cooldown_seconds` to `get_quota_details()`.
- `agy_proxy/auth/gemini_web.py`:
  - Added `cooldown_seconds` to `get_quota_details()`.
- `agy_proxy/templates/dashboard.html`:
  - Included `geminiCooldown` and `claudeCooldown` in Dual Quota Telemetry Matrix `maxCooldown`.
  - Added `Exhausted` badge fallback to table view.
- `tests/test_quota_analytics.py`:
  - Added tests for zero-bucket dual-window population, model-specific key cooldowns, 3p group name variations, API/web schema consistency, and base account session defaults.
- `tests/test_ui.py`:
  - Added template DOM tests for table row, dual quota matrix, and cooldown components.

---

## 3. Verification Record
- **Deep Verification (ran actual tests):**
  - Ran `./.venv/bin/pytest tests/test_quota_analytics.py tests/test_ui.py tests/test_server_routes.py -v`: 36 passed in 11.85s.
  - Ran full test suite `./.venv/bin/pytest`: 188 passed, 1 warning in 44.08s.
- **Shallow Verification (manual only):**
  - Verified DOM badge rendering and color class bindings for card view, table view, and telemetry matrix in `dashboard.html`.
- **Unverified aspects:**
  - Live browser pixel-rendering of countdown timer ticking in real-time across WebSockets.
