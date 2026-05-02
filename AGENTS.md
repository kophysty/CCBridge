# AGENTS.md — Repository Guidelines for AI agents

Этот файл — **peer** для [`CLAUDE.md`](CLAUDE.md). Если ты Codex /
ChatGPT / другой AI агент, работающий с этим репозиторием — читай
этот файл.

> Если ты Claude Code — читай CLAUDE.md (там детальнее, но та же
> дисциплина).

---

## Project Layout

```
CCBridge/
├── src/ccbridge/        # Источник
│   ├── core/             # Transport-agnostic logic
│   ├── transports/       # CLI / Stop hook / audit watch / (v0.3) MCP
│   ├── renderers/        # rich / silent / (v0.2) wave / (v0.3) mcp
│                          # (NB: persistence в audit.jsonl делает orchestrator,
│                          #  не renderer — см. ADR-002)
│   └── runners/          # Обёртки claude_runner / codex_runner
├── tests/
│   ├── unit/             # Без I/O
│   ├── integration/      # С tmp_path
│   └── e2e/              # С реальными CLI (claude / codex в PATH)
├── templates/            # codex-system-prompt + (v0.3) boilerplate-project
├── Rulebook/             # Активные правила процесса
├── ADR/                  # Архитектурные решения
├── Discovery/logs/        # Стенография
├── Projects/              # Планы фаз
└── audit/                # Аудиторские отчёты
```

---

## Build & Test Commands

```bash
# Setup
uv pip install -e ".[dev]"        # рекомендуется
# или: pip install -e ".[dev]"

# Все тесты
pytest

# Быстрые unit
pytest tests/unit

# Integration (требует tmp_path)
pytest tests/integration

# E2E (требует claude и codex в PATH)
pytest tests/e2e -m e2e

# Coverage
pytest --cov=ccbridge --cov-report=term-missing
```

## Linters & Type Checks

```bash
ruff check .
ruff format .                      # auto-fix
mypy src/ccbridge
```

**Pre-commit (всё разом):**

```bash
ruff check . && ruff format --check . && mypy src/ccbridge && pytest
```

---

## Coding Style

- **Python 3.11+**, type hints везде (`disallow_untyped_defs = true`)
- **100-символьная** ширина строки (handled by ruff format)
- **Docstrings** только когда WHY неочевидно. Не описывать WHAT —
  имена и так говорят
- **Никаких emoji** в коде. В .md только если пользователь явно просил
- **Cleanup discipline:** не удалять debug-логи без согласования —
  см. [R-002](Rulebook/R-002-workflow-no-debug-log-removal-without-approval.md)
- **Markdown tables:** ASCII с выровненными колонками — см.
  [R-003](Rulebook/R-003-preference-ascii-tables.md)

---

## Documentation Hierarchy

См. [R-008](Rulebook/R-008-workflow-logs-vs-rulebook.md) полностью.

```
  Слой               Что туда                                       Когда читать
  ─────────────────  ────────────────────────────────────────────   ─────────────────
  Rulebook/R-NNN.md  Активные правила кода/процесса                  Перед задачей
  ADR/ADR-NNN.md     Архитектурные решения (immutable)               Перед крупным
                                                                      архитектурным
                                                                      изменением
  Discovery/logs/    Нарратив (decisions/insights/conversation)      Для контекста
   *.md
  Projects/          Планы фаз/спринтов                              После ROADMAP
  ROADMAP.md         Active / Queued / Pending / Shipped             Каждую сессию
  ARCHITECTURE.md    Текущая архитектура                             При реализации
```

**Versioning документов:** не перезаписываем оригинал —
[R-004](Rulebook/R-004-preference-versioning-files.md). Существенная
правка → `<doc>-v2.md`, в шапке `Заменяет: v1`.

---

## Coordination Discipline

### Перед каждой сессией

1. Читай [`ROADMAP.md`](ROADMAP.md) — секция Active
2. Читай последний `Discovery/logs/*-checkpoint-*.md` (по mtime),
   если есть — там handoff с прошлой сессии
3. Сверься с [`Rulebook/INDEX.md`](Rulebook/INDEX.md) — таблица
   «Обязательные сверки перед задачей»

### Во время сессии

- **Plan-файл = запись в ROADMAP в одном коммите**
  [R-007](Rulebook/R-007-workflow-planning-discipline.md).
  Без записи план невидим — следующая сессия пропустит.
- **Решения, обсуждения, альтернативы** → `Discovery/logs/decisions.md`
  (Was/Now/Why/Impact)
- **Активные правила процесса** → `Rulebook/R-NNN-*.md` через
  [R-000](Rulebook/R-000-how-to-add-rules.md)
- **Архитектурные решения (immutable, что-зачем)** → `ADR/ADR-NNN.md`

### Конец сессии

- Обновлён `[Unreleased]` в `CHANGELOG.md`
- Если значимые наработки → `Discovery/logs/<date>-checkpoint-*.md`
  для следующей сессии

---

## Commit Guidelines

**Никогда** не делать `git commit`, `git push`, `git reset --hard`,
`git rebase`, изменения `.git/config` **без явной команды пользователя**.

См. [R-001](Rulebook/R-001-workflow-no-commits-without-approval.md).

«Явная команда» = «коммить» / «commit this» / «push» / прямой эквивалент.
Не «продолжай», не «готово».

### Формат коммитов

```
<type>(<scope>): <короткое описание>

<тело — что/зачем — без описания того, что Codex/Claude сделал на чужом проекте>
<ссылки на ROADMAP запись / Rulebook правило / ADR>
```

Типы: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

Scope: `core`, `cli`, `transports`, `renderers`, `runners`, `rulebook`,
`docs`, `tests`, `audit`.

**Пример:**

```
feat(core): R-005 verdict semantic validation

- Добавлен validate_semantics() с file/line/rule_id checks
- 22 unit-теста (включая edge cases)
- effective_verdict downgrade при confidence < 0.7

Связано с ROADMAP v0.1-PR1, R-005 (TDD).
```

---

## Definition of Done

См. [R-006](Rulebook/R-006-workflow-verification-before-completion.md).

Перед заявлением «готово»:

1. **Что именно проверено** (конкретная команда)
2. **Реальный вывод** (последние 10-30 строк stdout, не парафраз)
3. **Соотношение к утверждению** («тесты прошли — это покрывает X,
   но Y не проверено»)

**Нельзя:** «готово, тесты проходят» без вывода.

---

## Skills Reference

Используются скиллы (синхронизировано с [`CLAUDE.md`](CLAUDE.md)):

```
  Скилл                              Когда применять
  ─────────────────────────────────  ─────────────────────────────────
  test-driven-development             Новые модули в core/ и transports/
                                       (R-005)
  systematic-debugging                Баги / регрессии /
                                       pre-implementation failure mode
                                       review
  verification-before-completion      Перед заявлением «готово / fixed»
                                       (R-006)
  prompt-engineering-patterns         Для templates/codex-system-prompt
                                       и его адаптаций
  dispatching-parallel-agents         Для аудитов / исследований
                                       (3 параллельных subagent)
  subagent-driven-development         Для plan'а с независимыми задачами
                                       в одной сессии
```

---

## Security

- **API ключи (Codex / Claude)** — **только в env переменных**
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`)
- В `config.toml` — только имя env-ключа (`api_key_env = "OPENAI_API_KEY"`)
- **Запрещено:** хранить ключи в `config.toml`, `state.json`,
  `audit.jsonl`, любых tracked в git файлах
- **Hooks security:** Stop hook выполняется с правами пользователя.
  Скрипты в `.claude/settings.json` должны быть в read-only локациях
  (Anthropic docs warning)

---

## Не делать (consolidated)

- ❌ Коммитить без явного approval (R-001)
- ❌ Удалять debug-логи без спроса (R-002)
- ❌ Создавать plan-файл без записи в ROADMAP (R-007)
- ❌ Писать success-claims без evidence (R-006)
- ❌ Парсить свободный текст LLM как stop-condition
- ❌ Хранить API ключи в config.toml / state.json / audit.jsonl
- ❌ Создавать `transports/mcp_server.py` сейчас (v0.3)
- ❌ Делать абстракции `Coder`/`Reviewer` сейчас (YAGNI)
- ❌ Перезаписывать ARCHITECTURE.md / план-файлы значимыми правками
  (R-004 — новая версия, не overwrite)
- ❌ Использовать `git rebase -i` (interactive — не работает с
  AI агентами)

---

## Quick reference paths

```
ROADMAP.md                              ← всегда читать первым
ARCHITECTURE.md                          ← текущая архитектура
Rulebook/INDEX.md                       ← перед кодом
Discovery/logs/conversation-log.md      ← нарратив сессий
Discovery/logs/decisions.md             ← решения с обоснованием
audit/                                   ← аудиторские отчёты
templates/codex-system-prompt.md         ← system prompt для Codex
```
