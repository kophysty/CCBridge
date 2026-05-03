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
  Этап     Что                                                 Статус
  ───────  ─────────────────────────────────────────────────  ───────────
  PR1      core modules: verdict, events, event_bus,           ✅ Shipped
            lockfile, audit_log, state, migrations, config       b1edc23
            + unit tests + методология (Слой 1)                  107 tests
                                                                  97% cover

  PR2a     runners + context_builder + orchestrator            ✅ Shipped
            + integration tests                                   merged
                                                                  to main

  PR2b     renderers + transports + cli + audit fixes          ✅ Shipped
            + integration + e2e tests                             commit
                                                                  61dfbc5

  PR2c     skip-review + UserPromptSubmit hook                  🚧 Active
            + Stop hook fix #6 + audit Major #1/2/3 fixes         on branch
                                                                  pr2b/transports
                                                                   -cli

  PR3      templates + ccbridge init --methodology              📋 Queued
            (boilerplate для новых проектов)
```

### Added (Unreleased)

- 2026-05-03 — **PR2c этап 1.5** — закрытие 8 audit findings от
  повторного аудита PR2c этапа 1. Все blocker'ы и issues fixed:
  - **Blocker #1** (`prompt_hook` ignored `config.review.skip_marker`):
    `_resolve_skip_marker(project_dir)` загружает config через
    `load_config`, fail-open на ValueError → default. +4 теста
    (custom marker, default reject when custom set, no-config
    fallback, malformed config fallback).
  - **Blocker #2** (marker file forge-able с workspace write):
    HMAC-SHA256 signature через secret в `~/.ccbridge/skip-review.secret`
    (mode 0600 на POSIX, atomic write через mkstemp + os.replace +
    chmod). Подпись binds session_id + created_at + transcript_path +
    marker. Stop hook re-derives и validates через `hmac.compare_digest`.
    Reject и cleanup при mismatch. Дополнительно проверка
    `hook_event_name == "UserPromptSubmit"` против misroute. +9 тестов.
  - **Blocker #3** (backup poisoning через init --force / legacy):
    `_sanitize_for_backup()` — backup всегда CCBridge-free. Если
    источник содержит ТОЛЬКО CCBridge entries, backup не пишется.
    +3 теста + обновление 2 существующих под новую семантику.
  - **Blocker #4** (uninstall удалял весь parent entry, теряя user
    hooks): `_strip_ccbridge_from_hooks_dict()` — общий helper
    фильтрует nested `entry["hooks"]`, сохраняет parent если в
    nested.hooks остались user commands. Применяется и для backup
    sanitize, и для uninstall. +1 regression test.
  - **High #5** (consume failure → still skipped): `_consume_marker()`
    обязан успешно `unlink()` перед возвратом True; OSError в unlink
    → audit runs. +1 тест с monkeypatched `Path.unlink`.
  - **Medium #6** (future timestamp проходит TTL): clock-skew check
    `age < -SKIP_MARKER_CLOCK_SKEW (5 sec)`. +2 теста (rejection +
    small skew tolerance).
  - **Minor #7** (shell quoting только пробелы): `_quote_for_shell()`
    переписан на `subprocess.list2cmdline` (Windows) / `shlex.quote`
    (POSIX). +6 unit-тестов в `tests/unit/test_shell_quoting.py`.
  - **Minor #8** (stop_hook docstring stale): docstring обновлён —
    skipped теперь empty stdout, добавлено описание HMAC validation
    workflow.
  Метрики: 350 тестов (+27 от substep 5+6 baseline 323), ruff clean,
  mypy strict ok. Repro Blocker #1 ([no-audit] marker), Blocker #3
  (force backup), Blocker #4 (mixed entry) — все проходят на фиксах.
- 2026-05-03 — `Discovery/logs/decisions.md` запись «Plan A confirmed»:
  пользователь подтвердил doувод архитектуры до конца, не упрощать.
  Зафиксированы 8 слоёв сложности и rationale (Wave-readiness,
  recovery model, multi-transport, lifecycle hygiene, etc.).
- 2026-05-03 — `Projects/00-strategy/product-capabilities.md` —
  capabilities matrix по версиям v0.0.x → v0.3, use cases, rationale
  про сложность. Точка входа для стейкхолдеров.
- 2026-05-03 — **PR2c этап 1** на ветке `pr2b/transports-cli`.
  Skip-review feature (A+C) + audit fixes:
  - `transports/prompt_hook.py` (NEW, 12 тестов) — UserPromptSubmit
    hook, маркер `[skip-review]` (case-insensitive `casefold()`),
    атомарная запись `.ccbridge/skip-review.json` (session_id +
    transcript_path + ISO8601 created_at). Fail-open guardrails:
    missing session_id / non-str prompt → empty stdout, no marker.
  - `transports/stop_hook.py` — `_check_skip_marker()` consume:
    match по session_id, TTL 30 мин, best-effort delete. Fail-open
    на броken JSON. Fix #6: `verdict=skipped` → empty stdout
    (вместо `continue:false`); CLI audit run неизменён.
  - `core/config.py` — `[review] skip_marker` (default `[skip-review]`)
    + `skip_trivial_diff_max_lines` (default 0 = off). 3 теста.
  - `core/context_builder.py` — пороговый skip: при
    `min_diff_lines > 0` И `diff_lines ≤ N` → `ContextSkipped(
    reason="trivial_diff")`. Прокидывается через orchestrator +
    audit_invoker.
  - `cli.py` — рефакторинг `_patch_settings_json` /
    `_unpatch_settings_json` под список `CCBRIDGE_HOOK_EVENTS`:
    `init` пишет оба entry (Stop + UserPromptSubmit), `uninstall`
    снимает оба, legacy bare `ccbridge stop-hook` авто-апгрейдится
    на `<sys.executable> -m ccbridge.cli stop-hook`. Subcommand
    `ccbridge prompt-hook`. +9 интеграционных тестов.
  Метрики: 323 теста (+32 от PR2b), ruff clean, mypy strict ok.
- 2026-05-03 — `Discovery/logs/2026-05-03-pr2c-checkpoint.md` —
  чекпоинт перед context compaction (план substep 5+6 + ответы
  аудитора verbatim + marker file schema).
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
