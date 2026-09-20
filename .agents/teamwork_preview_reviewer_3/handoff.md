# Adversarial Reviewer 3 Handoff Report: Gemini Quota Discrepancy & Exhausted/Cooldown Fix

## 1. What the Prior Attempt Got Wrong

### Finding 1: Quota Exhaustion Falsely Injected as Fake 3600s Rate Limit in `fetch_quota()`
- **Input**: An account has fully exhausted quota (`remainingFraction <= 0.001` or `percent: 0.0`) in Google CloudCode's response.
- **Expected**: Account retains `fraction: 0.0`, `percent: 0.0`, `cooldown_seconds: 0`, `is_rate_limited: False`, and Dashboard renders red `Exhausted` badge with reset countdown time.
- **Actual**: `fetch_quota()` in `agy_proxy/auth/oauth.py` executed:
  ```python
  if is_exhausted:
      duration = earliest_reset if (earliest_reset and earliest_reset > 0) else 3600
      self.rate_limited_models[key] = now + duration
  ```
  This polluted `rate_limited_models` with a 3600-second duration! Consequently:
  - `to_dict()["rate_limited"]` was set to `True`.
  - `get_quota_details()["gemini"]["cooldown_seconds"]` became `3600`.
  - In the Dashboard UI, the account header badge, table status badge, and Telemetry Matrix all displayed `Cooldown (3600s)` instead of `Exhausted`!
- **Root cause**: Quota exhaustion was overloaded into `self.rate_limited_models` instead of being handled strictly as quota status.
- **Fix**: Removed the artificial `rate_limited_models` injection from `fetch_quota()`. Quota summary and bucket metrics now purely reflect Google CloudCode's quota numbers. Added `is_quota_exhausted()` on `BaseAccountSession`.

### Finding 2: Inverted Badge Priority in `dashboard.html` Displayed "Cooldown" for Fully Exhausted Accounts
- **Input**: Account has 0% quota across both Gemini and Claude (`geminiIsExhausted && claudeIsExhausted`), but has an active cooldown timestamp.
- **Expected**: Status badge renders `Exhausted` (in bold red).
- **Actual**: In `dashboard.html`, `maxAccountCooldown > 0` was evaluated before `geminiIsExhausted && claudeIsExhausted`:
  ```javascript
  ${maxAccountCooldown > 0 ? `Cooldown (${maxAccountCooldown}s)` : (geminiIsExhausted && claudeIsExhausted ? 'Exhausted' : '')}
  ```
  This caused any exhausted account to render as `Cooldown (Xs)` on the card header badge and table status badge. Furthermore, table quota cells displayed `Cooldown (Xs)` before checking `geminiIsExhausted`.
- **Root cause**: Inverted conditional evaluation order in template rendering.
- **Fix**: Reordered evaluation in `renderAccountCard` and `renderAccountTableRow` so that `(!isApiKey && !isWeb && geminiIsExhausted && claudeIsExhausted)` takes precedence over `maxAccountCooldown > 0`. In table quota cells, `isExhausted` now takes precedence over cooldown.

### Finding 3: Zero-Bucket Groups Omitted From CLI `agy-proxy quota` Output
- **Input**: Account has quota groups with 0 sub-buckets but a group-level `remainingFraction` (e.g., 35.4%).
- **Expected**: CLI `agy-proxy quota` / switcher display renders the group name and a progress bar showing 35.40%.
- **Actual**: `format_agy_quota_display()` in `agy_proxy/switcher.py` iterated only over `group.get("buckets", [])`, rendering zero progress bars when `buckets` was empty.
- **Root cause**: Missing zero-bucket fallback in ASCII progress bar generator.
- **Fix**: Added `if not buckets and "remainingFraction" in group:` branch to render group-level progress bars in `format_agy_quota_display()`.

### Finding 4: Account Pool Dispatch Failed to Filter Quota-Exhausted Accounts
- **Input**: Pool has Account A (0% quota, 0 requests) and Account B (100% quota, 10 requests).
- **Expected**: Requests for `gemini-2.5-pro` are dispatched to Account B.
- **Actual**: `pool.get_candidate_accounts()` only checked `not acc.is_rate_limited(model)`. Since Account A was not rate-limited (cooldown = 0), it was selected first due to having 0 requests.
- **Root cause**: Candidate accounts selection did not check quota exhaustion.
- **Fix**: Added `and not acc.is_quota_exhausted(model)` to candidate filtering in `pool.get_candidate_accounts()`, with graceful fallback to active pool if all accounts are constrained.

### Finding 5: CSS Bar Width Distortion on Boundary Values
- **Input**: Edge case quota values (< 0 or > 100).
- **Expected**: Valid CSS widths bounded between 0% and 100%.
- **Actual**: Raw unconstrained percentages could generate invalid CSS (e.g. `width: -1%` or `width: 105%`).
- **Root cause**: Direct interpolation of percentage values without math clamping.
- **Fix**: Clamped all progress bar style widths using `Math.max(0, Math.min(100, pct))` in card view, table view, and telemetry matrix. Also clamped `fraction` to `[0.0, 1.0]` in `oauth.py`.

---

## 2. What Was Changed
- `agy_proxy/auth/base.py`:
  - Added `BaseAccountSession.is_quota_exhausted(model)` to determine true quota exhaustion without relying on rate limiting flags.
- `agy_proxy/auth/oauth.py`:
  - Removed artificial `rate_limited_models` injection and cleanup loop from `AntigravityOAuthSession.fetch_quota()`.
  - Added clamping `[0.0, 1.0]` to `remainingFraction` in `get_quota_details()`.
- `agy_proxy/auth/pool.py`:
  - Updated `AccountPool.get_candidate_accounts()` to filter out accounts where `acc.is_quota_exhausted(model)` is true.
- `agy_proxy/switcher.py`:
  - Added zero-bucket fallback with group-level `remainingFraction` rendering in `format_agy_quota_display()`.
- `agy_proxy/templates/dashboard.html`:
  - Prioritized `Exhausted` over `Cooldown` in card header badge, table status badge, and table quota cells.
  - Added `Math.max(0, Math.min(100, ...))` clamping to all quota progress bars.
- `tests/test_quota_analytics.py`:
  - Added `test_quota_exhausted_decoupled_from_rate_limited` verifying that quota exhaustion does not set rate limits.
  - Added `test_pool_candidate_selection_prioritizes_non_exhausted_account` verifying routing prioritization.
  - Added `test_format_agy_quota_display_with_zero_buckets` verifying CLI output for zero-bucket groups.
- `tests/test_ui.py`:
  - Added `test_dashboard_card_and_table_exhausted_vs_cooldown_evaluation` testing Node evaluation of the template's JS rendering logic for both exhausted and cooldown states.

---

## 3. Verification Record
- **Deep Verification (ran actual tests):**
  - Ran `./.venv/bin/pytest tests/test_quota_analytics.py tests/test_ui.py tests/test_server_routes.py -v`: 40 passed in 8.70s.
  - Ran full test suite `./.venv/bin/pytest`: 192 passed, 1 warning in 37.16s.
- **Shallow Verification (manual only):**
  - Verified DOM badge rendering and color class bindings in `dashboard.html`.
  - Verified Node execution of `renderAccountCard` producing exact `Exhausted` and `Cooldown (60s)` badges under simulated conditions.
- **Unverified aspects:**
  - Live pixel rendering of real browser WebSockets connection timer in an external web browser.

---

## 4. Known Issues
- `Minor Robustness Risk`: If Google CloudCode introduces entirely new model group names not containing gemini, claude, gpt, 3p, anthropic, sonnet, or opus, they will fall back under the default group.

---

## 5. Remaining Risk & Next Step
- The task is complete. Backend calculations, routing candidate selection, and dashboard UI rendering are all verified and decoupled. All 192 unit tests pass. No further changes needed.
