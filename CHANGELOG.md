# Changelog — CCBridge

Все значимые изменения проекта. Формат:
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
семантическое версионирование (`MAJOR.MINOR.PATCH`).

> Версионирование привязано к ROADMAP. Каждый релиз = запись здесь
> + ссылка на план реализации в `Projects/`. См.
> [R-007](Rulebook/R-007-workflow-planning-discipline.md).

---

## [Unreleased]

**В работе:** v0.1.0 — MVP peer-review pipeline.

**План:** [`Projects/v0.1-mvp/README.md`](Projects/v0.1-mvp/README.md).

### Прогресс

```
  Этап     Что                                                Статус
  ───────  ─────────────────────────────────────────────────  ──────────
  PR1      core modules: verdict, events, event_bus,          ✅ Pushed
            lockfile, audit_log, state, migrations, config     b1edc23
            + unit tests + методология (Слой 1)                107 tests
                                                                97% cover

  PR2a     runners + context_builder + orchestrator           🚧 Active
            + integration tests                                 PR2-plan.md

  PR2b     renderers + transports + cli                        📋 Queued
            + integration + e2e tests                           PR2-plan.md

  PR3      templates + ccbridge init                            📋 Queued
            (boilerplate для новых проектов)
```

### Added (Unreleased)

- 2026-05-02 — `ADR/ADR-002-audit-jsonl-ownership-orchestrator.md`
  (Accepted) — фиксация архитектурного решения по конфликту
  PR2a vs PR2b plan. Variant A: orchestrator owns audit.jsonl
  appends, EventBus = UI/broadcast only, JsonlRenderer удалён из
  v0.1 плана. Найдено в аудите PR2a (handoff-pr2a-audit.md
  Major #4).

### Changed (Unreleased)

- 2026-05-02 — `Projects/v0.1-mvp/PR2-plan.md` §PR2b: JsonlRenderer
  удалён из списка модулей, добавлено примечание про ownership
  с ссылкой на ADR-002.
- 2026-05-02 — `ARCHITECTURE.md` §2.9: уточнение что renderer'ы
  НЕ пишут в audit.jsonl, обновлена диаграмма потока данных.
  audit_watch явно описан как отдельный процесс, читает файл
  напрямую (не in-process bus).
- 2026-05-02 — `.gitignore` исправлен: паттерны `logs/`, `tmp/`,
  `temp/` теперь анчорятся на корень репо (`/logs/`, `/tmp/`,
  `/temp/`), чтобы не накрывать `Discovery/logs/` (нашу
  нарративную папку — она tracked).

### Added (Unreleased)

- 2026-05-02 — **PR2a code-complete** на ветке `pr2a/orchestrator-
  runners`. 4 модуля + 47 интеграционных тестов:
  - `runners/claude_runner.py` (98% cov, 10 тестов) — subprocess
    обёртка `claude --print --output-format json`, structured error
    on every failure path.
  - `runners/codex_runner.py` (91% cov, 25 тестов) — subprocess
    `codex exec --json`, lenient JSON parse (markdown fences +
    walk-the-braces fallback), retry с backoff на 429 / Retry-After,
    1 retry на unparseable JSON. Закрывает AC-4, AC-19.
  - `core/context_builder.py` (95% cov, 12 тестов) — git stash
    create snapshot + pre-flight (empty/binary/too-large) +
    промпт-сборка (rules → cache_hit hash; recent audits filter
    по run_uuid; system prompt). Закрывает AC-14, AC-18, AC-20.
  - `core/orchestrator.py` (92% cov, 10 тестов) — main loop с
    recovery model: lockfile → for iter in 1..N → build_context →
    run_codex → Verdict.model_validate → validate_semantics →
    audit_log.append → save_state → release lock (always).
    Закрывает AC-3, AC-9, AC-11, AC-12, AC-18, частично AC-21.
  Метрики: 164 теста, coverage 95%, ruff clean, mypy strict ok.
- 2026-05-02 — `Projects/v0.1-mvp/PR2-plan.md` — детальный план PR2 с
  декомпозицией PR2a (runners + context_builder + orchestrator) /
  PR2b (renderers + transports + cli), per-модуль коммитами, AC-маппингом
  и TDD-дисциплиной. По R-007.
- 2026-05-02 — `.env.example` — шаблон env-переменных
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CLAUDE_PROJECT_DIR`,
  dev-overrides). См. ARCHITECTURE.md §6.1.
- 2026-05-02 — `core/lockfile.py` — портабельный file lock через
  `portalocker` с triplet metadata (pid + hostname + started_at +
  run_uuid), TTL stale recovery (default 30 мин), context manager.
  12 integration-тестов.
- 2026-05-02 — `core/audit_log.py` — append-only JSON-lines, atomic
  single-write, tolerant reader (skip torn lines + unknown event_type
  с warning). 14 integration-тестов включая cyrillic round-trip.
- 2026-05-02 — `core/state.py` — Identity / State dataclasses,
  atomic write через `tempfile + os.replace` (tempfile в той же
  директории — Windows safety), recovery model (state.json как кэш).
  20 integration-тестов.
- 2026-05-02 — `core/migrations.py` — schema_version миграции
  (registry + migrate function), `backup_file` helper. 10 unit-тестов.
- 2026-05-02 — `core/config.py` — TOML loader с иерархией
  global → project, BOM strip, type-safe Pydantic-стилей дата-классы,
  catch typos через `_reject_unknown`. 10 integration-тестов.

### Changed (Unreleased)

- 2026-05-02 — Методологическая структура заимствована из Oil Automation:
  Rulebook (INDEX + R-000..R-008), ROADMAP, ADR, Discovery/logs,
  Projects/, AGENTS.md (peer для Codex). Слой 1 из плана адаптации.
- 2026-05-02 — `core/events.py` — Pydantic event-классы для EventBus
  (StartedEvent, ContextBuiltEvent, CodexThinkingEvent, VerdictEvent,
  IterationCompleteEvent, ErrorEvent, WarningEvent) + `parse_event`.
  12 unit-тестов.
- 2026-05-02 — `core/event_bus.py` — синхронный pub/sub bus с защитой
  от broken listeners. 6 unit-тестов.
- 2026-05-02 — `core/verdict.py` — Pydantic `Issue` / `Verdict` с
  `model_validator(severity_implies_failure)` + функция
  `validate_semantics()` (drop issues с file/line/rule_id mismatch,
  effective verdict с confidence threshold). 22 unit-теста.
- 2026-04-28 — `pyproject.toml` (deps + ruff + mypy + pytest).
- 2026-04-28 — `templates/codex-system-prompt.md` — system prompt
  для Codex с anti-patterns, severity calibration, hard constraints.
- 2026-04-28 — `ARCHITECTURE.md` v0.0.3 — добавлен §2.9 «UX &
  Event-driven rendering» (Wave-readiness), AC-21.
- 2026-04-28 — `audit/2026-04-28-pre-implementation-audit.md` —
  сводный аудит трёх параллельных аудиторов (Plan,
  systematic-debugging, prompt-engineering). 25 правок применены
  в `ARCHITECTURE.md` v0.0.2.
- 2026-04-28 — `README.md`, `ARCHITECTURE.md` v0.0.1, базовая
  структура папок, `.gitignore`.
- 2026-05-02 — `.gitignore` расширен: caches (mypy/ruff/pytest/
  coverage), secrets (`.env`, `.env.local`, `*.pem`), runtime артефакты
  CCBridge (lockfile, state.json, audit.jsonl, identity.json,
  `.ccbridge/`), IDE/OS junk, logs, Output генерируемые артефакты.

### Changed (Unreleased)

- 2026-04-28 — Default `context_level` для Codex изменён с `full`
  на `medium` (cost spiral mitigation, см. P1-3 в аудите).
- 2026-04-28 — Lockfile вынесен из `state.json` в отдельный файл
  `.ccbridge/lockfile` (закрывает 5 race conditions, аудит P0-1).
- 2026-04-28 — `audit.jsonl` стал primary source of truth,
  `state.json` — кэш (recovery model, аудит P0-3).
- 2026-04-28 — `confidence` в Verdict разделён на
  `verdict_confidence + issues_completeness` (аудит P1-2).

### Deprecated

— (нет)

### Removed

- 2026-04-28 — `transports/mcp_server.py` заглушка убрана из v0.1
  (отложено в v0.3, YAGNI; аудит).
- 2026-04-28 — Абстракции `Coder`/`Reviewer` из v0.1 убраны
  (YAGNI; конкретные runners достаточно).

### Fixed

— (первый релиз)

### Security

- 2026-04-28 — Зафиксировано: API ключи (Codex/Claude) **не передаются**
  через CCBridge. Хранятся в env переменных, в config.toml только
  имя env-ключа.

---

## Формат записей

При добавлении новой записи в `[Unreleased]`:

- **Дата формата ISO** (`2026-05-02`)
- Категория: Added / Changed / Deprecated / Removed / Fixed / Security
- Связь с планом / правилом / ADR (ссылка)
- Если правка из аудита — указать ID находки (`P0-1`, `P1-3` и т.п.)

При релизе — секция `[Unreleased]` переименовывается в `[v0.X.Y] — YYYY-MM-DD`,
создаётся новая `[Unreleased]`.
