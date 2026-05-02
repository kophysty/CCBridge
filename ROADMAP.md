# ROADMAP — CCBridge

**Единый реестр планов, спринтов и версий.**

> Это первая точка входа для любого человека/агента, начинающего
> работу над проектом. Здесь видно: что делаем сейчас, что
> следующее, что отложено, что уже сделано. Ссылки на все плановые
> документы.

**Обновление:** 2026-05-02
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
  v0.1-PR1   Core modules                          Plan: Projects/v0.1-mvp/
   (Local     (verdict, events, event_bus,         README.md
   Complete)  lockfile, audit_log, state,
              migrations, config) + unit tests     ✅ Local Complete:
                                                   8/8 модулей готовы.
                                                   107 тестов проходят.
                                                   Coverage 97%, ruff clean,
                                                   mypy strict ok.

                                                   Ожидает: ОК пользователя
                                                   на коммит и старт PR2.

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
  v0.1-PR2   Orchestrator + runners + CLI          Plan: будет написан
              + transports/stop_hook.py            после завершения PR1
              + transports/audit_watch.py
              + renderers (rich/jsonl/silent)      Trigger: PR1 merged
              + integration tests                  + ОК пользователя

  v0.1-PR3   Templates + ccbridge init             Plan: будет написан
              (Слой 2 — boilerplate для новых      после завершения PR2
              проектов через ccbridge init
              --methodology=full)                  Trigger: PR2 merged

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
  (нет — первый релиз будет v0.1.0)
```
