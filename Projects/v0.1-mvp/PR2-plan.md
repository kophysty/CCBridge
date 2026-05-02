# PR2 — Orchestrator + transports + renderers (детальный план)

**Связано:** [`ROADMAP.md`](../../ROADMAP.md) запись `v0.1-PR2`,
[`Projects/v0.1-mvp/README.md`](README.md) §PR2,
[`ARCHITECTURE.md`](../../ARCHITECTURE.md) §2.6, §2.9, §3, §8.

**Дата:** 2026-05-02
**Статус:** 🚧 Active (после PR1 Local Complete + push)
**Базовая ветка:** `main` (commit `b1edc23`)

---

## Цель PR2

Склейка модулей PR1 в работающий peer-review pipeline. После PR2
команда `ccbridge audit run` проходит цикл `claude → codex → verdict`
без ручного вмешательства, события идут на EventBus, audit.jsonl
заполняется, lockfile корректно держит критическую секцию.

---

## Декомпозиция: PR2a → PR2b

PR2 разделён на два под-этапа. Каждый под-этап — отдельная ветка,
серия per-модуль коммитов, merge в `main` после зелёного pytest.

```
  Этап    Что входит                                          Зелёный stop-point
  ──────  ──────────────────────────────────────────────────  ──────────────────────────
  PR2a    runners/claude_runner.py                             pytest зелёный.
           runners/codex_runner.py                              AC-3, AC-4, AC-14, AC-18,
           core/context_builder.py                              AC-19, AC-20 закрыты.
           core/orchestrator.py                                 Ядро работает с моками
           + integration-тесты с mock subprocess                runner'ов.
  ──────  ──────────────────────────────────────────────────  ──────────────────────────
  PR2b    renderers/{base,silent,jsonl,rich}.py                 pytest зелёный.
           transports/stop_hook.py                              AC-1, AC-2, AC-5..AC-8,
           transports/audit_watch.py                            AC-10, AC-15, AC-21
           cli.py (Click)                                       закрыты. MVP работает
           + integration + e2e (skip-by-default)                end-to-end.
```

---

## PR2a — runners + context_builder + orchestrator

### Ветка

`pr2a/orchestrator-runners` от `main`.

### Порядок реализации (TDD по R-005)

```
  №   Модуль                         Ключевые задачи                     AC закрытие
  ──  ─────────────────────────────  ──────────────────────────────────  ─────────────────
  1   runners/claude_runner.py        subprocess.run обёртка               (вспомогательно)
                                       claude --print --output-format
                                       json. Парсит stdout как dict.
                                       NB: для PR2 это узкая роль —
                                       Claude вызывается только Stop-
                                       hook'ом самим, runner нужен
                                       для будущих сценариев и тестов.
  ──  ─────────────────────────────  ──────────────────────────────────  ─────────────────
  2   runners/codex_runner.py          subprocess.run + retry с              AC-4 (lenient
                                       backoff (1/4/16 сек) на 429/         JSON)
                                       network. Lenient JSON parse           AC-19 (network
                                       (markdown fences ```json…```          resilience)
                                       extraction). Возвращает (raw,
                                       parsed_json, retry_count).
                                       Учитывает api_key_env из Config.
  ──  ─────────────────────────────  ──────────────────────────────────  ─────────────────
  3   core/context_builder.py          1. git stash create → diff_sha        AC-14 (pre-flight
                                       2. git diff --numstat → LOC count    diff size)
                                       3. Pre-flight: max_diff_lines        AC-18 (empty/
                                          → ContextTooLargeError              binary skip)
                                       4. Empty/binary diff → возврат       AC-20 (diff
                                          ContextSkipped (verdict=skipped)    snapshot)
                                       5. Snapshot files в
                                          .ccbridge/iteration-{uuid}/
                                       6. Сборка prompt: system +
                                          rules (cached) + diff +
                                          recent audits (3, current run)
                                       Возвращает BuiltContext (prompt,
                                       diff_files, file_line_counts,
                                       known_rule_ids, snapshot_dir).
  ──  ─────────────────────────────  ──────────────────────────────────  ─────────────────
  4   core/orchestrator.py             Main loop:                            AC-3 (3 fail →
                                       acquire_lock → load_state →           needs_human)
                                       for iter in 1..max_iterations:        AC-9 (lockfile
                                         emit StartedEvent (iter==1)         реальный)
                                         build_context() → Context           AC-11 (recovery
                                         emit ContextBuiltEvent              из audit.jsonl)
                                         emit CodexThinkingEvent             AC-12 (tolerant
                                         result = codex_runner.run()         reader)
                                         verdict = Verdict(result)
                                         vv = validate_semantics(...)
                                         emit VerdictEvent
                                         audit_log.append(VerdictEvent)
                                         save_state(...)
                                         if vv.effective ∈ {pass,
                                                            needs_human,
                                                            error,
                                                            skipped}: break
                                       emit IterationCompleteEvent
                                       audit_log.append(...)
                                       release_lock
                                       Поведение при ошибках —
                                       try/finally вокруг lock; при
                                       повторном входе load_state
                                       решает: продолжать или новый
                                       run_uuid (recovery model).
```

### Тесты PR2a

```
  Файл                                  Что покрывает
  ────────────────────────────────────  ───────────────────────────────────────────
  tests/integration/test_codex_runner    Mock subprocess.run; lenient JSON parse
   .py                                    из markdown fences; retry на 429 +
                                          Retry-After; final failure → структуриров.
                                          ошибка; success → parsed dict.
  ────────────────────────────────────  ───────────────────────────────────────────
  tests/integration/test_claude_         Mock subprocess.run; парсинг JSON output;
   runner.py                              non-zero exit → структурированная ошибка.
  ────────────────────────────────────  ───────────────────────────────────────────
  tests/integration/test_context_        tmp_path с git init + commit + изменения.
   builder.py                             Empty diff → skipped; >2000 LOC → exit 1;
                                          binary-only → skipped; кириллица в путях;
                                          snapshot_dir создан и заполнен.
  ────────────────────────────────────  ───────────────────────────────────────────
  tests/integration/test_                Полный orchestrator с моками runners:
   orchestrator.py                        - happy path 1 iteration → pass
                                          - 3 fail → needs_human (AC-3)
                                          - codex invalid JSON → error verdict
                                          - lock busy → exit 2 (AC-8)
                                          - recovery: state.json удалён →
                                            восстановление из audit.jsonl (AC-11)
                                          - lockfile освобождается на исключении
                                          - все события прошли через EventBus
```

### Acceptance PR2a

- pytest зелёный (включая 107 тестов из PR1 + новые)
- ruff clean, mypy strict ok
- Coverage не падает ниже 90% на новых модулях
- AC-3, AC-4, AC-14, AC-18, AC-19, AC-20 закрыты
- AC-9, AC-11, AC-12 фактически проверены через test_orchestrator

### Коммиты PR2a (per-модуль)

```
  Шаг   Сообщение коммита
  ────  ─────────────────────────────────────────────────────────────────
  1     feat(runners): claude_runner — subprocess wrapper + JSON parse
  2     test(runners): unit tests for claude_runner
  3     feat(runners): codex_runner — subprocess + retry/backoff + lenient JSON
  4     test(runners): integration tests for codex_runner (AC-4, AC-19)
  5     feat(core): context_builder — diff snapshot + pre-flight (AC-14, AC-18, AC-20)
  6     test(core): integration tests for context_builder
  7     feat(core): orchestrator — main loop + recovery model
  8     test(core): integration tests for orchestrator (AC-3, AC-11, AC-12)
  9     docs: CHANGELOG entry for PR2a
```

Merge в `main` через `git merge --no-ff pr2a/orchestrator-runners`
(squash не используем — per-модуль коммиты ценны для аудита).

---

## PR2b — renderers + transports + cli

### Ветка

`pr2b/transports-cli` от `main` (после merge PR2a).

### Порядок реализации (TDD)

> ⚠️ **Архитектурное уточнение (ADR-002, 2026-05-02):**
> audit.jsonl writes владеет orchestrator (PR2a уже это делает),
> renderer'ы НЕ пишут в audit.jsonl — они только UI/broadcast.
> `JsonlRenderer` как класс **удалён** из плана. Persistence —
> ответственность core, отображение — ответственность renderers.
> См. [`ADR/ADR-002-audit-jsonl-ownership-orchestrator.md`](../../ADR/ADR-002-audit-jsonl-ownership-orchestrator.md).

```
  №   Модуль                               Ключевые задачи                  AC закрытие
  ──  ───────────────────────────────────  ───────────────────────────────  ──────────────
  1   renderers/base.py                     Renderer Protocol —              (фундамент
                                            __call__(event) → None.          для AC-21)
  2   renderers/silent_renderer.py          Тестовый: записывает события     (тесты)
                                            в list для проверок.
  3   renderers/rich_renderer.py            rich.live спиннер +              AC-21 (rich UI
                                            форматтированные блоки.           в Stop hook)
                                            Spec в ARCHITECTURE.md §2.9.
                                            (УБРАНО из плана:
                                             renderers/jsonl_renderer.py —
                                             audit.jsonl пишет orchestrator,
                                             см. ADR-002.)
  ──  ───────────────────────────────────  ───────────────────────────────  ──────────────
  4   transports/stop_hook.py               Entry point из Claude Code:      AC-2 (полный цикл
                                            читает stdin JSON,                без вмешат.)
                                            проверяет stop_hook_active,      AC-10 (timeout
                                            вызывает orchestrator,            → освобождение
                                            пишет decision: block в           lockfile)
                                            stdout. EventBus →
                                            RichRenderer (stdout).
                                            audit.jsonl уже пишется
                                            orchestrator'ом (ADR-002).
  5   transports/audit_watch.py             tail -f audit.jsonl с rich       AC-21 (live tail
                                            форматтированием. Используется    < 1 сек)
                                            как ccbridge audit watch.        AC-5/AC-12
                                            Tolerant к torn-write.            (через AuditLog)
                                            Watcher читает файл напрямую
                                            (между процессами), НЕ через
                                            in-process EventBus.
  ──  ───────────────────────────────────  ───────────────────────────────  ──────────────
  6   cli.py (Click)                        Команды:                         AC-1, AC-6, AC-7,
                                            - ccbridge init <path>            AC-8, AC-15
                                            - ccbridge audit run
                                            - ccbridge audit get [<uuid>]
                                            - ccbridge audit list
                                            - ccbridge audit watch
                                            - ccbridge status
                                            - ccbridge uninstall <path>
```

### Тесты PR2b

```
  Файл                                    Что покрывает
  ──────────────────────────────────────  ─────────────────────────────────────────────
  tests/unit/test_renderers.py             Каждый renderer на одном потоке событий.
                                            Silent — список накапливается.
                                            Rich — capsys, проверка ключевых маркеров.
                                            (Jsonl renderer удалён — см. ADR-002.)
  ──────────────────────────────────────  ─────────────────────────────────────────────
  tests/integration/test_stop_hook.py      stdin фейкового Claude JSON →
                                            decision: block в stdout. timeout simulation.
                                            stop_hook_active=true → no-op exit 0.
  ──────────────────────────────────────  ─────────────────────────────────────────────
  tests/integration/test_audit_watch.py    Параллельный writer → watcher видит < 1 сек.
                                            Torn-write → пропуск с warning.
  ──────────────────────────────────────  ─────────────────────────────────────────────
  tests/integration/test_cli.py            Click runner: каждая команда happy path.
                                            init: создание .ccbridge/, патч settings.json,
                                                  backup существующего settings.json (AC-15).
                                            audit run: с моками runners — полный цикл.
                                            audit list/get: rich-форматтированный output.
                                            uninstall: cleanup + восстановление backup.
  ──────────────────────────────────────  ─────────────────────────────────────────────
  tests/e2e/test_full_cycle.py             @pytest.mark.e2e — skip по умолчанию.
                                            Требует claude и codex в PATH.
                                            Запускает на тестовом проекте полный цикл.
```

### Acceptance PR2b (= конец PR2)

- pytest зелёный (107 + PR2a + PR2b)
- ruff clean, mypy strict ok
- Все AC из ARCHITECTURE.md §8 закрыты, кроме AC-7 (project_id
  стабильность) и AC-15 (settings.json merge) — эти зависят от PR3
  `ccbridge init` и могут быть проверены частично здесь.
- E2E тест работает локально (с реальным claude+codex в PATH)
- Реальный smoke-run на CCBridge самом себе (мини-задача,
  cycle проходит без багов)

### Коммиты PR2b

Аналогично PR2a — per-модуль (10-12 коммитов), merge `--no-ff`.

---

## Дисциплина (R-005, R-006, R-007)

- **TDD по умолчанию** (R-005): для каждого модуля сначала failing
  test, потом минимальная имплементация. Исключение: чистые dataclass'ы
  и Protocol'ы без логики.
- **Verification before completion** (R-006): после каждого модуля
  прогон `pytest --cov=ccbridge && ruff check . && mypy src/ccbridge`,
  только при зелёном — переход к следующему модулю.
- **Planning discipline** (R-007): этот PR2-plan + ROADMAP entry
  попадают в один коммит. Изменения объёма (например, выделение
  части PR2b в отдельный PR3) — обновление этого плана + ROADMAP
  в одном коммите.
- **No commits without approval** (R-001): merge каждого этапа
  (PR2a → main, PR2b → main) — только под explicit approval.

---

## Открытые точки

```
  Точка                                          Решение
  ─────────────────────────────────────────────  ─────────────────────────────────
  Реальный формат codex exec --json output        Откладываем до PR2a step 4.
                                                  Если нужно — спайк на 30 минут
                                                  с echo команды и чтением stdout.
  ─────────────────────────────────────────────  ─────────────────────────────────
  Поведение Stop hook на Windows (синхронный      Default 600s timeout обычно ок;
   subprocess блокирует?)                         если выяснится UX-проблема —
                                                  detached mode уходит в v0.2
                                                  (см. handoff §6).
  ─────────────────────────────────────────────  ─────────────────────────────────
  Cost tracking в audit.jsonl                     Откладываем в v0.2 — для PR2
                                                  достаточно cost_usd=0.0 как
                                                  placeholder в VerdictEvent.
```

---

## После PR2

Merge PR2b в `main` → tag `v0.1.0-rc1` (опционально) → переход к PR3
(templates + `ccbridge init --methodology`). См.
[`Projects/v0.1-mvp/README.md`](README.md) §PR3.
