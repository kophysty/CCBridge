# CCBridge — Product Capabilities

> Что CCBridge умеет на каждой версии и куда движется. Документ
> ориентирован на пользователя/стейкхолдера, не на разработчика
> (для разработки — `ARCHITECTURE.md`, `ROADMAP.md`, `Rulebook/`).

**Поддерживается:** обновляется при релизе каждой минорной версии
+ при значимых изменениях scope в активной фазе.

**Последнее обновление:** 2026-05-03 (PR2c этап 1 в работе)

---

## TL;DR

CCBridge — **runtime для автоматической peer-review между двумя
CLI AI агентами**. Сейчас это Claude Code (coder) ↔ OpenAI Codex
(reviewer). Архитектура event-driven, готовая к замене reviewer'а
на любой другой CLI/MCP-агент в будущем.

**Не «скрипт-передатчик»**, а полноценный pipeline-runtime с recovery,
схемной валидацией, мульти-транспортным UI и lifecycle-гигиеной для
hook-интеграций. См. §6 «Почему такая сложность» если непонятно
зачем столько слоёв.

---

## 1. Capabilities matrix по версиям

```
  Возможность                                        v0.0.x   v0.1.0   v0.2    v0.3
  ─────────────────────────────────────────────────  ──────   ──────   ─────   ─────
  ── Core peer-review pipeline ─────────────────────────────────────────────────────
  Запуск Codex после Claude Stop                       ⏳       ✅       ✅      ✅
  Pydantic Verdict со schema validation                ⏳       ✅       ✅      ✅
  Semantic validation (file/line/rule_id matching)     ⏳       ✅       ✅      ✅
  Hard cap на итерации (default 3)                      ⏳       ✅       ✅      ✅
  Verdicts: pass / fail / needs_human / error /         ⏳       ✅       ✅      ✅
   skipped
  ── Recovery model ────────────────────────────────────────────────────────────────
  audit.jsonl как primary source of truth              ✅       ✅       ✅      ✅
  state.json как кэш с recovery из audit.jsonl         ✅       ✅       ✅      ✅
  Cross-platform lockfile (portalocker) + TTL stale    ✅       ✅       ✅      ✅
   recovery 30 мин
  Atomic JSON writes (tempfile + os.replace)           ✅       ✅       ✅      ✅
  Schema-versioned migrations                          ✅       ✅       ✅      ✅
  ── CLI ───────────────────────────────────────────────────────────────────────────
  ccbridge audit run / list / get                       ⏳       ✅       ✅      ✅
  ccbridge audit watch (tail второго терминала)         ⏳       ✅       ✅      ✅
  ccbridge status                                       ⏳       ✅       ✅      ✅
  ccbridge init / uninstall (Claude Code wiring)       ⏳       ✅       ✅      ✅
  --json mode (strict JSON stdout, ANSI-free)          ⏳       ✅       ✅      ✅
  ── Claude Code integration ────────────────────────────────────────────────────────
  Stop hook (auto-review после каждого turn)           ⏳       ✅       ✅      ✅
  UserPromptSubmit hook (для skip-review)              ⏳       ✅*      ✅      ✅
  Decision JSON contract (block / continue / silent)   ⏳       ✅       ✅      ✅
  Fail-open discipline (любая внутр. ошибка → silent)  ⏳       ✅       ✅      ✅
  Recursion guard (stop_hook_active=true → no-op)      ⏳       ✅       ✅      ✅
  ── Skip-review ────────────────────────────────────────────────────────────────────
  Trivial diff threshold (skip_trivial_diff_max_lines)  ⏳       ✅*      ✅      ✅
  User-typed [skip-review] marker                       ⏳       ✅*      ✅      ✅
  Custom marker через config.toml                      ⏳       ✅*      ✅      ✅
  ── UI rendering ──────────────────────────────────────────────────────────────────
  Rich UI (terminal colours, tables, badges)           ⏳       ✅       ✅      ✅
  Event-driven render layer (renderer protocol)        ✅       ✅       ✅      ✅
  audit_watch tail (live во втором терминале)          ⏳       ✅       ✅      ✅
  Wave Terminal renderer (wsh badges, tab signals)     —        —        ✅      ✅
  MCP renderer (verdict как tool_result)               —        —        —       ✅
  ── Configuration ────────────────────────────────────────────────────────────────
  TOML config (global ~/.ccbridge + project)            ✅       ✅       ✅      ✅
  Project name / project_id (identity.json)            ✅       ✅       ✅      ✅
  include_rules (literal + glob auto-detect)           ⏳       ✅       ✅      ✅
  context_level: minimal / medium / full                ✅       ✅       ✅      ✅
  Hard caps (max_iterations, max_diff_lines)           ✅       ✅       ✅      ✅
  Skip-review config (skip_marker, threshold)          ⏳       ✅*      ✅      ✅
  Methodology templates (через ccbridge init           —        —        ✅      ✅
   --methodology=full)
  ── Reviewer agnosticism ──────────────────────────────────────────────────────────
  Codex CLI runner (subprocess JSONL events)           ⏳       ✅       ✅      ✅
  Claude CLI runner (subprocess JSON output)           ⏳       ✅       ✅      ✅
  Pluggable reviewer interface                          —        —        🔬      ✅
  MCP-based reviewer (любой MCP server)                 —        —        —       ✅
  ── Cross-platform ───────────────────────────────────────────────────────────────
  Windows (cp1251 console + PATHEXT codex.cmd          ⏳       ✅       ✅      ✅
   resolution + UTF-8 BOM strip)
  Linux / macOS                                         ⏳       ✅       ✅      ✅
  ── Methodology layer (Слой 2) ───────────────────────────────────────────────────
  Rulebook + ROADMAP + ADR + Discovery шаблоны          —        —        ✅      ✅
  ccbridge init --methodology=full (boilerplate         —        —        ✅      ✅
   для нового проекта)
```

Легенда: `✅` shipped · `✅*` shipped в текущей фазе с открытыми
audit-finding'ами · `⏳` в процессе текущей версии · `🔬` spike ·
`—` не запланировано в этой версии.

---

## 2. Что умеет ПРЯМО СЕЙЧАС (на 2026-05-03)

```
  Слой           Что работает end-to-end
  ─────────────  ───────────────────────────────────────────────────────
  Pipeline        Claude → diff → Codex → verdict → block/continue/silent
                  → loop (до 3 итераций) → final verdict в audit.jsonl
  Recovery        Lockfile + atomic writes + state recovery из audit.jsonl
                  при крашах. Stale lock через 30 мин. Schema migrations.
  CLI             ccbridge audit run/list/get/watch, status, init,
                  uninstall, stop-hook, prompt-hook. Все с --json mode.
  Hooks           Stop hook (block on fail / continue:false on
                  needs_human/error/lockbusy / silent on pass/skipped),
                  UserPromptSubmit hook (записывает skip-marker).
  UI              RichRenderer в CLI stdout (или Stop hook stderr —
                  чтобы не порушить decision JSON). audit_watch для
                  live-tail во втором терминале.
  Config          TOML с иерархией global → project, BOM strip, type
                  validation. include_rules с auto-detect literal/glob.
                  Skip-review fields (но см. open audit findings).
  Tests           323 теста (107 unit + 216 integration), coverage ~91%,
                  ruff/mypy strict clean, e2e тесты (3 opt-in).
  Methodology     Rulebook 8 правил, ROADMAP, 2 ADR, Discovery/logs
                  для нарратива, handoffs/checkpoints для cross-session.
```

---

## 3. Open audit findings (не пускают в v0.1.0 release)

На 2026-05-03 PR2c этап 1 находится под повторным аудитом:

```
  ID         Severity    Описание                                   Status
  ─────────  ──────────  ─────────────────────────────────────────  ─────────
  Blocker 1  Critical     prompt_hook игнорирует config.skip_marker   📋 to fix
                          — использует только default `[skip-review]`
  Blocker 2  Critical     Marker file в writable workspace —         📋 to fix
   security                любой процесс с write-доступом может       (требует
                          подделать marker и обойти audit             перепроект.)
  Blocker 3  Critical     Backup poison: init→init --force →         📋 to fix
                          uninstall оставляет CCBridge entries в
                          restored backup
  Blocker 4  Critical     uninstall удаляет весь parent entry        📋 to fix
                          settings.json, теряя пользовательские
                          hooks в том же entry
  High 5     High         consume не обязателен — unlink fail        📋 to fix
                          → marker reusable
  Medium 6   Medium       future timestamp проходит TTL              📋 to fix
   (clock-skew)
  Minor 7    Low          shell quoting только пробелы (нужен        📋 to fix
                          shlex.quote/list2cmdline)
  Minor 8    Low          docstring stop_hook stale (skipped         📋 to fix
                          → continue:false устарело)
```

После закрытия — повторный аудит, потом merge в main.

---

## 4. Ближайшая дорожная карта

```
  Версия    Что добавляется                                    ETA
  ────────  ────────────────────────────────────────────────  ──────────────
  v0.1.0    Закрыть PR2c аудит → final аудит → merge → tag    1-2 дня после
            • all 8 audit findings закрыты                     PR2c аудита
            • coverage не падает ниже 90%                       (текущий
            • документация (CHANGELOG, README quick start)     spike)

  v0.2      Codex survey integration (этап 2):                Когда v0.1.0
            • --output-last-message как primary verdict        стабилен
              channel
            • System prompt skills section (concrete names)
            • Tool-usage caveats (binary search exclude)
            • Native retry layer учтён (наш default → 1)
            Reason improvement Path A:
            • summary + severity counts в decision:block
            Methodology layer (Слой 2):
            • ccbridge init --methodology=full
            • Boilerplate Rulebook/ROADMAP/ADR в новый проект
            Wave Terminal renderer (wsh badges)

  v0.3      Reviewer-agnostic interface (replace Codex)        После
            MCP-based reviewer support                         feedback'а
            MCP renderer (verdict как tool_result)             v0.1.0/v0.2
```

---

## 5. Use cases (для кого и зачем)

```
  Кто                         Что получает                                  Сценарий
  ──────────────────────────  ────────────────────────────────────────────  ─────────────────
  Solo developer              Auto-review каждого turn'а Claude Code        Claude закончил
                              без переключения на ChatGPT                   → Codex проверил
                                                                            → блок если плохо
  ──────────────────────────  ────────────────────────────────────────────  ─────────────────
  Solo dev на мелких правках  [skip-review] tag в prompt → audit            Claude фиксит typo
                              пропускается                                  → не нужен Codex
  ──────────────────────────  ────────────────────────────────────────────  ─────────────────
  Team / strict review        Hard caps (3 iter) → escalate to              Codex даёт fail
                              `needs_human` без infinite loop               два раза подряд
                                                                            → human разбирается
  ──────────────────────────  ────────────────────────────────────────────  ─────────────────
  Multi-turn debugging        audit.jsonl как audit trail, ccbridge         "Что Codex
                              audit get показывает все события              сказал в run X?"
  ──────────────────────────  ────────────────────────────────────────────  ─────────────────
  Two-terminal workflow       audit_watch в окне рядом с Claude Code        Reviewer "смотрит
                              для live-feed                                 за плечом"
  ──────────────────────────  ────────────────────────────────────────────  ─────────────────
  Future: Wave Terminal       Verdict как badge на табе, не блокируя         Тиха фоновая
                              workflow                                      проверка
  ──────────────────────────  ────────────────────────────────────────────  ─────────────────
  Future: Custom reviewer     Codex заменён на MCP server / другой CLI       Свой reviewer на
                              без изменения core                            local LLM
```

---

## 6. Почему такая сложность

(Из обсуждения 2026-05-03 — фиксируется здесь, чтобы будущие
сессии и стейкхолдеры понимали rationale без копания в коде.)

Изначальная задача звучит как "запусти Codex после Claude и передай
вердикт". Это `subprocess.run` в 50 строк. Но мы добавили **8 слоёв**,
каждый из которых закрывает реальный класс багов:

```
  Слой                          Что закрывает                           Без него ломается
  ────────────────────────────  ──────────────────────────────────────  ─────────────────────────
  1. Recovery model              Crash посередине audit'а               state.json в xpaths
                                                                        + state.json corrupt
  2. Schema discipline           Codex hallucinations                   Claude чинит файлы
                                                                        которых нет
  3. Wave-readiness / EventBus   Замена UI без переписывания core       Caller depends on
                                                                        конкретный rich.print
  4. Multi-transport             CLI + Stop hook + UserPromptSubmit +   stdout/stderr collisions,
                                 audit_watch — четыре разных stdin/     decision JSON corruption
                                 stdout контракта
  5. Hook lifecycle hygiene      double init + legacy upgrade +         Backup poisoning,
                                 idempotency + rollback                 lost user hooks
  6. Security boundaries         Claude не может self-bypass review     Reviewer становится
                                                                        декорацией
  7. Cross-platform              Windows cp1251 + PATHEXT + UTF-8 BOM   Не работает на
                                                                        русской Windows
  8. Methodology layer           Cross-session continuity, новые        Каждая сессия с нуля,
                                 проекты быстро онбордят disciplines    методология теряется
```

**Короткое объяснение:** мы строим runtime для долгоживущего
peer-review pipeline'а с auto-recovery, а не однократный передатчик.
Каждый слой по отдельности оправдан реальным failure mode (часть из
них уже укусили — recovery model пришёл из аудита P0-3, Pydantic
verdict из P1-2, backup discipline из текущего blocker'а #3).

**Тradeoff:** выше кривая обучения, больше тестов, выше время
до первого релиза. **Ниже:** меньше production-инцидентов, проще
расширять (Wave / MCP / другие reviewer'ы), долгий жизненный цикл
кода. Решение принято осознанно (Plan A, 2026-05-03 в decisions.md).

---

## 7. Что НЕ входит в scope

```
  Не делаем (в v0.1, возможно никогда)
  ─────────────────────────────────────────────────────────────────────
  Web UI / dashboard. CLI + Wave/MCP renderer покрывают use case.
  Распределённый pipeline (несколько машин). Это локальный tool.
  Multi-tenant / RBAC. Single-user.
  Replay / undo audits. audit.jsonl read-only для consumer'а.
  Auto-fix кода (Codex предлагает изменения). Только review verdict.
  Custom system prompt UI. Файл в `templates/` редактируется руками.
  Real-time chat между Claude и Codex. Pipeline один-к-одному.
  Любые сетевые сервисы кроме API ключей в env vars.
```

---

## Связанные документы

```
  Документ                                  Что в нём
  ────────────────────────────────────────  ─────────────────────────────
  ARCHITECTURE.md                           Технический дизайн (модули,
                                            event flow, recovery model)
  ROADMAP.md                                Активные/queued/shipped версии
  README.md                                 Quick start для разработчика
  Discovery/logs/decisions.md               Append-only лог принятых
                                            решений с альтернативами
  Discovery/logs/insights.md                Наблюдения и инсайты
  Rulebook/INDEX.md                         8 активных правил кода
  ADR/                                      Immutable архитектурные
                                            решения (текущие: ADR-001,
                                            ADR-002)
```
