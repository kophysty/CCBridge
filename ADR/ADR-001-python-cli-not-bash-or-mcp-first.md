# ADR-001 — Python CLI вместо bash, Node.js или сразу MCP server

**Статус:** Accepted
**Дата:** 2026-04-28
**Авторы:** kophy

---

## Контекст

При проектировании CCBridge нужно было выбрать транспорт / runtime
для оркестрации цикла Claude Code → Codex → verdict → Claude:

- Соло-разработчик на Windows 11
- Поддержка нескольких проектов (CCBridge задумывается как переиспользуемый)
- Главная боль — ручной copy-paste между двумя CLI

Рассмотрено четыре варианта.

## Решение

**Python CLI tool (`ccbridge`)** на Python 3.11+, кросс-платформенно
(`pip install ccbridge`).

## Альтернативы

### Bash + jq

**Плюсы:** минимум зависимостей, короткий MVP.

**Минусы:**
- Windows работает только через Git Bash или WSL → не «one click»
- JSON-парсинг через `jq` хрупкий, особенно для structured Verdict
- Stop-condition по тексту (антипаттерн из AutoForge `agent.py:272`)
  легче случайно реализовать

→ Отвергнуто.

### Node.js CLI

**Плюсы:** официальный MCP SDK на TypeScript, лучшая интеграция с
будущим MCP server в v0.3.

**Минусы:**
- Дополнительный runtime для пользователей, у которых может быть
  только Python окружение
- Nodе/npm экосистема в Windows менее предсказуема

→ Отвергнуто.

### MCP server сразу (вместо CLI)

**Плюсы:** Claude видит Codex как обычный tool, нет hooks вообще.

**Минусы:**
- Сложнее debug в фазе MVP
- Overengineering — сначала надо проверить, работает ли идея вообще
- MCP добавляется поверх Python CLI без переделки (новый transport)

→ Отложено в v0.3.

## Последствия

**Положительные:**

- Pydantic для Verdict schema из коробки
- `subprocess` обёртка над обоими CLI без headache
- `tomllib` (3.11+) для конфига встроен
- `pip install ccbridge` для пользователей
- Один и тот же код работает на Linux/macOS/Windows
- MCP server в v0.3 добавляется как новый `transport`, не переписывая
  `core/`

**Trade-offs:**

- Нужен Python venv у пользователя (vs zero-deps bash)
- На Windows разработчики без Python должны его поставить — но
  3.11+ обычно уже есть как dependency других tools

## Связи

- ARCHITECTURE.md §2.1 «Транспорт»
- ARCHITECTURE.md §3 «Module layout» (`core/` ↔ `transports/` разделение
  именно ради добавления MCP без переделки)
- audit/2026-04-28-pre-implementation-audit.md (P0-1, P0-3 — почему bash
  не справился бы с lockfile/atomic операциями)
