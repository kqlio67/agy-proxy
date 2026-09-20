# Original User Request

## Initial Request — 2026-09-18T18:11:53Z

This is a single self-contained fix; keep it small and focused.

Виправлення розбіжності та багу некоректного відображення квот Gemini у веб-панелі (Dashboard), де ліміти відображаються як 0% / Exhausted, тоді як у CLI (`agy-proxy quota`) та у відповідях Google Cloud Code відображаються дійсні ненульові залишки (наприклад, 22.74% тижневого ліміту та 100% 5-годинного ліміту).

Working directory: /home/qumhab/Documents/Projects/agy-proxy
Integrity mode: development

## Requirements

### R1. Збереження дійсних значень квот у розрахунках Backend
Забезпечити, щоб метод розрахунку структурованих квот акаунта (`get_quota_details` у `agy_proxy/auth/oauth.py` / `agy_proxy/auth/base.py`) завжди повертав реальні відсотки та частки залишкової квоти (`remainingFraction`) із бакетів Cloud Code (`gemini-weekly`, `gemini-5h`), і ніколи не зануляв їх до 0.0 при наявності тимчасових кулдаунів чи прапорців обмеження швидкості.

### R2. Чітке розмежування "Вичерпано квоту" (Exhausted) та "Тимчасовий кулдаун / 429" (Rate Limited)
Відокремити статус повного вичерпання квоти (`remainingFraction <= 0`) від тимчасового обмеження частоти запитів (HTTP 429 / cooldown 60с). Тимчасовий кулдаун не повинен маскуватися під 0% залишкової квоти та статус "Exhausted".

### R3. Синхронізація відображення у веб-панелі Dashboard
Оновити логіку рендерингу карток акаунтів у `agy_proxy/templates/dashboard.html`, щоб шкали Weekly Limit та 5-Hour Limit для Gemini та Claude & GPT завжди рендерили точні відсотки та кольори, ідентичні до офіційного виводу CLI `agy-proxy quota`, а бейдж статусу чітко показував актуальний стан (наприклад, реальний відсоток квоти або "Cooldown (Xс)" замість хибного "Exhausted ⚠️").

## Acceptance Criteria

### Точність числових показників
- [ ] Ендпоінт `/api/accounts` повертає точні ненульові значення `percent` і `fraction` для обох бакетів Gemini (`weekly` та `5h`), коли в `quota_summary` є залишок квоти.
- [ ] У веб-інтерфейсі Dashboard шкали Weekly Limit та 5-Hour Limit відображають дійсні ненульові відсотки (наприклад, 23% та 100%), що збігаються з виводом CLI `agy-proxy quota`.

### Коректність статусів
- [ ] Картка акаунта із залишком квоти (> 0%) не відображає статус "Exhausted ⚠️" та червону пусту шкалу.
- [ ] При тимчасовому кулдауні (429) відображається статус кулдауну з часом відновлення, без спотворення фактичного залишку тижневої квоти.

### Регресійне тестування
- [ ] Всі наявні тести (`./.venv/bin/pytest`) проходять без помилок (177+ passed).
- [ ] Додано автоматизований тест на валідацію збереження дійсних квот при розрахунку `get_quota_details`.

## Follow-up — 2026-09-18T18:55:01Z

Status update request: What is the current progress of Review Round 3 / Victory Audit?

## Follow-up — 2026-09-19T03:47:56Z

This is a single self-contained fix; keep it small and focused.

Виправлення збереження стану відключення (Pause / Disable) для сесій Gemini Web Browser Sessions у проксі. Якщо користувач вимикає сесію веб-браузера (в індивідуальній картці або масово для секції), цей стан (`enabled: false`) повинен суворо зберігатися у конфігурації `web_sessions.json` та не скидатися автоматично на `enabled: true` після перезавантаження чи перезапуску сервера (точно так само, як це працює для Google AI Studio API Keys).

Working directory: /home/qumhab/Documents/Projects/agy-proxy
Integrity mode: development

## Requirements

### R1. Суворе збереження стану `enabled: false` у сховищі сесій
Забезпечити, щоб стан `enabled` для `GeminiWebSession` зберігався у `web_sessions.json` та коректно зчитувався при перезавантаженні пулу (`AccountPool.load_accounts()`). Ні за яких умов перезапуск сервера чи виклик `load_accounts()` не повинен скидати вимкнену сесію на `enabled: true`.

### R2. Заборона автоматичної ініціалізації та CDP-перевірок для вимкнених акаунтів
У методі `initialize_all()` та фонових процесах пулу акаунтів виключити запуск оновлення токенів, кукі та звернень через CDP (Chrome DevTools Protocol на порту 9222) для акаунтів, де `enabled == False`. Вимкнена сесія не повинна самовільно опитувати браузер чи тригерити колбеки `on_token_refreshed`, що можуть перезаписати файл або скинути прапорець.

### R3. Дедуплікація та міграція без перезапису вимкненого стану
При завантаженні та дедуплікації сесій (Step 2 автоміграції та Step 6 завантаження `web_sessions.json`), якщо виявлено збіг за куками чи ID між наявним вимкненим акаунтом та імпортованими даними, пріоритет повинен надаватися збереженому стану користувача (`enabled: false`), а не дефолтному `true`. Усунути можливі гонки запису при масовому перемиканні сесій.

### R4. Коректність відображення у веб-панелі Dashboard
При вимкненні сесії Gemini Web у Dashboard бейдж повинен переходити у стан "Paused", повзунок перемикача залишатися вимкненим, а після перезавантаження сторінки або перезапуску проксі статус повинен залишатися "Paused".

## Acceptance Criteria

### Стійкість збереження стану (Persistence)
- [ ] Вимкнення сесії Gemini Web через API (`POST /api/accounts/{id}/toggle`) або метод `pool.set_account_enabled(id, False)` зберігає `"enabled": false` у файлі `web_sessions.json`.
- [ ] При створенні нового екземпляра `AccountPool` та виклику `load_accounts()` вимкнена сесія завантажується зі значенням `enabled == False`.
- [ ] Виклик `await pool.initialize_all()` не змінює стан вимкненої сесії `GeminiWebSession` і залишає `enabled == False`.

### Ізоляція від фонових оновлень
- [ ] Для вимкненої веб-сесії під час запуску проксі не викликається автоматичне підключення до CDP та оновлення кукі, якщо сесія вимкнена.
- [ ] Масове перемикання секції веб-сесій (`toggleSectionAccounts` у Dashboard або послідовні запити toggle) не призводить до конфліктів запису чи пошкодження файлу.

### Регресійне тестування
- [ ] Всі наявні тести (`./.venv/bin/pytest tests/`) проходять без помилок (192+ passed).
- [ ] Додано цільові автоматизовані тести на перевірку збереження стану `enabled: False` для Gemini Web після повного циклу перезапуску та ініціалізації пулу.

## 2026-09-20T10:57:59Z

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: Small, focused team

This is a single self-contained fix; keep it small and focused.
Fix the account/session switching functionality in `agy-proxy` ("switch"). Currently, changing accounts via CLI or UI fails with an error, requiring manual session changes in `antigravity cli`. The goal is to make the `switch` command/feature work correctly.

Working directory: /home/qumhab/Documents/Projects/agy-proxy
Integrity mode: development

## Requirements

### R1. Fix account switching
When a user attempts to switch accounts (via CLI or UI), the operation must complete successfully without throwing errors. It must correctly update the underlying session state without requiring manual intervention in `antigravity cli`.

## Acceptance Criteria

### Verification via CLI
- [ ] Running the CLI command to switch accounts executes successfully (exit code 0).
- [ ] The command output does not contain any error messages or tracebacks related to session updating.
- [ ] After switching, the active session is correctly updated to the requested account.

---
*Next: when approved → delegate via invoke_subagent (see Delegation Protocol)*

