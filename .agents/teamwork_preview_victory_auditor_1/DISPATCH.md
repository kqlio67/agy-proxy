## 2026-09-18T19:01:53Z
You are teamwork_preview_victory_auditor_1.
Your working directory is: /home/qumhab/Documents/Projects/agy-proxy/.agents/teamwork_preview_victory_auditor_1
The workspace directory is: /home/qumhab/Documents/Projects/agy-proxy
Your parent agent is: 0646c7c8-55fd-4b00-b603-319f4000cd4e

<original_task>
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
</original_task>
