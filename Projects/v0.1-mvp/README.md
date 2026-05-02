# v0.1 MVP — Implementation Plan

**Связано с:** [`ROADMAP.md`](../../ROADMAP.md) — запись `v0.1-PR1`,
`v0.1-PR2`, `v0.1-PR3`, `v0.1.0`

**Архитектура:** [`ARCHITECTURE.md`](../../ARCHITECTURE.md) v0.0.3+

**Pre-implementation audit:** [`audit/2026-04-28-pre-implementation-audit.md`](../../audit/2026-04-28-pre-implementation-audit.md)

---

## Цель v0.1

MVP peer-review pipeline между Claude Code и Codex CLI:
- Claude закончил → Stop hook автоматически запускает Codex review
- Verdict в виде Pydantic JSON с semantic validation
- Цикл максимум 3 итерации, потом → `needs_human`
- Live tail второй терминал через `ccbridge audit watch`
- Все события — через EventBus, готовность к Wave Terminal в v0.2
- Команда `ccbridge init` устанавливает CCBridge в любой проект

---

## Декомпозиция: PR1 + PR2 + PR3

### PR1 — Core modules (foundation)

**Цель:** все transport-agnostic модули с unit-тестами, работающие
в изоляции.

**Модули:**

```
  Файл                              Что делает                                Тесты
  ────────────────────────────────  ────────────────────────────────────────  ────────
  src/ccbridge/core/events.py        Pydantic event-классы для EventBus        ✅ 12
  src/ccbridge/core/event_bus.py     In-process pub/sub                         ✅ 6
  src/ccbridge/core/verdict.py        Pydantic Verdict + validate_semantics    ✅ 22
  src/ccbridge/core/lockfile.py      portalocker-based + TTL stale recovery     🟡 TBD
  src/ccbridge/core/audit_log.py     JSON-lines append-only + tolerant reader   🟡 TBD
  src/ccbridge/core/state.py         state.json + identity.json + recovery     🟡 TBD
  src/ccbridge/core/migrations.py    schema_version миграции                    🟡 TBD
  src/ccbridge/core/config.py        TOML loader + BOM strip + иерархия        🟡 TBD
```

**Acceptance:**
- Все unit-тесты проходят (`pytest tests/unit/`)
- ruff check zero warnings
- mypy strict zero errors
- Cross-platform: Windows + Linux (CI)

**Не входит в PR1 (намеренно):**
- orchestrator (PR2)
- runners (PR2)
- CLI entry points (PR2)
- transports (PR2)
- renderers (PR2)
- integration tests (PR2)

---

### PR2 — Orchestrator + transports + renderers

**Цель:** склейка всех модулей PR1 в работающий цикл.

**Модули:**

```
  Файл                                  Что делает
  ────────────────────────────────────  ───────────────────────────────────────
  src/ccbridge/core/orchestrator.py      Main loop с recovery model
  src/ccbridge/runners/claude_runner.py  Обёртка claude --print
  src/ccbridge/runners/codex_runner.py   Обёртка codex exec + retry/backoff
  src/ccbridge/core/context_builder.py   Diff snapshot + files + rules → prompt
  src/ccbridge/transports/stop_hook.py   Stop hook entry point
  src/ccbridge/transports/audit_watch.py Live tail второго терминала
  src/ccbridge/cli.py                    Click-based CLI: init/audit/status/uninstall
  src/ccbridge/renderers/rich_renderer.py
  src/ccbridge/renderers/silent_renderer.py
  # NB: jsonl_renderer не существует — audit.jsonl пишет
  #     orchestrator. См. ADR-002.
```

**Acceptance:**
- Все integration tests с `tmp_path` проходят
- E2E test (требует `claude` и `codex` в PATH) — golden path работает
- AC-1..AC-21 из ARCHITECTURE.md покрыты

---

### PR3 — Templates + ccbridge init

**Цель:** boilerplate для новых проектов через `ccbridge init`.

**Артефакты:**

```
  Файл                                            Что делает
  ──────────────────────────────────────────────  ──────────────────────────────
  templates/boilerplate-project/                   Копия Слоя 1 как шаблон
   ├── CLAUDE.md.j2                                 для нового проекта.
   ├── AGENTS.md.j2                                Jinja2 шаблоны
   ├── ROADMAP.md.j2                               параметризуются project_name.
   ├── CHANGELOG.md.j2
   ├── Rulebook/INDEX.md.j2
   ├── Rulebook/R-000..R-008 (8 правил)
   ├── ADR/README.md
   ├── Discovery/logs/decisions.md
   ├── Discovery/logs/insights.md
   ├── Discovery/logs/conversation-log.md
   ├── Projects/README.md
   ├── Projects/00-strategy/.gitkeep
   ├── Projects/cross-cutting/.gitkeep
   ├── Output/README.md
   └── .ccbridge/.gitignore                         (* — не коммитим)

  src/ccbridge/cli.py (init команда)              ccbridge init <path>
                                                    --methodology=full | minimal
```

**Acceptance:**
- `ccbridge init D:\Dev\test-project --methodology=full` создаёт всю
  структуру
- `--methodology=minimal` — только `.ccbridge/`
- Существующие файлы в проекте не перезаписываются (merge стратегия
  для `.claude/settings.json`, см. AC-15)
- Backup `.claude/settings.json.ccbridge.bak` создаётся

---

## Финал v0.1.0

Объединение PR1 + PR2 + PR3, tag в git, обновление CHANGELOG.

**Acceptance v0.1.0:**
- Все AC-1..AC-21 проходят
- Real-world smoke на Oil Automation (первый клиент CCBridge):
  - `ccbridge init D:\Dev\supermvp_prj\Oil_automation --methodology=minimal`
    (там уже есть Rulebook, не нужно перезаписывать)
  - Цикл проходит на одной реальной задаче (CCBridge ревьюит изменения
    в Oil Automation)
  - 24h prod-traffic без сбоев
- README обновлён с реальными примерами
- v0.1.0 tag в git

---

## Текущий прогресс

```
  Этап     Статус               Что готово
  ───────  ──────────────────   ──────────────────────────────────────────────
  PR1       ✅ Shipped (push)     8/8 модулей: events, event_bus, verdict,
                                  lockfile, audit_log, state, migrations, config
                                  107 тестов (40 unit + 67 integration)
                                  Coverage 97%, ruff clean, mypy strict ok
                                  + методологическая структура (Слой 1)
                                  Commit b1edc23 → main pushed.
  PR2a      🚧 Active             Plan: PR2-plan.md §PR2a
                                  runners + context_builder + orchestrator
                                  Branch: pr2a/orchestrator-runners
  PR2b      📋 Queued             Plan: PR2-plan.md §PR2b
                                  renderers + transports + cli
  PR3       📋 Queued
  v0.1.0    📋 Queued
```

См. детальный план PR2: [`PR2-plan.md`](PR2-plan.md).

См. также `Discovery/logs/decisions.md` для контекста принятых
решений.
