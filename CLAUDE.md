# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

---

## 🗺 Навигация

- **⭐ ПЕРЕД ЛЮБОЙ СЕССИЕЙ** — прочитать последний
  `Discovery/logs/*-handoff-*.md` или `*-checkpoint-*.md` (по mtime)
  если есть. Это точка синхронизации с предыдущими сессиями.
  **Текущий актуальный handoff:**
  [`Discovery/logs/2026-05-02-handoff-pr2a-audit.md`](Discovery/logs/2026-05-02-handoff-pr2a-audit.md)
  (после PR2a merge, ожидает аудита).
  Предыдущий: [`2026-05-02-handoff-pr1-to-pr2.md`](Discovery/logs/2026-05-02-handoff-pr1-to-pr2.md).
- **⭐ [`ROADMAP.md`](ROADMAP.md)** — **ВТОРОЕ ЧТО ЧИТАТЬ.** Единый
  реестр планов и версий: что делаем сейчас (Active), что следующее
  (Queued), что отложено (Pending), что уже в production (Shipped).
  Любая работа начинается со сверки с ROADMAP.
  Правило ведения: [R-007](Rulebook/R-007-workflow-planning-discipline.md).
- **[`README.md`](README.md)** — суть проекта, quick start
- **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — полная архитектура
  (текущая версия v0.0.3)
- **[`AGENTS.md`](AGENTS.md)** — peer-файл для Codex / других AI агентов
- **`ADR/`** — архитектурные решения (Architecture Decision Records).
  Перед крупным архитектурным изменением проверить, нет ли ADR по теме.
- **`Rulebook/`** — активные правила кода. См. ниже.
- **`Projects/`** — планы и спецификации фаз / спринтов
  (оглавление в [`Projects/README.md`](Projects/README.md))
- **`Discovery/logs/`** — стенография (decisions, insights, conversation)
- **`audit/`** — аудиторские отчёты (pre-implementation, post-mortem)
- **`templates/`** — шаблоны (codex-system-prompt, в будущем
  boilerplate-project)

---

## 🔖 Rulebook — ПЕРЕД КОДОМ СВЕРИТЬСЯ

**Активные правила проекта:** [`Rulebook/INDEX.md`](Rulebook/INDEX.md).

Это **реестр с приоритетами** (P0/P1/P2/P3) накопленного опыта.
Каждое правило — атомарный файл `Rulebook/R-NNN-*.md`.

### Обязательные сверки перед задачей

```
  Тип задачи                                  Сверить с
  ──────────────────────────────────────────  ─────────────────────
  Любые изменения в репо CCBridge              🟡 R-001 (commits)
                                                 R-008 (logs vs rules)

  Новый модуль / тест                           🟢 R-005 (TDD),
                                                 R-006 (verification)

  Удаление дебаг-логирования                    🟡 R-002

  План спринта / spike                           🟡 R-007 (ROADMAP entry)

  Markdown с таблицами                           ⚪ R-003 (ASCII tables)

  Создание новой версии файла                    ⚪ R-004 (не перезаписывать)

  Запись нового опыта (повторяющийся кейс)      R-000 (как добавить)
```

---

## 🔴 Critical Rules

### P1 (запрет действовать самостоятельно)

- **R-001 — Нет commits/push без явного approval.**
  [Полностью](Rulebook/R-001-workflow-no-commits-without-approval.md).
  Никогда не делать `git commit`, `git push`, `git reset --hard`,
  `git rebase`, изменения `.git/config` без явной команды
  пользователя в текущей сессии.
- **R-002 — Нет удаления debug-логов без согласования.**
  [Полностью](Rulebook/R-002-workflow-no-debug-log-removal-without-approval.md).
  Не удалять `print()`, `logger.debug`, `assert`, `# TODO/FIXME/DEBUG`
  без спроса.
- **R-007 — Plan-файл = запись в ROADMAP в одном коммите.**
  [Полностью](Rulebook/R-007-workflow-planning-discipline.md).
  Без записи в ROADMAP план невидим и теряется.

### P2 (workflow дисциплина)

- **R-005 — TDD по умолчанию для новых модулей в `core/` и `transports/`.**
- **R-006 — verification-before-completion.** Никаких success-claims
  без evidence (реальный pytest output / реальный exit code).
- **R-008 — Rulebook ≠ Discovery/logs.** Активные правила vs нарратив.

### P3 (preferences)

- **R-003 — ASCII-таблицы в .md** с выровненными колонками
- **R-004 — Версионный подход для документов** (v1 → v2, не перезаписываем)

---

## Project Overview

**CCBridge** — Python CLI tool для автоматизации peer-review между
Claude Code CLI и OpenAI Codex CLI.

**Текущая версия:** v0.0.3-draft (PR1+PR2a in main)
**Текущая фаза:** Audit checkpoint после PR2a → PR2b (transports + cli)

**Архитектура:** [`ARCHITECTURE.md`](ARCHITECTURE.md)

**Status: ACTIVE** — PR1 (core modules) и PR2a (runners +
context_builder + orchestrator) в main, push в GitHub. Ожидает аудита
перед стартом PR2b.

---

## Текущая задача (по ROADMAP)

См. [`ROADMAP.md`](ROADMAP.md) — секция Active.

```
  Audit       Аудит PR2a перед стартом PR2b          🚧 Active
   (2026-     (claude_runner, codex_runner,           Plan: Discovery/logs/
    05-02)     context_builder, orchestrator)         2026-05-02-handoff-
                                                       pr2a-audit.md

                                                      Trigger: ✅ PR2a merged
                                                       (a740890), pushed.
                                                      Acceptance: ОК на
                                                       старт PR2b.
```

---

## Архитектурные принципы (TL;DR)

1. **`core/` ничего не знает про конкретные CLI** — только абстракции.
   Конкретные обёртки в `runners/`, точки входа в `transports/`,
   UI в `renderers/`. Это даёт замену Codex на любой reviewer и
   готовность к MCP / Wave Terminal в v0.3 / v0.2.

2. **`audit.jsonl` — primary source of truth.** `state.json` — кэш.
   При краше после Codex но до записи state — recovery идёт из
   audit.jsonl.

3. **Verdict через Pydantic + semantic validation.** Никаких
   text-matching stop-conditions. Codex hallucinations отлавливаются
   `validate_semantics()` (file/line/rule_id mismatch → drop).

4. **Hard cap на итерации (3).** Default из config. Превышение → 
   `verdict=needs_human`, lockfile освобождается.

5. **Lockfile через `portalocker`,** не PID в state.json. TTL 30 минут
   для stale lock recovery.

---

## Engineering Skills (используем когда подходит)

- **`test-driven-development`** — для новых модулей в `core/` и
  `transports/`. Совпадает с R-005.
- **`systematic-debugging`** — для багов / регрессий / pre-implementation
  failure mode review. Совпадает с R-006.
- **`verification-before-completion`** — перед заявлением «готово».
  Совпадает с R-006.
- **`prompt-engineering-patterns`** — для `templates/codex-system-prompt.md`
  и его адаптации под конкретные проекты.
- **`dispatching-parallel-agents`** — для аудитов и исследований
  (`Plan + systematic-debugging + prompt-engineering` параллельно).

---

## Разделение источников знания

См. [R-008](Rulebook/R-008-workflow-logs-vs-rulebook.md) полностью.

```
  Слой                    Что туда                                      Когда читать
  ──────────────────────  ───────────────────────────────────────────  ──────────────────
  Rulebook/R-NNN-*.md     Активные правила кода и процесса              Перед задачей
   (атомарные файлы)
  ADR/ADR-NNN-*.md         Архитектурные решения (immutable, что-зачем)  Перед крупным
                                                                          архитектурным
                                                                          изменением
  Discovery/logs/         Нарратив (decisions / insights /               Для контекста
   *.md                    conversation)                                  при онбординге
  Projects/               Планы фаз / спринтов                            После ROADMAP
  ARCHITECTURE.md          Текущая архитектура                            При реализации
  README.md                Quick start                                    При запуске
```

---

## Команды разработки

```bash
# Установка для разработки
uv pip install -e ".[dev]"

# Тесты
pytest                          # все
pytest tests/unit               # быстрые unit
pytest tests/integration        # с tmp_path
pytest --cov=ccbridge            # с coverage

# Линтер
ruff check .
ruff format .

# Типы
mypy src/ccbridge

# Pre-commit (всё разом)
ruff check . && ruff format --check . && mypy src/ccbridge && pytest
```

См. [`docs/development.md`](docs/development.md) для полного setup.

---

## Что НЕ делать

- ❌ Коммитить без явного approval (R-001)
- ❌ Удалять debug-логи без спроса (R-002)
- ❌ Создавать plan-файл без записи в ROADMAP (R-007)
- ❌ Писать success-claims без evidence (R-006)
- ❌ Парсить свободный текст LLM как stop-condition (антипаттерн
  AutoForge `agent.py:272`)
- ❌ Хранить API ключи в config.toml / state.json / audit.jsonl
  (только env vars)
- ❌ Создавать `transports/mcp_server.py` сейчас — это v0.3
- ❌ Делать абстракции `Coder`/`Reviewer` сейчас — YAGNI, два
  конкретных runner'а достаточно

---

## История проекта

См. [`Discovery/logs/conversation-log.md`](Discovery/logs/conversation-log.md)
для нарратива по сессиям.
