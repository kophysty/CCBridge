# ROADMAP — CCBridge

**Единый реестр планов, спринтов и версий.**

> Это первая точка входа для любого человека/агента, начинающего
> работу над проектом. Здесь видно: что делаем сейчас, что
> следующее, что отложено, что уже сделано. Ссылки на все плановые
> документы.

**Обновление:** 2026-05-02 (PR2a + audit fixes shipped, PR2b started)
**Правило ведения:** [R-007 — planning discipline](Rulebook/R-007-workflow-planning-discipline.md)

---

## Как читать этот файл

```
  Колонка         Что означает
  ──────────────  ─────────────────────────────────────────────────
  Версия / ID      v0.X.Y или короткий ID спайка
  Статус           🚧 Active — делаем прямо сейчас
                   📋 Queued — следующее на очереди
                   ⏸ Pending — отложено до конкретного условия
                   🔬 Spike — исследование без коммитов кода
                   ✅ Shipped — в production
                   ❄ Frozen — остановлено, не возвращаемся без решения
  Plan             Ссылка на файл с планом. Если нет файла — это знак
                   что задача недооформлена (см. R-007)
  Trigger          Что должно случиться чтобы задача сдвинулась вперёд
```

---

## 🚧 Active — сейчас в работе

```
  Версия     Название                              Plan / Статус
  ─────────  ────────────────────────────────────  ─────────────────────────────
  v0.1-PR2b  Renderers + transports + cli          Plan: Projects/v0.1-mvp/
   (Active)   + integration + e2e tests             PR2-plan.md §PR2b
                                                    (обновлён под ADR-002 —
                                                    JsonlRenderer удалён)

                                                   Trigger: ✅ Audit OK,
                                                    fix/pr2a-audit-findings
                                                    merged (61dfbc5)

                                                   Branch: pr2b/transports-cli

                                                   Acceptance: AC-1, AC-2,
                                                   AC-5..AC-8, AC-10, AC-15,
                                                   полный AC-21. MVP работает
                                                   end-to-end.

  Слой 1     Методологическая структура            Plan: вытащено из Oil_auto
   (Active)   (Rulebook + ROADMAP + Discovery       — анализ в Discovery/logs/
              + ADR + CLAUDE/AGENTS)                2026-05-02-oil-auto-best-
                                                    practices-import.md

                                                   Trigger: ✅ выбран Вариант B
                                                   из плана адаптации

                                                   Acceptance: дисциплина
                                                   применяется к самой работе
                                                   (PR1 уже идёт по R-007)
```

---

## 📋 Queued — следующее по очереди

```
  Версия     Название                              Plan / Trigger
  ─────────  ────────────────────────────────────  ─────────────────────────────
  v0.1-PR3   Templates + ccbridge init             Plan: будет написан
              (Слой 2 — boilerplate для новых      после завершения PR2b
              проектов через ccbridge init
              --methodology=full)                  Trigger: PR2b merged

  v0.1.0     Финальный релиз MVP                   Все AC-1..AC-21 проходят,
              (объединение PR1+PR2+PR3,            tag в git, CHANGELOG
              tag, release notes)                  обновлён
```

---

## ⏸ Pending — отложено до конкретного условия

```
  Название                                    Условие возврата
  ──────────────────────────────────────────  ─────────────────────────────────
  v0.2 — UX polish + audit history             После 2 недель эксплуатации v0.1
   - cost tracking                              в реальном проекте (Oil_auto)
   - retry с backoff
   - детачнутый background mode (если          Plan: TBD
     синхронный hook upset пользователя)
  ──────────────────────────────────────────  ─────────────────────────────────
  v0.3 — MCP server                            Когда понадобится ревью в
   - transports/mcp_server.py                  середине задачи (не только
   - mcp/ optional dep                         в Stop hook)
                                               Plan: TBD
  ──────────────────────────────────────────  ─────────────────────────────────
  WaveRenderer                                 Когда CCBridge стабильно
   (renderers/wave_renderer.py)                работает в Wave Terminal
                                               (после личной миграции)
                                               Plan: TBD
  ──────────────────────────────────────────  ─────────────────────────────────
  v1.0 (C-scope)                               Если CCBridge вырастает в
   - Multi-project orchestrator                продукт. Триггер не определён.
   - SQLite/PG state, HTTP server, Web UI
```

---

## 🔬 Spikes — исследования без кода

```
  Имя                                         Plan / Результат
  ──────────────────────────────────────────  ─────────────────────────────────
  (нет активных)
```

---

## ❄ Frozen — остановлено

```
  (нет)
```

---

## ✅ Shipped — в production

```
  Версия     Когда         Что
  ─────────  ────────────  ──────────────────────────────────────────
  v0.1-PR1    2026-05-02    Core modules: events, event_bus, verdict,
   (push)                    lockfile, audit_log, state, migrations,
                             config. 107 тестов, coverage 97%, ruff
                             clean, mypy strict ok. + методологическая
                             структура (Слой 1: Rulebook + ROADMAP +
                             ADR + Discovery + CLAUDE/AGENTS).
                             Commit: b1edc23 на main → pushed в
                             github.com/kophysty/CCBridge.

  v0.1-PR2a   2026-05-02    Runners + context_builder + orchestrator
   (push)                    + 47 integration-тестов. Ядро peer-review
                             цикла работает: lockfile → context build
                             (git stash snapshot) → run_codex (retry/
                             backoff/lenient JSON) → Verdict + semantic
                             validation → audit.jsonl → state.json →
                             release lock. 164 теста total, coverage
                             95%, ruff clean, mypy strict ok.
                             Закрыто: AC-3, AC-4, AC-9, AC-11, AC-12,
                             AC-14, AC-18, AC-19, AC-20, частично
                             AC-21. Merge a740890 на main → pushed.

  Audit       2026-05-02    Two rounds of audit closed all blockers
   fixes                     before PR2b. ADR-002 (audit.jsonl owner-
   (push)                    ship), AuditPersistenceError class
                             (narrow OSError catch), codex JSONL
                             stream parsing (real codex 0.125.0
                             contract), prompt via stdin, --sandbox
                             read-only, Windows PATHEXT resolution
                             (shutil.which). 181 passed (+17 regr
                             tests), coverage 94%. Merge 61dfbc5 на
                             main → pushed.

  v0.1.0     (TBD)           Финальный релиз MVP — после PR2b + PR3.
```
