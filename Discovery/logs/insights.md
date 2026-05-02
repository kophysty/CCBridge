# Insights — CCBridge

> Наблюдения, инсайты, неочевидные связи. Append-only.

> Decisions (что выбрали) → `decisions.md`.
> Active rules (что делать) → `Rulebook/`.
> Здесь — **наблюдения**: что нового мы поняли о проекте, экосистеме,
> пользователе.

---

## Формат записи

```markdown
### [YYYY-MM-DD] <Заголовок>

Source: <откуда пришёл инсайт — конкретный файл / разговор / эксперимент>
Type: INSIGHT | WISH

> Цитата если применимо

Тело: 1-2 абзаца, связи, что это меняет.
```

---

### [2026-04-28] Готового CC↔Codex orchestrator'а в open-source нет

Source: research через 2 параллельных агентов (CC↔Codex automation +
Wave Terminal)
Type: INSIGHT

Под конкретный case «два CLI AI разговаривают между собой автоматически»
готового продукта нет. Ближайшие аналоги:
- AutoForge (`D:\Dev\confprd\autoforge-master`) — но это long-running
  build N features, не peer-review цикл; AGPL-3.0; SQLite/MCP overhead
- Hermes Pattern + Strapi (упомянуто в обсуждении) — single-user,
  без RBAC/multi-tenant, не enterprise
- CrewAI / LangGraph / AutoGen — это API-уровень оркестрации, не
  CLI-to-CLI

**Что это значит:** ниша свободна для Python CLI MVP. Можно делать.

---

### [2026-04-28] Claude Code Stop hook timeout = 600 секунд (default)

Source: Anthropic docs https://docs.claude.com/en/docs/claude-code/hooks
+ subagent verification 2026-04-28
Type: INSIGHT

Default 10 минут, override через `timeout` поле в `settings.json`.
Это снимает блокер с CCBridge архитектуры — синхронный Stop hook
реалистичен (3 итерации × 2-3 мин помещаются).

**Bonus:** есть штатное поле `stop_hook_active: true` в JSON input
hook'а — защита от рекурсии. Не нужно изобретать свой механизм.

**Что это меняет:** detached background process откладывается на v0.2
(если синхронный hook окажется UX-проблемой).

---

### [2026-04-28] Wave Terminal — это не магический «общий AI»

Source: глубокий обзор waveterm.dev docs через subagent
Type: INSIGHT

Часть ожиданий пользователя про Wave были мифом:
- ❌ «Один общий AI, видит все терминалы» — нет, AI это отдельный
  block, не глобальный listener
- ✅ Что есть: `wsh ai` — pipe-команда контекста в AI-блок явно

То есть Wave решает другую задачу: дисциплина «осознанной отправки
контекста» вместо магии. Это **лучше** для нашего случая, потому что
ты контролируешь что попадает в контекст.

Локальные модели через Wave: Ollama (qwen, llama, deepseek, codellama),
Kimi через Groq, LM Studio / vLLM / llama.cpp / LiteLLM через
provider=custom. **Полная поддержка**.

---

### [2026-04-28] AutoForge text-matching stop-condition — антипаттерн

Source: AutoForge `agent.py:272` — single-mode ловит фразы
«all features are passing» / «no more work to do» в LLM response
Type: INSIGHT

Парсинг свободного текста LLM как stop-condition — хрупко:
- LLM может вернуть «mostly passing» / «almost done»
- Локализация ломает (русский/английский)
- Sycophancy bias заставляет LLM сказать «всё хорошо», когда не всё

В CCBridge решено через **Pydantic Verdict schema + semantic
validation** (`core/verdict.py`). Verdict — это enum-литерал, не
текст. Semantic validation после Pydantic ловит hallucinations.

→ Эту разницу зафиксировать как Rulebook правило **не для CCBridge**
(там оно встроено в код), а **для будущих проектов через
ccbridge init** — стартовый правило-шаблон «не парсить text для
stop conditions».

---

### [2026-05-02] Oil Automation методология применима как boilerplate

Source: анализ практик Oil Automation через subagent 2026-05-02
Type: INSIGHT

Ровно 8 универсальных правил (R-001..R-008 в CCBridge) переносятся
без изменений из Oil Automation. Domain-specific убрано (R-001
internal-double-counting, R-019 deploy на VPS, R-026 schema-migrations
fingerprint и т.д.).

Это значит: **CCBridge может стать инструментом распространения
методологии**. Команда `ccbridge init <project> --methodology=full`
разворачивает в новом проекте всю эту дисциплину сразу. Это
превращает «техническую утилиту peer-review» в «кит для запуска
дисциплинированного AI-driven проекта».

**Что это меняет:**
- В PR3 явно делается папка `templates/boilerplate-project/` с
  копией Слоя 1
- В README CCBridge продаётся не только как «automated review», но
  и как «start a new project with proven methodology»
- Шаблон обновляется как параллельный артефакт — когда улучшается
  методология в CCBridge самом, она же улучшается для всех новых
  проектов

---

### [2026-05-02] CCBridge будет применён в Oil Automation первым

Source: обсуждение с пользователем 2026-05-02
Type: WISH (план)

После v0.1.0 релиза CCBridge — он развернётся в Oil Automation как
первый «настоящий» пользователь. Это даст:
- Realistic stress test на реальном проекте с 44 правилами Rulebook
- Calibration verdict_confidence на реальных diff'ах
- Возможность сравнить ad-hoc audit (3 параллельных subagent) с
  автоматическим CCBridge

**Что это меняет в текущей работе:** при разработке v0.1 надо помнить,
что первый клиент — мы сами с реальной методологией. Default
конфиг должен из коробки нормально работать с Oil Automation Rulebook
(44 правила × 2KB = тестируем prompt caching на cap'ах).
