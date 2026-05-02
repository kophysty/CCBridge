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
  PR1      core modules: verdict, events, event_bus,          ✅ 8/8
            lockfile, audit_log, state, migrations, config     107 tests
            + unit tests                                        97% cover

  PR2      orchestrator, runners, CLI, transports,             📋 Queued
            renderers, integration tests

  PR3      templates + ccbridge init                            📋 Queued
            (boilerplate для новых проектов)
```

### Added (Unreleased)

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
