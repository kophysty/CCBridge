# Decision Log — CCBridge

> Strategic / tactical decisions с контекстом и последствиями.
> **Append-only.** Старые записи никогда не редактируются —
> добавляется новая запись «Superseded by [date]».

> Architecture decisions (immutable, что-зачем) живут в `ADR/`.
> Активные правила кода живут в `Rulebook/`. Сюда — **нарратив**:
> как пришли к решению, какие альтернативы рассматривали,
> что обсуждали с пользователем. См.
> [R-008](../../Rulebook/R-008-workflow-logs-vs-rulebook.md).

---

## Формат записи

```markdown
### [YYYY-MM-DD] <Заголовок>

**Was:** Состояние до решения. Что нас не устраивало.
**Now:** Что изменилось. Что выбрали.
**Why:** Аргументация — конкретная боль, инсайт, ссылка на discussion.
**Impact:** Что это меняет — какие файлы, какие правила, что не делать.
```

---

### [2026-04-28] Создан проект CCBridge

**Was:** Соло-разработчик копипастил вручную diff из Claude Code в
Codex CLI и обратно. На каждой задаче — 5-15 минут на ручные операции,
теряется контекст между шагами, нет истории ревью.

**Now:** Создан проект `D:\Dev\CCBridge` для автоматизации этого цикла.
Решение: Python CLI tool, проектируется как переиспользуемый шаблон
для всех будущих проектов.

**Why:** Боль реальная и повторяющаяся. Похожие инструменты в open-source
(AutoForge — closer всех) либо слишком тяжёлые, либо AGPL, либо
single-feature. Свой минимальный CLI закрывает 80% боли.

**Impact:**
- Создан репозиторий с README, ARCHITECTURE.md.
- В Oil Automation у Вари добавлен Pending пункт «использовать CCBridge
  после v0.1.0 релиза» (Oil_auto будет первым проектом-потребителем).

---

### [2026-04-28] Architecture v0.0.1 → v0.0.2 после параллельного аудита

**Was:** Архитектурный драфт v0.0.1 содержал PID-based lockfile,
state.json как primary, Verdict без semantic validation, default
context_level=full, MCP заглушку в v0.1, mcp_server.py.

**Now:** Запущены 3 параллельных аудитора (Plan, systematic-debugging,
prompt-engineering). Применены 25 правок:
- Lockfile вынесен в `.ccbridge/lockfile` через portalocker
- audit.jsonl стал primary source of truth (state.json кэш)
- Verdict обогащён `model_validator` + `validate_semantics()`
- Default context_level = medium (full → opt-in)
- MCP отложен в v0.3, заглушка убрана
- Добавлены AC-9..AC-20

**Why:** Все три аудитора независимо нашли пересекающиеся P0/P1
проблемы. Без правок — баги в первую неделю на Windows и потеря
данных при крашах.

**Impact:**
- `ARCHITECTURE.md` v0.0.2 с обновлённым decision log
- `audit/2026-04-28-pre-implementation-audit.md` зафиксирован
- `templates/codex-system-prompt.md` создан
- ROADMAP отражает PR1 как Active

---

### [2026-05-02] UX слой добавлен (Wave-readiness)

**Was:** ARCHITECTURE.md v0.0.2 описывал бизнес-логику, но не описывал
UX. Пользователь спросил: «как это будет визуально, два терминала или
один?». Ответ требовал дополнения архитектуры.

**Now:** Добавлен §2.9 «UX & Event-driven rendering».
- EventBus + типизированные события (`core/events.py` + `event_bus.py`)
- Renderer'ы как listener'ы: rich (Stop hook stdout), jsonl (audit.jsonl),
  silent (тесты). WaveRenderer и MCPRenderer заложены через интерфейс
  на v0.2/v0.3.
- `ccbridge audit watch` — live tail второй терминал в v0.1
- AC-21 добавлен

**Why:** Пользователь хочет видеть «живой процесс» и планирует
переезд в Wave Terminal. Без отделения logic от UI пришлось бы
переписывать половину при добавлении WaveRenderer.

**Impact:**
- `ARCHITECTURE.md` v0.0.3
- В `core/` появятся два новых файла: `events.py`, `event_bus.py`
- Появится `renderers/` директория
- Появится `transports/audit_watch.py`
- v0.0.3 в decision log ARCHITECTURE.md

---

### [2026-05-02] Заимствование методологии из Oil Automation

**Was:** В CCBridge были только README + ARCHITECTURE + pyproject.
Не было Rulebook, ROADMAP, ADR, Discovery/logs, AGENTS.md.
Дисциплина процесса — ad-hoc.

**Now:** Применён Слой 1 из плана адаптации Oil Automation:
- `Rulebook/INDEX.md` + `R-000` (как добавлять) + 8 универсальных
  правил (R-001..R-008): commits-approval, debug-removal,
  ascii-tables, versioning-files, TDD-default,
  verification-before-completion, planning-discipline, logs-vs-rulebook
- `ROADMAP.md` (Active/Queued/Pending/Spikes/Frozen/Shipped)
- `ADR/` (README + ADR-001 «Python CLI not bash/MCP»)
- `Discovery/logs/` (этот файл, insights.md, conversation-log.md)
- `Projects/` структура (00-strategy/, cross-cutting/, v0.1-mvp/)
- `Output/` READ-ONLY
- `CHANGELOG.md` формата 0.X.Y

Слой 2 (boilerplate для `ccbridge init`) делается в PR3.

**Why:** Oil Automation за год выработал зрелые practices, многие из
них универсальны (TDD, planning discipline, ADR vs Rulebook). Не
переизобретать с нуля. Дисциплина применяется сразу к самой работе
над CCBridge — каждое решение через decisions, каждый план через
ROADMAP.

**Impact:**
- Все будущие правки следуют R-007 (plan-файл + запись в ROADMAP в
  одном коммите)
- PR1 теперь имеет план в `Projects/v0.1-mvp/README.md` и запись в
  ROADMAP как Active
- При создании `ccbridge init` команды (PR3) шаблон будет копией
  этой структуры в `templates/boilerplate-project/`

---

### [2026-05-02] Выбран B-scope (single-tool) с C-ready foundation

**Was:** Обсуждались три варианта scope:
- A: утилита для Вари (1-2 часа)
- B: generic CLI для любого проекта (6-8 часов)
- C: multi-project orchestrator с registry/dashboard (2-3 дня)

**Now:** Выбран **B**. C-ready foundation заложен через:
- `project_id` UUID (не path как identity)
- JSON-lines audit log (импорт в БД позже одним запросом)
- CLI команды как «API» (`audit run/get/list`) — обёртываются в HTTP
- Конфиг иерархия global → project
- Чёткое разделение `core/` ↔ `transports/`

**Why:** A не даёт переиспользования. C — overkill на старте, нужно
проверить что идея вообще работает. B — sweet spot.

**Impact:**
- ARCHITECTURE.md §4 «Roadmap B → C» с конкретными границами
- StateBackend abstraction, plugin system, event bus — НЕ заложены
  сейчас (YAGNI), но архитектура их добавление допускает
