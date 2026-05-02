# ADR-002 — audit.jsonl writes owned by orchestrator, not by a renderer

**Статус:** Accepted
**Дата:** 2026-05-02
**Авторы:** kophy + Claude

---

## Контекст

`ARCHITECTURE.md` §2.4 фиксирует ключевой инвариант:

> **audit.jsonl — primary source of truth, state.json — кэш.**

В §2.9 одновременно описана event-driven архитектура: orchestrator
эмитит `CCBridgeEvent`'ы на `EventBus`, а множественные renderer'ы
(rich, jsonl, silent, в будущем wave / mcp) подписываются как
listener'ы. Это даёт Wave-readiness и чистое разделение
"вычисление vs отображение".

При планировании PR2 эти два кадра вошли в конфликт. План
[`Projects/v0.1-mvp/PR2-plan.md`](../Projects/v0.1-mvp/PR2-plan.md)
§PR2b предполагал `JsonlRenderer` как listener, который пишет
события в `audit.jsonl` через `AuditLog.append`. Одновременно
PR2a-orchestrator (`src/ccbridge/core/orchestrator.py:_emit`) уже
сам пишет события в `audit.jsonl` перед `bus.emit(...)`.

При буквальной реализации PR2b мы получим **двух writer'ов** на
один и тот же файл, что либо приведёт к дублированию записей,
либо потребует размытой ответственности (orchestrator пишет
часть, renderer пишет часть).

Этот conflict выявлен в аудите PR2a (Discovery/logs/
2026-05-02-handoff-pr2a-audit.md, Major #4) до того, как
JsonlRenderer был написан.

## Решение

**Orchestrator владеет append'ами в `audit.jsonl`.** EventBus
остаётся **только** UI/broadcast каналом. JsonlRenderer как класс
не реализуется в v0.1.

Конкретно:

- `orchestrator._emit` атомарно делает `audit_log.append(event)`
  **перед** `bus.emit(event)`. Если append упал — это failure
  primary persistence layer; orchestrator переходит в error path
  (см. ADR-002 §Последствия).
- Renderer'ы (rich, silent, wave/mcp в будущем) — listener'ы
  EventBus, **без** доступа к `audit.jsonl`. Они отображают, не
  персистят.
- `transports/audit_watch.py` читает `audit.jsonl` напрямую с
  диска (`tail -f` стиль). Watcher НЕ подписан на in-process
  EventBus — он работает **между процессами**, и это правильно.

## Альтернативы

### Variant B: JsonlRenderer-owned audit log (отвергнута)

Orchestrator только эмитит на EventBus, ничего не пишет на диск.
JsonlRenderer subscribe'ится и записывает в `audit.jsonl`.
RichRenderer / WaveRenderer — другие listener'ы.

**Плюсы:**
- Чистое архитектурное разделение: orchestrator не знает про файлы.
- Persistence как listener расширяется (добавить SQLiteRenderer
  для v1.0 — symmetrically).
- Совпадает с дословным текстом PR2-plan.md §PR2b.

**Минусы (почему отвергнуто):**
- Orchestrator не может **гарантировать** что событие persisted.
  Listener может крашнуться, и orchestrator об этом не узнает,
  потому что bus.emit fire-and-forget по дизайну.
- При краше JsonlRenderer на iter `verdict_event` — Codex-токены
  потрачены, audit.jsonl пуст. Это нарушает §2.4 «audit primary
  source of truth».
- Failure handling сложнее: нужно либо превращать bus.emit в
  sync-with-ack (теряет KISS из §2.9), либо принять что primary
  source иногда теряется (теряет §2.4).
- Архитектурное «чистое разделение» — мнимая выгода: orchestrator
  уже знает про `state.json` и `lockfile` (тоже persistent state).
  `audit.jsonl` в той же категории, не в категории UI.

### Status quo: оба пишут (явно отвергнут)

Двойная запись или race conditions. Не рассматривается серьёзно.

## Последствия

### Положительные

- **Один writer → один источник правды.** Никаких race conditions
  между двумя путями записи в `audit.jsonl`.
- **Failure path чистый** (см. ADR-002-fixup в `_emit`):
  - Если `audit_log.append` упал → orchestrator выбрасывает
    fallback ErrorEvent **только на EventBus** (НЕ в сломанный
    audit.jsonl), помечает run как failed, очищает state, освобождает
    lock. Outcome.final_verdict = "error".
  - Renderer'ы получают ErrorEvent и показывают пользователю.
- **§2.4 соблюдается дословно**: каждое событие, попавшее в bus,
  гарантированно уже в audit.jsonl (или мы в error path).
- **Wave-readiness не пострадала.** WaveRenderer / MCPRenderer
  добавляются как listener'ы EventBus, не нуждаются в доступе к
  файлу.

### Отрицательные / trade-offs

- **Orchestrator знает про файловый persistence.** Это нарушает
  буквальный принцип §2.9 «разделение вычислений и отображения».
  Но ADR явно фиксирует: persistence ≠ отображение, audit.jsonl
  это persistence layer, не UI.
- **JsonlRenderer как класс не реализуется в v0.1.** Если в v0.2+
  захотим иметь альтернативный persistence sink (например,
  SQLite или HTTP POST в registry), это будет **второй writer**
  в orchestrator, не listener. Это контролируемая сложность, а не
  архитектурное расширение через bus.
- **PR2-plan.md §PR2b нужно обновить** — JsonlRenderer удаляется
  из списка модулей. Это делается одним коммитом в составе
  fix/pr2a-audit-findings.

### Что становится сложнее

- Добавить второй persistence backend в будущем (например, для
  multi-project registry в v1.0) — потребует либо второго writer'а
  в orchestrator, либо отдельного `audit_log_sinks` слоя
  внутри ядра. Это считается приемлемым: KISS на v0.1, известный
  путь миграции если C-scope станет реальностью.

## Связи

- [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.4 — audit.jsonl primary
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.9 — EventBus + renderers
  (уточнено: renderers не пишут в audit.jsonl)
- [`Projects/v0.1-mvp/PR2-plan.md`](../Projects/v0.1-mvp/PR2-plan.md)
  §PR2b — JsonlRenderer удалён, см. изменения в этом коммите
- [`Discovery/logs/2026-05-02-handoff-pr2a-audit.md`](../Discovery/logs/2026-05-02-handoff-pr2a-audit.md)
  — аудит, в котором conflict обнаружен (Major #4)
- [`Discovery/logs/decisions.md`](../Discovery/logs/decisions.md) —
  запись от 2026-05-02 «audit.jsonl ownership = orchestrator»
- [R-008](../Rulebook/R-008-workflow-logs-vs-rulebook.md) — Rulebook
  ≠ Discovery/logs ≠ ADR
