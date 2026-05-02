# CCBridge

**Автоматизированный peer-review цикл между Claude Code CLI и OpenAI Codex CLI.**

> Status: 🟡 v0.0.1-draft — pre-implementation. Архитектура утверждена,
> ожидается аудит и реализация v0.1.

---

## Зачем это

Сейчас flow выглядит так:

```
Claude Code сделал работу
  → я копирую diff
  → вставляю в Codex CLI
  → читаю ответ
  → копирую обратно в Claude
  → Claude правит
  → опять копирую → Codex → ...
```

Это медленно и теряет контекст между шагами.

**CCBridge делает это автоматически:** Claude закончил → Codex автоматически
получил diff и контекст → Claude получил verdict как обычный tool result или
файл → продолжил работу. Без ручного copy-paste, со счётчиком итераций
и hard cap'ом, чтобы не зацикливаться.

## Suite принципы

1. **Code review — first-class operation.** В контейнере каждой задачи
   ревью встроен в pipeline, не как ритуал в конце.
2. **Стоп-условия по структурированному verdict, не по тексту.** Codex
   возвращает строгий JSON. Парсинг текста («всё ок», «looks good») —
   запрещён (антипаттерн из AutoForge).
3. **Project-agnostic.** CCBridge живёт отдельно от подключаемых проектов.
   `ccbridge init <project-path>` создаёт `.ccbridge/` в проекте,
   ничего не трогая в коде.
4. **C-ready foundation, B-scope сейчас.** Архитектура заложена под
   масштабирование в multi-project orchestrator (вариант C), но
   реализуем минимальный CLI-tool (вариант B). См. ARCHITECTURE.md.

## Quick Start (после релиза v0.1)

> ⚠️ Эти команды — целевой интерфейс. Сейчас в репо только документация
> и аудиторские отчёты. Код появится после фазы review.

```bash
# Установка
pip install ccbridge

# Подключить новый проект к CCBridge
cd D:\Dev\my-project
ccbridge init

# Запустить цикл (Claude работает, Codex автоматически ревьюит)
ccbridge audit run

# Посмотреть последний verdict
ccbridge audit get

# История ревью по проекту
ccbridge audit list
```

## Документация

### Для разработчиков CCBridge

- [ARCHITECTURE.md](ARCHITECTURE.md) — полная архитектура, design decisions, roadmap B → C
- [ROADMAP.md](ROADMAP.md) — что в работе, что следующее, что отложено
- [CLAUDE.md](CLAUDE.md) — инструкции для Claude Code agent
- [AGENTS.md](AGENTS.md) — инструкции для Codex / других AI agents
- [Rulebook/INDEX.md](Rulebook/INDEX.md) — активные правила процесса
- [ADR/](ADR/) — архитектурные решения (immutable)
- [Discovery/logs/](Discovery/logs/) — нарратив сессий, decisions, insights
- [docs/development.md](docs/development.md) — setup и команды разработки

### Пользовательская документация (TBD после v0.1)

- [docs/setup.md](docs/setup.md) — пошаговый install для нового проекта
- [docs/configuration.md](docs/configuration.md) — все ключи `.ccbridge.toml`
- [docs/audit-flow.md](docs/audit-flow.md) — диаграмма цикла peer-review

## Структура репозитория

```
CCBridge/
├── README.md                # ← вы здесь
├── ARCHITECTURE.md          # Архитектура (текущая v0.0.3)
├── ROADMAP.md               # Active / Queued / Pending / Shipped
├── CHANGELOG.md             # Релизы и [Unreleased]
├── CLAUDE.md                # Инструкции для Claude Code
├── AGENTS.md                # Инструкции для Codex / других AI
├── pyproject.toml
├── .gitignore
│
├── Rulebook/                # Активные правила процесса
│   ├── INDEX.md
│   ├── R-000-how-to-add-rules.md
│   └── R-001..R-008         # 8 универсальных правил
│
├── ADR/                     # Архитектурные решения (immutable)
│   ├── README.md
│   └── ADR-001-python-cli-not-bash-or-mcp-first.md
│
├── Discovery/               # Стенография процесса
│   ├── logs/
│   │   ├── decisions.md     # Append-only Was/Now/Why/Impact
│   │   ├── insights.md      # Наблюдения с источником
│   │   └── conversation-log.md
│   └── sources/             # Raw материалы
│
├── Projects/                # Планы фаз и спринтов
│   ├── README.md            # Где что лежит
│   ├── 00-strategy/         # Сквозные документы
│   ├── cross-cutting/       # Сквозная архитектура
│   └── v0.1-mvp/            # Текущая фаза
│
├── audit/                   # Аудиторские отчёты
│   └── 2026-04-28-pre-implementation-audit.md
│
├── docs/                    # Пользовательская документация
│   └── development.md
│
├── templates/               # Шаблоны
│   └── codex-system-prompt.md
│
├── Output/                  # READ-ONLY deliverables
│
├── src/ccbridge/            # Исходный код
│   ├── core/                # Логика оркестрации (transport-agnostic)
│   └── transports/          # CLI / Stop hook / audit watch / (v0.3) MCP
├── templates/               # Шаблоны для подключаемых проектов
│                            # (.ccbridge.toml, hook.sh, slash-команда)
├── tests/                   # pytest tests
└── audit/                   # Отчёты аудиторов и решения
```

## Структура внутри подключаемого проекта

После `ccbridge init` в проекте появляется:

```
my-project/
├── .ccbridge/
│   ├── config.toml          # Локальный конфиг (override глобального)
│   ├── state.json           # Текущая итерация, lockfile, last verdict
│   ├── audit.jsonl          # JSON-lines history всех ревью
│   └── last-review.json     # Последний verdict (для Claude /audit-loop)
├── .claude/
│   ├── settings.json        # Stop hook добавлен/обновлён
│   └── commands/
│       └── audit-loop.md    # Slash-команда для Claude
└── ... (остальные файлы проекта не тронуты)
```

## Принципы для масштабирования (B → C)

Если CCBridge вырастет в multi-project orchestrator (dashboard, история
по всем проектам, web UI):

- `project_id` стабильный (UUID, не path) → registry склеит проекты
- JSON-lines audit log → импортируется в БД одним запросом
- CLI команды спроектированы как «API» (`audit run/get/list`) →
  поверх можно повесить HTTP сервер без переписывания логики
- Конфиг иерархия `~/.ccbridge/global.toml` → `project/.ccbridge/config.toml`
- Чёткое разделение `core/` (логика) ↔ `transports/` (CLI / hook / MCP)

См. ARCHITECTURE.md → раздел «Roadmap B → C» для деталей.

## Когда добавлять MCP server

MCP даёт Claude возможность дёргать Codex review **в середине работы**,
а не только в конце через Stop hook. Полезно когда:
- Хочется ревью «выборочно» — не всего diff'а, а одного файла
- Хочется параллельно ревьюить несколько изменений
- Стоимость и токены Codex'а должны видеть в conversation usage

MCP — отдельный transport поверх той же `core/orchestrator.py`. Не
переделка, добавление. Roadmap: v0.3.

## Лицензия

TBD — после первой реализации. По умолчанию planning to use MIT.

## Связанные проекты

- **Claude Code** — https://github.com/anthropics/claude-code
- **OpenAI Codex CLI** — https://github.com/openai/codex
- **Анализ AutoForge** (откуда взяты паттерны failure counter / atomic state) —
  см. ARCHITECTURE.md → раздел «Заимствования»

## История

- **2026-04-28** — концепция, выбор архитектуры (Python CLI, B-scope, C-ready),
  старт документации и аудита
