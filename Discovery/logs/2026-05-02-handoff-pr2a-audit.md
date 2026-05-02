# Handoff & Audit Package: PR2a → audit checkpoint

**Дата:** 2026-05-02
**Создан:** в конце сессии 2026-05-02 после merge PR2a
**Автор:** Claude Opus 4.7 (CCBridge dev session)
**Состояние репо:** main = `a740890`, синхронизирован с
`origin/main` (https://github.com/kophysty/CCBridge).

---

## TL;DR

PR2a code-complete и слит в `main`. Ядро peer-review цикла работает
end-to-end на интеграционных тестах: lockfile → context build (git
stash snapshot) → run_codex (retry/backoff/lenient JSON) → Verdict
+ semantic validation → audit.jsonl → state.json → release lock.

164 теста, coverage 95%, ruff clean, mypy strict ok.

**Не хватает для запуска на реальном проекте:** точки входа
(CLI/Stop hook) и UI (renderers) — это PR2b. До этого pipeline
работает только в тестах с моками subprocess.

Этот документ — сопроводительная записка + аудит-чеклист.
Используй §3 для проверки кода глазами; §4 — для прогона
живой smoke-проверки; §5 — для решения куда дальше двигаться.

---

## 1. Что сделано в этой сессии

### Хроника

```
  Время        Что
  ───────────  ────────────────────────────────────────────────────────
  утро          PR1 push: коммит b1edc23, .gitignore + .env.example,
                git init + push на github.com/kophysty/CCBridge.
                ROADMAP/CHANGELOG обновлены, PR1 → Shipped.
  середина      Plan-коммит 360ed76: Projects/v0.1-mvp/PR2-plan.md
                с декомпозицией PR2a/PR2b, ROADMAP entry → Active.
  PR2a (TDD)    4 модуля + 47 integration-тестов на ветке
                pr2a/orchestrator-runners. Шаги:
                1. claude_runner — subprocess wrapper + JSON parse
                2. codex_runner  — retry/backoff + lenient JSON
                3. context_builder — git stash snapshot + pre-flight
                4. orchestrator — main loop + recovery model
                Каждый шаг = 2 коммита (feat + test), pytest зелёный
                после каждого шага.
  merge         git merge --no-ff pr2a/orchestrator-runners → a740890.
                git push origin main. Локальная ветка удалена.
```

### Граф коммитов (новое в этой сессии)

```
* a740890 Merge pr2a/orchestrator-runners — PR2a code-complete
|\
| * 5997bc1 docs: CHANGELOG entry for PR2a code-complete
| * b915970 test(core): integration tests for orchestrator (AC-3, AC-11, AC-12, AC-18)
| * ce736bb feat(core): orchestrator — main loop + recovery model
| * 8dbac47 test(core): integration tests for context_builder
| * c5d378b feat(core): context_builder — diff snapshot + pre-flight (AC-14, AC-18, AC-20)
| * 38e6b17 test(runners): integration tests for codex_runner (AC-4, AC-19)
| * 48c0a48 feat(runners): codex_runner — subprocess + retry/backoff + lenient JSON
| * 7abdeb4 test(runners): integration tests for claude_runner
| * 377a5cc feat(runners): claude_runner — subprocess wrapper + JSON parse
|/
* 360ed76 docs(plan): PR2 detailed plan + ROADMAP entry → Active
* b1edc23 PR1: core modules + методологическая структура (Слой 1)
```

---

## 2. Что добавлено: модули + тесты

### Файлы (всего +3036 строк)

```
  Слой / файл                                 LOC   Coverage   Что делает
  ─────────────────────────────────────────  ─────  ────────  ──────────────────────────────
  src/ccbridge/runners/__init__.py             1     100%      docstring
  src/ccbridge/runners/claude_runner.py       189    98%        Обёртка claude --print
                                                                 --output-format json
  src/ccbridge/runners/codex_runner.py        343    91%        Обёртка codex exec --json
                                                                 + retry/backoff на 429
                                                                 + lenient JSON parse
                                                                   (markdown fences +
                                                                    walk-the-braces)
  src/ccbridge/core/context_builder.py        443    95%        git stash create snapshot
                                                                 + numstat pre-flight (empty/
                                                                   binary/too-large)
                                                                 + сборка prompt
                                                                   (system + rules + diff +
                                                                    recent audits)
                                                                 + cache_hit signal (rules
                                                                   SHA-256 marker)
                                                                 + extract R-NNN для
                                                                   known_rule_ids
  src/ccbridge/core/orchestrator.py            495    92%        Main loop + recovery model:
                                                                 lockfile → for iter in 1..N
                                                                 → build_context → run_codex
                                                                 → Verdict.model_validate
                                                                 → validate_semantics
                                                                 → audit_log.append
                                                                 → save_state
                                                                 → release lock (всегда)
                                                                 audit.jsonl запись ВСЕГДА
                                                                 перед save_state.
  ─────────────────────────────────────────  ─────  ────────  ──────────────────────────────
  tests/integration/test_claude_runner.py     263    —          10 tests, monkeypatched
                                                                 subprocess.run
  tests/integration/test_codex_runner.py      404    —          25 tests (11 на парсер +
                                                                 6 happy + 3 failure +
                                                                 5 retry/timeout)
  tests/integration/test_context_builder.py   394    —          12 tests с реальным git
                                                                 в tmp_path
  tests/integration/test_orchestrator.py      485    —          10 tests с реальным
                                                                 lock/audit/state +
                                                                 stubbed run_codex
```

### Метрики (post-merge)

```
  Метрика                                Значение
  ─────────────────────────────────────  ────────────────────────────────
  Модулей в src/ccbridge/                  13 (8 core + 2 runners + 3 __init__)
  Тестов всего                              164 (40 unit + 124 integration)
  Test coverage                              95% (1048 stmts, 50 miss)
  pytest время                                ~5.5 сек
  ruff check src/ tests/                     ✅ All checks passed
  mypy --strict src/ccbridge                  ✅ no issues found in 16 files
  git push                                    ✅ origin/main = a740890
```

### Закрытые AC (из ARCHITECTURE.md §8)

```
  AC      Что                                              Где закрыто
  ──────  ──────────────────────────────────────────────  ──────────────────────────
  AC-3    3 fail-итерации → needs_human, lockfile          orchestrator.py +
           освобождается                                     test_orchestrator
  AC-4    Lenient JSON parse (markdown fences) + 1 retry    codex_runner.py +
           → verdict=error                                   test_codex_runner
  AC-9    Lockfile отдельным файлом (portalocker), не      lockfile.py (PR1) +
           поле в state.json. Stale lock TTL 30 мин          использование в orchestrator
  AC-11   audit.jsonl primary; state.json удалён →         orchestrator.py +
           реконструкция (новый run_uuid, прежние             test_orchestrator
           verdict'ы остаются в audit.jsonl)
  AC-12   Tolerant audit reader: torn last line skipped    audit_log.py (PR1) +
           с warning                                         test_orchestrator
                                                              (torn-write кейс)
  AC-14   Pre-flight diff size: > max_diff_lines →         context_builder.py +
           ContextTooLargeError                              test_context_builder
  AC-18   Empty / binary-only diff → verdict=skipped,      context_builder.py +
           Codex не вызывается                               orchestrator.py +
                                                              test_orchestrator
  AC-19   Network resilience: 429 detected в stderr →      codex_runner.py +
           retry с уважением Retry-After; backoff 1/4/16     test_codex_runner
           сек по умолчанию; max 3 retries
  AC-20   Diff snapshot: git stash create + копия в        context_builder.py +
           .ccbridge/iteration-<id>/files/                   test_context_builder
  AC-21   Event-driven: orchestrator emit'ит на EventBus,  orchestrator.py +
           audit.jsonl стрим. Renderer'ы из PR2b будут        test_orchestrator
           подписываться без правок ядра.                    (частично — без UI)
   (part)
```

### Намеренно НЕ покрыто в PR2a (уйдёт в PR2b)

```
  AC      Что                                              Зависит от
  ──────  ──────────────────────────────────────────────  ──────────────────────────
  AC-1    ccbridge init создаёт .ccbridge/ + патчит         cli.py + transports
           .claude/settings.json
  AC-2    ccbridge audit run проходит цикл без              cli.py
           вмешательства
  AC-5    audit.jsonl валиден после 100 итераций + краш     stress test (опционально
                                                              в PR2b)
  AC-6    ccbridge audit list — read-friendly история      cli.py + rich_renderer
  AC-7    project_id стабилен после переноса проекта        identity.json (PR1) +
                                                              cli.py init/status
  AC-8    Параллельный audit run × 2 — exit 2 «already      cli.py (lockfile сейчас
           running»                                          бросает LockBusyError —
                                                              CLI должен поймать и
                                                              превратить в exit 2)
  AC-10   Stop hook timeout — lockfile освобождается         transports/stop_hook.py
  AC-15   ccbridge init не ломает существующий               cli.py
           settings.json (merge + backup)
```

### Что НЕ закрыто намеренно в v0.1 в принципе

- **AC-13 (verdict semantic) полностью** — основная логика в `verdict.py` PR1,
  но CCBridge сам не валидирует *Issue.line ≤ длины файла*, если файл
  не находится в `file_line_counts` (мы ставим counts только для
  изменённых файлов). Это допущение.
- **Cost tracking** — поле `cost_usd=0.0` placeholder, реальный расчёт
  в v0.2 (см. PR2-plan.md «Открытые точки»).

---

## 3. Что проверить глазами (code review checklist)

Если хочешь сделать ручной аудит — пройдись по этому списку.
Каждый пункт привязан к коммиту/файлу.

### 3.1 runners/claude_runner.py (`377a5cc`)

```
  Пункт                                         Где смотреть
  ────────────────────────────────────────────  ──────────────────────────────────
  argv не содержит shell injection               run_claude → argv = [...]
   (нет shell=True, всё через list)              src/ccbridge/runners/claude_runner.py:130
  API key не передаётся через argv               argv не содержит env vars
  env=None по умолчанию → inherit от родителя    src/ccbridge/runners/claude_runner.py:139
   (env vars типа ANTHROPIC_API_KEY доходят)
  Все exception paths → ClaudeRunnerError        FileNotFoundError → :143
   с returncode/stdout/stderr/cause              TimeoutExpired   → :149
                                                  non-zero        → :157
                                                  invalid JSON    → :167
  JSON parsed object, не array/string            src/ccbridge/runners/claude_runner.py:173
   (защита от Codex hallucinations)
```

### 3.2 runners/codex_runner.py (`48c0a48`)

```
  Пункт                                         Где смотреть
  ────────────────────────────────────────────  ──────────────────────────────────
  extract_json_payload — безопасный walk-the-    _find_first_balanced_object
   braces, не падает на "{":"}" в строках        src/ccbridge/runners/codex_runner.py:148
  Markdown fence regex — case-insensitive,        _FENCE_RE = re.compile(...)
   non-greedy                                     src/ccbridge/runners/codex_runner.py:104
  429-детект — по тексту stderr (не по кодам),    _RATE_LIMIT_RE
   потому что codex CLI не возвращает HTTP        src/ccbridge/runners/codex_runner.py:296
   статусы напрямую. Нормально для CLI.
  Retry-After hint парсится, default backoff      _next_backoff
   1/4/16 если хинта нет                          src/ccbridge/runners/codex_runner.py:308
  json_retries отдельный счётчик от               run_codex переменные
   rate_limit_retries — оба ограничены             src/ccbridge/runners/codex_runner.py:209-211
  ⚠️ Реальный output formato `codex exec --json`   Не проверен. Спайк нужен —
   не проверен в продакшене (только моки)         см. §5 «Открытые риски»
```

### 3.3 core/context_builder.py (`c5d378b`)

```
  Пункт                                         Где смотреть
  ────────────────────────────────────────────  ──────────────────────────────────
  Pre-flight numstat: parsing табов, binary       _git_numstat
   detect "-\\t-\\t<path>"                         src/ccbridge/core/context_builder.py:213
  Initial-commit fallback — если HEAD нет,        _git_numstat fallback
   читаем --cached                                 src/ccbridge/core/context_builder.py:230
                                                  _git_diff fallback :260
  Path normalisation — все paths через            _normalise_path :309
   forward-slash (Windows-safety)
  diff_files и file_line_counts заполняются       build_context, цикл по text_items
   только для текстовых файлов (не binary)         src/ccbridge/core/context_builder.py:158-184
  rules SHA включает (path, content) tuples —     _read_rules
   переименование файла инвалидирует cache         src/ccbridge/core/context_builder.py:269
  cache_hit logic: marker file rules-cache.       _check_cache_hit + write
   sha256 в .ccbridge/                              src/ccbridge/core/context_builder.py:189-194
  recent_audits фильтр по run_uuid +              build_context :196
   последние N (default 3)
  cleanup_iteration вызывается из orchestrator    Удаляет .ccbridge/iteration-<id>/
   после успешного verdict_event                   после finish — НЕ оставляем
                                                    мусор. Тестируется через
                                                    test_orchestrator.
  ⚠️ ВНИМАНИЕ: snapshot_dir НЕ очищается на       Проверить orchestrator.py:
   error path (codex error, validation error,     cleanup_iteration вызовы только
   diff_too_large) — мусор может оставаться        в success path. Возможно
                                                    нужна очистка в finally?
                                                    См. §5 риск #2.
```

### 3.4 core/orchestrator.py (`ce736bb`)

```
  Пункт                                         Где смотреть
  ────────────────────────────────────────────  ──────────────────────────────────
  Lock через context manager — release           run_audit, with CCBridgeLock
   гарантирован даже при exception                src/ccbridge/core/orchestrator.py:144
  state.json clear в finally блоке               _run_loop, finally clause
   независимо от пути выхода                      src/ccbridge/core/orchestrator.py:339
  Порядок записи: audit_log.append → save_state  _emit (audit_log.append)
   (если crash между ними, verdict сохранён)     :379-385
  StartedEvent эмитится только на 1й итерации    started_emitted флаг
   с реальным diff (не на skipped)               :250
  IterationCompleteEvent эмитится в любом случае Конец _run_loop
   (даже на error path) — finalizer event          src/ccbridge/core/orchestrator.py:344
  TERMINAL_VERDICTS = {pass, needs_human,         orchestrator.py:90
   skipped, error} — fail НЕ терминал, цикл
   продолжается
  for...else: если три fail подряд (нет break)   :331
   → final_verdict = "needs_human" (AC-3)
  WarningEvent на recovered_stale_lock и         AC-9 partial coverage,
   semantic validation drops                      см. orchestrator.py:147 и :291
```

### 3.5 Архитектурное соответствие

```
  Принцип ARCHITECTURE.md                       Где видно
  ────────────────────────────────────────────  ──────────────────────────────────
  §2.4 audit.jsonl primary, state.json кэш      orchestrator order: append →
                                                  save → release.
  §2.4 atomic single-write JSON line             audit_log.py PR1 + orchestrator
                                                  использует через AuditLog.append
  §2.6 default context_level = medium            orchestrator.py:266 hardcoded
                                                  (config из PR1 пока не читаем —
                                                  это ок до cli.py в PR2b)
  §2.6 max_diff_lines pre-flight                 context_builder.py + orchestrator
                                                  передают max_diff_lines аргументом
  §2.8 Cross-platform paths normalisation        context_builder._normalise_path,
                                                  все diff_files в forward-slash
  §2.9 EventBus + множественные renderers        orchestrator emit'ит, listeners
                                                  пока только аудит-тесты;
                                                  renderers будут в PR2b
  §6.1 Secrets: только env, никогда в state/     code-проверка: codex_runner и
   audit/config                                   claude_runner не пишут API ключи
                                                  никуда; orchestrator не видит
                                                  ключей вообще
```

---

## 4. Smoke-проверка живьём

Если хочешь проверить, что всё запускается у тебя локально (а не
только в моих логах):

### 4.1 Базовый зелёный baseline

```bash
cd D:\Dev\CCBridge
.venv\Scripts\activate

pytest --cov=ccbridge -q
# Ожидаемо: 164 passed, coverage 95%

ruff check src/ tests/
# Ожидаемо: All checks passed!

mypy src/ccbridge
# Ожидаемо: Success: no issues found in 16 source files
```

### 4.2 Запустить orchestrator вручную (без CLI)

Можно прямо сейчас погонять цикл с фейковым Codex'ом — это
покажет работу пайплайна без ожидания PR2b. Сохрани этот скрипт
как `scripts/manual_smoke.py` (в репо его нет — он не нужен в коде):

```python
# scripts/manual_smoke.py — НЕ коммитить, для локальной проверки
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from ccbridge.core.event_bus import EventBus
from ccbridge.core.orchestrator import run_audit
from ccbridge.runners.codex_runner import CodexRunResult


def fake_codex(*, prompt, cwd, **kwargs):
    print(f"\n=== Codex got prompt ({len(prompt)} chars) ===")
    print(prompt[:500] + "...\n")
    return CodexRunResult(
        parsed={
            "schema_version": 1,
            "verdict": "fail",
            "summary": "found one minor issue",
            "issues": [{
                "severity": "minor",
                "category": "style",
                "file": "demo.py",
                "line": 1,
                "message": "consider docstring",
                "rule_id": "R-001",
            }],
            "verdict_confidence": 0.85,
            "issues_completeness": 0.9,
            "files_reviewed": ["demo.py"],
            "rules_checked": ["R-001"],
        },
        stdout="",
        stderr="",
        returncode=0,
        retry_count=0,
    )


def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "demo"
        repo.mkdir()
        for cmd in [
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@e.com"],
            ["git", "config", "user.name", "T"],
            ["git", "config", "core.autocrlf", "false"],
        ]:
            subprocess.run(cmd, cwd=repo, check=True)

        (repo / "demo.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "demo.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "init"], cwd=repo, check=True
        )

        (repo / "demo.py").write_text(
            "def f():\n    return 2  # changed\n", encoding="utf-8"
        )

        bus = EventBus()
        bus.subscribe(
            lambda e: print(f"[event] {type(e).__name__}: "
                            f"{e.model_dump(mode='json')}")
        )

        with patch("ccbridge.core.orchestrator.run_codex", fake_codex):
            outcome = run_audit(
                project_dir=repo,
                ccbridge_dir=repo / ".ccbridge",
                bus=bus,
                max_iterations=3,
            )

        print(f"\n=== Outcome ===")
        print(f"verdict={outcome.final_verdict}")
        print(f"iterations={outcome.iterations_used}")
        print(f"duration={outcome.duration_sec:.2f}s")

        audit = (repo / ".ccbridge" / "audit.jsonl").read_text(encoding="utf-8")
        print(f"\n=== audit.jsonl ===")
        for line in audit.splitlines():
            print(json.dumps(json.loads(line), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
```

Запуск:
```bash
python scripts/manual_smoke.py
```

Что увидишь:
- Полный prompt, который ушёл бы в Codex
- События на EventBus в реальном времени
- Содержимое audit.jsonl после прогона
- Финальный verdict

### 4.3 Проверить с реальным codex CLI (опционально)

⚠️ Стоит токены и требует `codex` в PATH. Можно отложить до PR2b
и e2e тестов.

Если хочешь сейчас — заменить `with patch(...)` на ничего, и
запустить с реальной задачей. Скорее всего будет одна из двух
проблем:
1. Format `codex exec --json` отличается от ожидаемого — `codex_runner`
   падает на parsing.
2. Auth/конфигурация codex — нужно сначала `codex auth`.

**Это и есть открытый риск №1** (см. §5).

---

## 5. Открытые риски и точки внимания

```
  №   Риск                                              Серьёзность   Когда решать
  ──  ─────────────────────────────────────────────────  ──────────  ──────────────
  1   Реальный output `codex exec --json` не проверен    🔴 высокая    PR2b или
       — мы основываемся на документации. Если формат                  отдельный спайк
       отличается, codex_runner потребует правок.                       (~30 мин).
                                                                        До этого все
                                                                        тесты на codex —
                                                                        это тесты на
                                                                        наши собственные
                                                                        моки.
  2   snapshot_dir НЕ очищается на error/skipped path    🟡 средняя    PR2b или
       (только на success). Может скапливаться мусор                    follow-up. Не
       в .ccbridge/iteration-<id>/ при многократных                     блокер MVP.
       краш-сценариях.
  3   iteration_id формата "<run_uuid>-<n>" — не       🟡 средняя    PR2b
       UUID. Это удобно для тестов, но AC-21 ожидает                    (audit_watch
       что iteration_id уникален между runs (для                        дисплей).
       audit watch). Проверь: возможно стоит UUID.
  4   StateRecovery: orchestrator.run_audit всегда       🟡 средняя    PR2b cli.py
       стартует НОВЫЙ run_uuid. Реконструкция из                        (status command
       audit.jsonl при удалённом state.json — это                       должен читать
       работа cli.audit status, не run_audit. Сейчас                     audit.jsonl
       нет CLI, так что AC-11 закрыт частично                            при отсутствии
       (test_orchestrator проверяет, что цикл                            state.json).
       работает после удаления state, но не что
       старый run_uuid восстанавливается).
  5   Stop hook на Windows — синхронный subprocess        🟡 средняя    PR2b
       блокирует Claude Code на до 600 сек.                              transports/
       Архитектура говорит "default 600s обычно                          stop_hook.py.
       достаточно", но на больших проектах может                        Если будет
       упереться. detached mode — v0.2.                                  проблема —
                                                                        будем решать.
  6   `cost_usd=0.0` placeholder во всех verdict          ⚪ низкая     v0.2 (cost
       events. Не блокирует функциональность.                            tracking).
  7   3 fail подряд → needs_human, но fail-counter        ⚪ низкая     PR2b (нужен
       НЕ сравнивает diff_blob_shas (как в                                для anti-loop
       ARCHITECTURE.md §2.3). Сейчас 3 разных                             discipline,
       fail на разных diff'ах тоже escalate'ятся.                         не для MVP).
       Это safer-default; жёсткое правило
       "unchanged + previous_fail" — в PR2b.
  8   Нет explicit graceful handling для                 🟡 средняя    PR2b или
       LockBusyError в orchestrator.run_audit.                            cli.py
       Сейчас он просто пробрасывается наружу,                           (поймать и
       что в тестах ловится pytest.raises. CLI                            превратить в
       должен поймать и преобразовать в exit 2                            exit 2 «already
       (AC-8).                                                            running»).
```

### Что НЕ риск (защищённые места)

- ✅ Race conditions через lockfile — закрыто portalocker, тестируется
  в `test_orchestrator::test_concurrent_run_blocks_with_lock_busy`.
- ✅ Torn write в audit.jsonl — tolerant reader, тестируется в
  `test_orchestrator::test_torn_last_line_does_not_break_next_run`.
- ✅ LLM sycophancy (verdict=pass + critical issue) — `Verdict`
  `model_validator` срабатывает, orchestrator превращает в
  `ErrorEvent`.
- ✅ Кириллица в путях / summary — round-trip тесты в
  `test_audit_log.py` и `test_codex_runner.py`.
- ✅ API ключи не утекают — runner'ы не пишут env в audit/state,
  orchestrator не видит ключей в принципе.

---

## 6. Что дальше — PR2b

После твоего OK на старт PR2b:

```
  Шаг   Модуль                              Что закрывает
  ────  ──────────────────────────────────  ──────────────────────────────
  1     renderers/base.py (Protocol)        Фундамент для AC-21
  2     renderers/silent_renderer.py        Тестовый
  3     renderers/jsonl_renderer.py         AC-21 stream-запись (по сути
                                             то же, что делает
                                             orchestrator._emit, но как
                                             listener — для разделения
                                             ответственности)
  4     renderers/rich_renderer.py          AC-21 rich UI в Stop hook stdout
  5     transports/stop_hook.py             AC-2, AC-10 (Stop hook)
  6     transports/audit_watch.py           AC-21 live tail
  7     cli.py (Click)                      AC-1, AC-6, AC-7, AC-8, AC-15
                                             (init, audit run/get/list,
                                             status, watch, uninstall)
```

После PR2b → реальный smoke с claude+codex в PATH → PR3 (templates +
init --methodology).

---

## 7. Что нужно от тебя для продолжения

```
  Действие                                      Что от меня нужно
  ────────────────────────────────────────────  ─────────────────────────────
  «PR2a OK, продолжай PR2b»                      Создаю ветку pr2b/transports-
                                                  cli, начинаю с renderers/base.py
                                                  + silent_renderer (TDD).

  «Найди баг X / поправь Y»                      Делаю на ветке fix/<scope>,
                                                  test first, потом merge в main.

  «Нужен спайк на codex CLI» (риск №1)            Делаю короткий branch
                                                  spike/codex-cli-format,
                                                  запускаю codex руками с
                                                  тестовым промптом, фиксирую
                                                  реальный output, обновляю
                                                  codex_runner если надо.

  «Покажи как это работает на реальном            Помогаю составить
   проекте сейчас»                                manual_smoke.py (см. §4.2)
                                                  и провести его на твоём
                                                  репо.
```

---

## 8. Полезные ссылки

### Внутри проекта

- [`README.md`](../../README.md) — суть проекта
- [`ARCHITECTURE.md`](../../ARCHITECTURE.md) v0.0.3 — полная архитектура
- [`ROADMAP.md`](../../ROADMAP.md) — текущий статус (PR2a Shipped)
- [`Projects/v0.1-mvp/PR2-plan.md`](../../Projects/v0.1-mvp/PR2-plan.md)
  — детальный план PR2a/PR2b
- [`audit/2026-04-28-pre-implementation-audit.md`](../../audit/2026-04-28-pre-implementation-audit.md)
  — pre-implementation audit (на старте проекта)
- [`Discovery/logs/2026-05-02-handoff-pr1-to-pr2.md`](2026-05-02-handoff-pr1-to-pr2.md)
  — предыдущий handoff (после PR1)
- [`templates/codex-system-prompt.md`](../../templates/codex-system-prompt.md)
  — то, что мы используем как SYSTEM_PROMPT (через context_builder)

### Внешние

- GitHub: https://github.com/kophysty/CCBridge — main = `a740890`
- Anthropic Claude Code hooks docs:
  https://docs.claude.com/en/docs/claude-code/hooks
- portalocker: https://pypi.org/project/portalocker/
- Codex CLI: https://github.com/openai/codex (для проверки реального
  формата `codex exec --json`)

---

## 9. Финал

PR2a — это ~70% работы v0.1 (PR1 был ~30%). Осталось ~30% (PR2b)
— это UI, CLI и точки входа. Архитектурно ничего нового они не
добавляют, только склейку с существующим ядром.

После PR2b на CCBridge можно будет запустить реальный peer-review
цикл командой `ccbridge audit run` или автоматически через Claude
Code Stop hook. PR3 (templates + `ccbridge init --methodology`) —
это удобство для подключения новых проектов, не функциональность.

Спасибо за дисциплину «полный скоуп без срезов» — фундамент
получился крепкий, прохождение PR2b будет быстрым именно из-за
того, что 95% трудных мест уже решены и под тестами.

— Claude
