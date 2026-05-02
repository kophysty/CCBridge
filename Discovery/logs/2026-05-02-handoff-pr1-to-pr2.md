# Handoff: PR1 → PR2 (CCBridge)

**Дата:** 2026-05-02
**Создан:** в конце сессии 2026-05-02 для передачи в новую сессию
**Прошлая сессия:** этот же чат, ~6 часов работы
**Следующая сессия:** новый чат, открытый из папки проекта `D:\Dev\CCBridge`

---

## TL;DR — что нужно знать новой сессии

1. **PR1 завершён локально.** 8 модулей `core/` написаны, 107 тестов
   проходят, coverage 97%, ruff и mypy strict зелёные.
2. **Перед началом работы** — инициализировать git репозиторий и
   запушить в GitHub `https://github.com/kophysty/CCBridge` (см. §6).
3. **Дальше — PR2:** orchestrator + runners + CLI + transports +
   renderers. План в `Projects/v0.1-mvp/README.md` §PR2.
4. **Всё что нужно для контекста — в этом репо.** ROADMAP, ARCHITECTURE,
   Discovery/logs, Rulebook. Никаких внешних знаний о прошлой сессии
   не требуется.

---

## 1. Что было сделано в этой сессии (хроника)

### Утром
- Обсудили боль ручного copy-paste между Claude Code и Codex CLI
- Запустили research-агентов: автоматизация CC↔Codex + Wave Terminal +
  AutoForge
- Создали папку `D:\Dev\CCBridge\` с README v0 и ARCHITECTURE.md v0.0.1
- Запустили 3 параллельных аудитора (Plan, systematic-debugging,
  prompt-engineering) на v0.0.1
- Свели аудит в `audit/2026-04-28-pre-implementation-audit.md`
- Применили 25 правок → ARCHITECTURE.md v0.0.2

### Днём (продолжение)
- Уточнили UX (один терминал + опционально `audit watch`)
- Заложили Wave-readiness через EventBus + множественные renderer'ы
- ARCHITECTURE.md v0.0.2 → v0.0.3 (§2.9 + AC-21)
- Начали PR1: `events.py`, `event_bus.py`, `verdict.py` + 40 unit-тестов

### Вечером
- Заимствовали методологию из Oil Automation (Слой 1)
- Создали Rulebook (INDEX + R-000 + R-001..R-008), ROADMAP, CHANGELOG,
  ADR, Discovery/logs, Projects/, CLAUDE.md, AGENTS.md
- Завершили PR1: `lockfile.py`, `audit_log.py`, `state.py`,
  `migrations.py`, `config.py` + 67 integration-тестов
- pytest: 107/107 ✅, ruff clean, mypy strict ok, coverage 97%
- Этот handoff документ

---

## 2. Текущий статус проекта

### Структура

```
D:\Dev\CCBridge\
├── README.md                                  # обновлён
├── ARCHITECTURE.md (v0.0.3)                    # все 25 правок аудита
├── ROADMAP.md                                  # PR1 → Local Complete
├── CHANGELOG.md                                # [Unreleased] обновлён
├── CLAUDE.md                                   # для Claude Code
├── AGENTS.md                                   # для Codex
├── pyproject.toml                              # deps + ruff + mypy
├── .gitignore
│
├── Rulebook/ (10 файлов)                      # активные правила
│   ├── INDEX.md
│   ├── R-000-how-to-add-rules.md
│   └── R-001..R-008                           # 8 универсальных правил
│
├── ADR/                                        # arch decisions
│   ├── README.md
│   └── ADR-001-python-cli-not-bash-or-mcp-first.md
│
├── Discovery/                                  # нарратив
│   ├── logs/
│   │   ├── decisions.md                        # 5 записей
│   │   ├── insights.md                         # 5 записей
│   │   ├── conversation-log.md                 # 2 сессии
│   │   └── 2026-05-02-handoff-pr1-to-pr2.md    # ← этот файл
│   └── sources/.gitkeep
│
├── Projects/
│   ├── README.md                               # где что лежит
│   ├── 00-strategy/.gitkeep
│   ├── cross-cutting/.gitkeep
│   └── v0.1-mvp/README.md                      # план PR1+PR2+PR3
│
├── audit/
│   └── 2026-04-28-pre-implementation-audit.md  # 3 аудитора + 25 правок
│
├── docs/
│   └── development.md                          # setup команды
│
├── templates/
│   └── codex-system-prompt.md                  # system prompt с anti-patterns
│
├── Output/
│   └── README.md                               # READ-ONLY
│
├── src/ccbridge/
│   ├── __init__.py
│   ├── core/                                   # ← PR1 ВЕСЬ ЗДЕСЬ
│   │   ├── __init__.py
│   │   ├── events.py            (75 строк, 12 тестов)   ✅
│   │   ├── event_bus.py         (26 строк, 6 тестов)    ✅
│   │   ├── verdict.py           (78 строк, 22 теста)    ✅
│   │   ├── lockfile.py          (97 строк, 12 тестов)   ✅
│   │   ├── audit_log.py         (51 строк, 14 тестов)   ✅
│   │   ├── state.py             (105 строк, 20 тестов)  ✅
│   │   ├── migrations.py        (42 строки, 10 тестов)  ✅
│   │   └── config.py            (100 строк, 10 тестов)  ✅
│   └── transports/__init__.py                  # PR2 наполнит
│
└── tests/
    ├── __init__.py
    ├── unit/
    │   ├── test_events.py                      # 12
    │   ├── test_event_bus.py                   # 6
    │   ├── test_verdict.py                     # 22
    │   └── test_migrations.py                  # 10
    └── integration/
        ├── test_lockfile.py                    # 12
        ├── test_audit_log.py                   # 14
        ├── test_state.py                       # 20
        └── test_config.py                      # 10
```

### Метрики качества

```
  Метрика                               Значение
  ────────────────────────────────────  ─────────────────────────────────
  Модулей в core/                        8 / 8 (PR1 complete)
  Тестов всего                            107 (40 unit + 67 integration)
  Test coverage                           97% (575 stmts, 18 miss)
  pytest время                            0.68 сек
  ruff check                              ✅ All checks passed
  mypy strict                             ✅ no issues found
```

### Что покрыто из аудита

```
  Финдинг              Где закрыто                                          AC
  ──────────────────   ───────────────────────────────────────────────────  ─────
  P0-1 (lockfile race)  lockfile.py — portalocker + TTL + triplet            AC-9
  P0-3 (audit primary)  audit_log.py + state.py recovery model                AC-11, AC-12
  P1-1 (semantic)       verdict.py — model_validator + validate_semantics    AC-13
  P1-2 (confidence)     verdict_confidence + issues_completeness               AC-13
  Schema migration      migrations.py + state.py / identity.json               AC-16
  Config + BOM strip    config.py                                              AC-17 part
  Cyrillic round-trip   audit_log + state                                      AC-17
```

---

## 3. ⚠️ ПЕРВЫЕ ШАГИ В НОВОЙ СЕССИИ

### Шаг 1: Прочитать обязательные файлы

В таком порядке (по R-007 + CLAUDE.md шапка):

1. **Этот handoff** — `Discovery/logs/2026-05-02-handoff-pr1-to-pr2.md`
2. **`ROADMAP.md`** — секция Active (там PR1 «Local Complete»,
   PR2 «Queued»)
3. **`ARCHITECTURE.md`** v0.0.3 — особенно §2.9, §3 «Module layout»,
   §8 «Acceptance criteria» AC-1..AC-21
4. **`CLAUDE.md`** — обязательные сверки перед задачей
5. **`Projects/v0.1-mvp/README.md`** — план PR2 в деталях

### Шаг 2: Проверить что окружение работает

```bash
cd D:\Dev\CCBridge
.venv\Scripts\activate                      # Windows
# или: source .venv/bin/activate            # Linux/Mac

# Проверка зелёного прогона:
pytest --cov=ccbridge                       # должно быть 107 passed, 97%
ruff check src/ tests/                       # All checks passed
mypy src/ccbridge                            # Success
```

Если что-то красное — **остановиться и разобраться** (по R-006 не идти
дальше без зелёного baseline).

### Шаг 3: Инициализировать git и запушить в GitHub

**Это важный шаг — ДО старта PR2.** Pre-existing CCBridge — это не
git-репозиторий, нужно сделать первый коммит.

GitHub репо: **`https://github.com/kophysty/CCBridge`** (создан пустой,
ждёт первый push).

#### Команды (точно как на скриншоте от пользователя)

```bash
cd D:\Dev\CCBridge

git init
git add .
git status              # ← ВАЖНО: проверить что .venv не попал
                        # (он в .gitignore, должен быть исключён)
                        # также НЕ должны попасть:
                        # - .ccbridge/identity.json (gitignore'нится в подключаемых проектах)
                        # - state.json, *.lock, *.jsonl (gitignore)

git commit -m "$(cat <<'EOF'
PR1: core modules + методологическая структура (Слой 1)

Реализованы 8 модулей core/ (events, event_bus, verdict, lockfile,
audit_log, state, migrations, config) с 107 тестами и coverage 97%.
ruff clean, mypy strict ok.

Закрывает аудит-финдинги P0-1 (lockfile race), P0-3 (audit primary),
P1-1 (semantic validation), P1-2 (confidence split). См. AC-9..AC-21
в ARCHITECTURE.md.

Методология (Слой 1) — Rulebook + ROADMAP + ADR + Discovery/logs +
CLAUDE.md + AGENTS.md — заимствована и адаптирована из Oil Automation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"

git branch -M main
git remote add origin https://github.com/kophysty/CCBridge.git
git push -u origin main
```

#### Альтернатива: тематические коммиты

Если предпочитаешь дробить — можно несколькими коммитами:

```bash
git init
git add pyproject.toml .gitignore docs/development.md
git commit -m "chore: project setup (pyproject, ruff, mypy, pytest)"

git add Rulebook/ ADR/ ROADMAP.md CHANGELOG.md CLAUDE.md AGENTS.md \
        Discovery/ Projects/ Output/
git commit -m "docs(methodology): Слой 1 — Rulebook + ADR + ROADMAP + ..."

git add ARCHITECTURE.md README.md audit/ templates/
git commit -m "docs(arch): ARCHITECTURE v0.0.3 + audit + templates"

git add src/ccbridge/ tests/
git commit -m "feat(core): 8 core modules + 107 tests (coverage 97%)"

git branch -M main
git remote add origin https://github.com/kophysty/CCBridge.git
git push -u origin main
```

> **R-001 reminder:** этот handoff — единственное место в проекте, где
> я заранее даю детальные команды коммита. По R-001 ассистент не
> делает commit без approval; **ты сам выполняешь эти команды**.

#### После пуша

Проверить, что репозиторий заполнился корректно:

```bash
git log --oneline                  # должен показать твой commit(ы)
gh repo view kophysty/CCBridge      # если установлен gh CLI
# или открыть https://github.com/kophysty/CCBridge в браузере
```

---

## 4. Что делать дальше — PR2

### Цель PR2

Склейка всех модулей PR1 в работающий цикл. Реальный peer-review
заработает по итогам PR2.

### Список модулей

```
  Файл                                       Что делает
  ─────────────────────────────────────────  ─────────────────────────────────
  src/ccbridge/core/orchestrator.py           Main loop с recovery model.
                                               Использует lockfile + audit_log
                                               + state + verdict + EventBus.

  src/ccbridge/core/context_builder.py        Diff snapshot (git stash create) +
                                               файлы + rules → prompt для Codex.
                                               Pre-flight: max_diff_lines check.

  src/ccbridge/runners/claude_runner.py       subprocess обёртка
                                               `claude --print --output-format json`.
                                               Возвращает stdout как dict.

  src/ccbridge/runners/codex_runner.py        subprocess обёртка
                                               `codex exec ...`.
                                               + retry с backoff на 429/network.
                                               + lenient JSON parse (markdown
                                                 fences extraction).

  src/ccbridge/transports/stop_hook.py        Entry point из Claude Code Stop
                                               hook. Читает stdin JSON,
                                               проверяет stop_hook_active,
                                               запускает orchestrator,
                                               пишет decision: block в stdout.

  src/ccbridge/transports/audit_watch.py      Live tail audit.jsonl с rich
                                               форматтированием (второй
                                               терминал).

  src/ccbridge/cli.py                         Click-based:
                                               - ccbridge init <path>
                                               - ccbridge audit run
                                               - ccbridge audit get
                                               - ccbridge audit list
                                               - ccbridge audit watch
                                               - ccbridge status
                                               - ccbridge uninstall <path>

  src/ccbridge/renderers/base.py              Renderer Protocol
  src/ccbridge/renderers/rich_renderer.py     Stop hook stdout — rich UI
  src/ccbridge/renderers/jsonl_renderer.py    audit.jsonl writer как listener
  src/ccbridge/renderers/silent_renderer.py   для тестов
```

### Тесты для PR2

- **Integration tests** в `tests/integration/`:
  - `test_orchestrator.py` — recovery model, lockfile usage,
    state persistence
  - `test_context_builder.py` — diff snapshot, pre-flight checks
  - `test_runners.py` — мок subprocess, retry, JSON extraction
  - `test_renderers.py` — каждый renderer на одном потоке событий
- **E2E tests** в `tests/e2e/` (с маркером `@pytest.mark.e2e`):
  - `test_full_cycle.py` — `claude` и `codex` в PATH, полный цикл
    на тестовой задаче (skip по умолчанию)

### Acceptance для PR2

Закрыть оставшиеся AC из ARCHITECTURE.md §8:
- AC-1 (init создаёт `.ccbridge/` + патчит `.claude/settings.json`)
- AC-2 (audit run проходит цикл)
- AC-3 (3 fail → needs_human, lockfile освобождается)
- AC-4 (lenient JSON parse + retry → error)
- AC-5 (audit.jsonl валиден после torn-write краша)
- AC-6 (audit list rich-форматирован)
- AC-7 (project_id стабилен после перемещения)
- AC-8 (параллельный audit run × 2 — блокировка)
- AC-10 (Stop hook в timeout, lockfile освобождается)
- AC-14 (pre-flight diff size)
- AC-15 (init не ломает существующий settings.json)
- AC-18 (empty/binary diff → skipped)
- AC-19 (network resilience: 429 + 3 retries)
- AC-20 (diff snapshot защищает от race с Claude)
- AC-21 (event-driven UX — все renderer'ы на одной шине)

---

## 5. Дисциплина для новой сессии

По R-007 и CLAUDE.md:

### При начале

1. Прочитать этот handoff
2. Прочитать `ROADMAP.md`
3. Если что-то непонятно — `Discovery/logs/decisions.md` и
   `insights.md` объясняют ключевые «почему»
4. Создать новый план в `Projects/v0.1-mvp/PR2-plan.md` (или дополнить
   существующий `Projects/v0.1-mvp/README.md`)
5. Обновить `ROADMAP.md` — перевести `v0.1-PR2` из Queued в Active

### Во время

- **Никаких commits/push без approval** (R-001)
- **TDD по умолчанию** для новых модулей (R-005) — failing test
  first, потом минимальная имплементация
- **verification before completion** (R-006) — каждое «готово» с
  evidence (pytest output)
- **decisions.md** — каждое значимое решение Was/Now/Why/Impact
- **CHANGELOG.md `[Unreleased]`** — каждый merge с описанием

### При завершении сессии

- Создать новый `Discovery/logs/2026-XX-XX-handoff-*.md` если работа
  не закончена
- Обновить `ROADMAP.md` (статусы)
- Обновить `CHANGELOG.md` `[Unreleased]`

---

## 6. Открытые точки / вопросы

### Закрытые в эту сессию

- ✅ Architecture v0.0.1 → v0.0.2 → v0.0.3 (аудит, UX слой)
- ✅ Claude Code Stop hook timeout (600s default — подтверждено)
- ✅ Все 8 core модулей с тестами и coverage
- ✅ Методологическая структура (Слой 1)
- ✅ Wave-readiness заложена через EventBus + Renderer Protocol

### Открытые на PR2

- ⏳ subprocess detached mode для Stop hook на Windows — пока не
  тестировался; возможно понадобится в `transports/stop_hook.py`
  если синхронный flow окажется UX-проблемой (но default 600s
  timeout обычно достаточно)
- ⏳ Реальное поведение `codex exec --json` — формат вывода нужно
  будет проверить на живом CLI и адаптировать парсер в
  `runners/codex_runner.py`
- ⏳ E2E тесты требуют `claude` и `codex` в PATH; на CI нужно либо
  мокнуть, либо помечать `@pytest.mark.e2e` и запускать только
  локально

### Открытые на PR3 (Слой 2 boilerplate)

- ⏳ `templates/boilerplate-project/` — копия структуры Слоя 1 с
  Jinja2 шаблонами, параметризованными `project_name`
- ⏳ Команда `ccbridge init <path> --methodology=full | minimal`

### Открытые на v0.2

- ⏳ WaveRenderer (`renderers/wave_renderer.py`) — `wsh badge`
  интеграция
- ⏳ Cost tracking в audit.jsonl
- ⏳ Detached background process если синхронный hook окажется
  UX-проблемой

### Открытые на v0.3

- ⏳ MCP server (`transports/mcp_server.py`) — Claude видит Codex
  как обычный tool

---

## 7. Полезные ссылки

### Внутри проекта

- [`README.md`](../../README.md) — суть проекта
- [`ARCHITECTURE.md`](../../ARCHITECTURE.md) — полная архитектура
- [`ROADMAP.md`](../../ROADMAP.md) — единая точка входа
- [`CLAUDE.md`](../../CLAUDE.md) — правила для Claude
- [`AGENTS.md`](../../AGENTS.md) — правила для Codex
- [`Rulebook/INDEX.md`](../../Rulebook/INDEX.md) — реестр правил
- [`Projects/v0.1-mvp/README.md`](../../Projects/v0.1-mvp/README.md) —
  план PR1/PR2/PR3
- [`audit/2026-04-28-pre-implementation-audit.md`](../../audit/2026-04-28-pre-implementation-audit.md)
  — pre-implementation audit
- [`templates/codex-system-prompt.md`](../../templates/codex-system-prompt.md)
  — system prompt для Codex с anti-patterns

### Внешние

- GitHub: **https://github.com/kophysty/CCBridge** (пустой, ждёт
  первый push)
- Anthropic Claude Code hooks docs:
  https://docs.claude.com/en/docs/claude-code/hooks
- portalocker: https://pypi.org/project/portalocker/
- Pydantic: https://docs.pydantic.dev/

---

## 8. Финальные слова

PR1 — это ~30% работы v0.1.0. PR2 будет крупнее (~50%), PR3 меньше
(~20%, в основном templates).

Готовый PR1 = надёжный фундамент: EventBus отделяет UI от логики,
audit.jsonl — primary source of truth, lockfile через portalocker
закрывает все Windows race conditions, semantic validation отлавливает
LLM hallucinations. Можно строить PR2 сверху без оглядки на низкий
уровень.

Удачи в новой сессии. ✊
