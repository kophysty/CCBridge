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

### [2026-05-03] Plan A confirmed — доводим архитектуру до конца, не упрощаем

**Was:** Перед PR2c этап 1 повторным аудитом возник вопрос: почему
"скрипт-передатчик результатов между двумя CLI" вырос в продукт с
~25 модулями, 323 тестами, 3 аудитами и неделей работы. Ответ был
дан подробно — мы построили не передатчик, а runtime для долгоживущего
peer-review pipeline'а: recovery model (audit.jsonl primary +
state.json кэш + lockfile), schema discipline (Pydantic verdict +
validate_semantics), Wave-readiness (event-driven), multi-transport
(CLI + Stop hook + UserPromptSubmit + audit_watch), hook lifecycle
hygiene (backup + idempotency + legacy upgrade), security boundaries
(stdout discipline + fail-open), cross-platform (Windows cp1251 +
PATHEXT + UTF-8 BOM), методология (Rulebook + ROADMAP + ADR + handoffs).
Каждый слой по отдельности оправдан реальным аудитом или smoke-test'ом.

Я предложил три варианта:
- **A:** доводим текущую архитектуру (~1.5–2ч на 4 blocker'а текущего
  аудита, потом финальный аудит, потом v0.1.0).
- **B:** урезать scope (выкинуть skip-review + UserPromptSubmit +
  audit_watch), релизнуть minimal v0.1.0.
- **C:** откат substep 5 и переписать в 80 строк без отдельного marker
  файла.

**Now:** Plan A подтверждён пользователем:
> «Я абсолютно за вариант А. Всё делаем по плану. Лучше сделать
> нормально и архитектурно делать масштабируемый продукт, а не сделать
> поделку, которая будет ломаться.»

**Why:**
- Слои не "лишние" — они закрывают реальные failure modes (5 race
  conditions в lockfile аудит P0-1, hallucinations в Codex P1-2,
  poisoned backup в Major #1, и т.д.). Урезание scope = откладывание
  тех же багов в production.
- Wave/MCP-readiness был осознанным выбором с ARCHITECTURE.md v0.0.1.
  Откат event-driven архитектуры = переписать через 1-2 версии.
- Skip-review закрывает реальный UX pain (мелкая правка → не запускать
  Codex). Удаление = потеря фичи + время уже потрачено.

**Impact:**
- Закрываем 4 blocker'а PR2c аудита (custom marker, marker file
  trust boundary, backup poisoning через --force, top-level entry
  deletion). +3 high/medium/minor.
- На Finding #2 (marker file в writable workspace) — нужна структурная
  правка: signed token / per-process channel / OS-level secret. Это
  +час проектирования сверху, потому что простой fix не закрывает
  заявленную security boundary.
- Документируем продакт-описание в `Projects/00-strategy/
  product-capabilities.md` — что умеем сейчас, куда движемся,
  чтобы будущие сессии видели полную картину без чтения 25 модулей.
- В будущих PR строже соблюдать "don't add features beyond what
  the task requires" (CLAUDE.md). Я расширял scope skip-review за
  пределы запрошенного — это и привело к 6 из 8 findings про неё же.

---

### [2026-05-03] PR2b аудит — разделение фиксов на 2 этапа + skip-review

**Was:** PR2b code-complete (273 tests passed, ruff/mypy clean) ушёл
на повторный аудит. Аудитор нашёл 4 findings (3 major + 1 minor) и
параллельно был выполнен Codex CLI survey по запросу пользователя.
Накопилось 3 пакета изменений: audit fixes, codex survey integration,
reason content improvement.

**Now:** Разделяем работу на 2 этапа в одной ветке pr2b/transports-cli:

- **PR2c-этап-1 (~75 мин):** все 4 audit findings + новая фича
  skip-review (CCBridge не запускает аудит для turn'ов, помеченных
  как "не аудировать"). После — повторный короткий аудит.
- **PR2c-этап-2 (~45 мин):** codex survey integration
  (--output-last-message primary, skills section в system prompt,
  binary-search caveat, native retries учёт) + reason improvement
  Path A (summary + severity counts в decision:block).

**Why:** Меньше риска что я внесу скрытую регрессию большим patch'ем.
Аудитор может проверить fixes отдельно от features. Skip-review
запрошен пользователем для мелких правок где аудит overkill — это
маленькое и логически связано с config wiring (по сути ещё один
config knob).

**Impact:**
- Не мерджим PR2b → main до завершения этапа 1 + повторного аудита.
- PR3 (templates / Layer 2) сдвигается ещё на ~120 мин работы.
- В аудит-журнал (Discovery/logs/2026-05-03-pr2b-audit-findings.md)
  залогированы все 4 findings + ответ Codex CLI survey, со ссылками
  на конкретные line numbers.
- skip-review shape ещё обсуждается с пользователем (CLI flag vs
  prompt prefix vs settings option).

---

### [2026-05-02] audit.jsonl ownership = orchestrator (Variant A)

**Was:** В PR2a orchestrator сам append'ит события в `audit.jsonl`
перед `bus.emit`. PR2-plan.md §PR2b планировал `JsonlRenderer` как
listener, который тоже пишет в `audit.jsonl`. Это два writer'а на
один файл — либо дубли, либо размытая ответственность. Conflict
найден в аудите PR2a.

**Now:** Variant A: orchestrator владеет `audit.jsonl` append'ами,
EventBus только UI/broadcast канал, `JsonlRenderer` как класс
не реализуется в v0.1. Зафиксировано в `ADR/ADR-002-audit-jsonl-
ownership-orchestrator.md` (Accepted).

**Why:** ARCHITECTURE.md §2.4 прямо называет audit.jsonl primary
source of truth. Primary не может зависеть от lossy fire-and-forget
шины. Если bus.emit в JsonlRenderer крашнется — Codex-токены
потрачены, история пуста, контракт §2.4 нарушен. Variant A
эту проблему не имеет: один writer, явный failure path
(см. failure handling ниже). Trade-off — orchestrator знает про
файловый persistence — мнимый, потому что он уже знает про
state.json и lockfile (тоже persistence).

**Impact:**
- ADR-002 создан, ADR/README.md реестр обновлён.
- `Projects/v0.1-mvp/PR2-plan.md` §PR2b: JsonlRenderer удалён из
  списка модулей, добавлено примечание про ownership.
- ARCHITECTURE.md §2.9: уточнение что renderer'ы НЕ пишут в
  audit.jsonl (только UI broadcast).
- Failure handling в `orchestrator._emit`: если `audit_log.append`
  упал → ErrorEvent ТОЛЬКО на bus (НЕ в сломанный audit.jsonl),
  outcome.final_verdict="error", state очищен, lock освобождён.
  Это поправка к моему первоначальному предложению (где я
  обещал записать ErrorEvent в тот же сломанный sink — нельзя).

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
