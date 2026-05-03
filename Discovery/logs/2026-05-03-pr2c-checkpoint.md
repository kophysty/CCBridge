# PR2c — Checkpoint перед context compaction

**Дата:** 2026-05-03
**Ветка:** `pr2b/transports-cli`
**HEAD:** `5d198d3`
**Baseline:** 291 passed (3 e2e deselected), ruff clean, mypy strict ok, coverage ~91%
**Не пушено в origin:** ничего после `652059b` (предыдущий main).

> Этот файл — single point of truth для возобновления работы после
> compact. Содержит: текущее состояние, что уже закрыто, что осталось,
> ответы аудитора по открытым пунктам, и точный план substep 5
> (skip-review) с учётом всех corrections.

---

## 1. Что уже сделано на ветке (3 коммита поверх `985ed4e`)

```
  Commit     Что закрыто                                           Tests added
  ─────────  ───────────────────────────────────────────────────  ───────────
  985ed4e    docs(discovery): audit findings + Codex CLI survey    —
             [предыдущий чекпоинт-коммит]
  ─────────  ───────────────────────────────────────────────────  ───────────
  0b13fad    Audit Major #1 (backup overwrite on double init)        +4
             Audit Major #3 (bare command → absolute path)
             prereq: __main__ block в cli.py
             follow-up: legacy marker matching
  ─────────  ───────────────────────────────────────────────────  ───────────
  5d198d3    Audit Major #2 (config + identity wiring)               +14
             - Новый модуль transports/audit_invoker.py
             - Обa transport'а (cli + stop_hook) используют
               run_audit_with_config() как chokepoint
             - resolve_include_rules helper auto-detect
               literal/glob с edge cases
```

**Метрики до checkpoint:**

```
                       До PR2c    Сейчас      Δ
  Tests default         273         291        +18
  Tests e2e (opt-in)    3           3          0
  Coverage              91%         91%        ~
  Audit findings closed 0/4         3/4
  Open items            4           1 (Minor #4) + skip-review + #6
```

---

## 2. Что осталось в этап 1 (substep 5 + Minor #4 + #6)

### Substep 5 — Skip-review feature (A+C, ~65 мин)

User shape (запрошен пользователем 2026-05-03):
> Хотел бы добавить возможность запустить задачу с какой-нибудь
> командой, чтобы без аудита дополнительного. Выключить эту функцию
> возможность хочется, потому что фишки могут быть какими-то
> маленькими, или может быть просто мой какой-то банальный вопрос.

Принятая архитектура:

- **A:** маркер `[skip-review]` в **UserPromptSubmit.prompt** (НЕ в Stop
  transcript — security boundary: только пользователь может пропустить
  аудит). Новая subcommand `ccbridge prompt-hook` ловит UserPromptSubmit,
  пишет ephemeral marker в `.ccbridge/skip-review.json`. Stop hook
  consume'ит при матчинге `session_id`.
- **C:** `[review] skip_trivial_diff_max_lines = N` в config.toml.
  Default 0 (off). Если > 0 — Stop hook без Codex пропускает diff
  ≤ N changed lines.
- **A1 (skip = no Codex call вообще)**, не shallow review.

### Substep 5 — детальный план

```
  Substep  Что                                              Время
  ───────  ──────────────────────────────────────────────  ──────
  5a       transports/prompt_hook.py — новый модуль:        ~15 мин
           prompt_hook_main():
             reads stdin JSON
             detects [skip-review] marker (case-insensitive
               via .casefold())
             writes .ccbridge/skip-review.json
             empty stdout, exit 0 always (fail-open)

           Поля input (UserPromptSubmit per docs):
             session_id, transcript_path, cwd,
             permission_mode, hook_event_name, prompt

           Guardrails (от аудитора):
             - missing session_id → fail-open empty stdout
             - prompt not str → fail-open
             - marker file ТОЛЬКО при user marker
             - inline match anywhere в prompt
             - case-insensitive через casefold()

           tests/integration/test_prompt_hook.py:
             ~8 тестов
  ───────  ──────────────────────────────────────────────  ──────
  5b       Stop hook consume marker:                        ~10 мин
           transports/stop_hook.py:
             ПЕРЕД _resolve_project: проверка
               .ccbridge/skip-review.json
             match по session_id (transcript_path metadata only)
             TTL 30 min check (created_at)
             consume = delete file
             на match: empty stdout, exit 0, no run_audit
             fail-open:
               - битый файл → stderr warning + продолжаем
                 обычный audit path
               - delete fail → log + продолжаем

           tests/integration/test_stop_hook.py:
             +4 теста (matched, expired, mismatched session,
                       broken marker)
  ───────  ──────────────────────────────────────────────  ──────
  5c       Stop hook semantics fix #6:                      ~5 мин
           verdict=skipped → empty stdout
             (вместо continue:false stopReason)
           cli audit run остаётся без изменений
             (final_verdict=skipped в outcome JSON)

           tests/integration/test_stop_hook.py:
             update test_skipped_outcome_emits_continue_false
             → переименовать + перевернуть assertion
  ───────  ──────────────────────────────────────────────  ──────
  5d       config.toml fields:                              ~10 мин
           [review] skip_marker = "[skip-review]"
           [review] skip_trivial_diff_max_lines = 0

           core/config.py:
             ReviewSection +2 поля
             default skip_marker, skip_trivial_diff_max_lines

           Stop hook:
             если diff_lines <= skip_trivial_diff_max_lines
             И skip_trivial_diff_max_lines > 0 →
             empty stdout

           BUT: для этого Stop hook должен видеть diff_lines
             ДО run_audit. Нужно вынести pre-flight check
             в Stop hook (чтобы skip случился без вызова
             orchestrator). Или: orchestrator emit'ит signal
             что skipped по trivial-diff causing.

           Решение: добавить параметр в run_audit_with_config:
             min_diff_lines (skip if smaller).
             orchestrator/context_builder проверит: если
             diff_lines < min_diff_lines → ContextSkipped с
             reason="trivial_diff_below_threshold".
             Stop hook decision: skipped → empty stdout (5c).

           Это объединяет 5d с 5c — оба используют тот же
           empty-stdout path для skipped.

           tests:
             test_audit_run_skips_when_diff_below_threshold
             (CLI mode shows skipped в JSON)
             test_stop_hook_returns_empty_for_trivial_diff
             (Stop hook → empty stdout)
  ───────  ──────────────────────────────────────────────  ──────
  5e       cli init: add UserPromptSubmit hook entry        ~10 мин
           settings.json:
             hooks.UserPromptSubmit = [{
               matcher: "*",
               hooks: [{type: "command",
                        command: "<sys.executable> -m
                                  ccbridge.cli prompt-hook"}]
             }]

           Refactor: _patch_settings_json должен принимать
             hook_event_name parameter (Stop / UserPromptSubmit).
             ИЛИ два отдельных helper'а.

           Я бы сделал общий helper _patch_settings_hook(
             settings_path, event_name, marker_substring, force):
             - применять для обоих entry types
             - markers возвращать тоже параметризованные

           uninstall: удаляет оба (Stop + UserPromptSubmit
             ccbridge entries)
           idempotency: оба entry проверяются отдельно
           backup discipline: тот же fix как Major #1, не
             перезаписывать pre-CCBridge backup

           tests:
             init создаёт оба entries
             uninstall удаляет оба
             double init не дублирует ни один из двух
             init на legacy bare ccbridge stop-hook +
               отсутствие UserPromptSubmit → добавляется
               UserPromptSubmit + апгрейдится Stop
  ───────  ──────────────────────────────────────────────  ──────
  5f       cli prompt-hook subcommand                        ~5 мин
           @cli.command("prompt-hook")
           def prompt_hook():
               sys.exit(prompt_hook_main())

           tests:
             test_prompt_hook_subcommand_invokes_main
             test_prompt_hook_subcommand_propagates_exit_code
```

**Итого substep 5: ~55 мин (немного больше, чем оценка ~50 мин).**

### Substep 6 — Minor #4 (docs cleanup, ~10 мин)

```
  Файл                                  Что чинить
  ────────────────────────────────────  ─────────────────────────────────
  CHANGELOG.md:29                        обновить статусы PR2a/PR2b/audit
                                          fixes — все shipped, plus текущая
                                          PR2c работа на ветке
  src/ccbridge/cli.py:1 (docstring)      "PR2b step 6a: read/run-only
                                          commands" больше не точно —
                                          init/uninstall/stop-hook реализованы
                                          в 6b. После substep 5 + prompt-hook
                                          добавится. Полностью переписать
                                          docstring под актуальное состояние.
  ARCHITECTURE.md:572                    проверить ту строку (контекст
                                          уже исправил в f24cb53?), confirm
                                          consistency с §2.9 диаграммой
```

---

## 3. Ответы аудитора (закреплены 2026-05-03 после моего design check)

### #1 — UserPromptSubmit schema (подтверждена)

> По docs Claude Code UserPromptSubmit получает common fields плюс
> prompt; пример содержит session_id, transcript_path, cwd,
> permission_mode, hook_event_name, prompt. Stop получает
> stop_hook_active и last_assistant_message, но не user prompt.
> Источник: https://code.claude.com/docs/en/hooks

**Применение:** В `prompt_hook_main()` ожидаем JSON со всеми этими
полями. Hard-required для skip-review marker writing — `prompt` (str)
и `session_id`. Остальные — metadata.

### #2 — Marker match: case-insensitive через casefold()

> Делай через casefold() для prompt и skip_marker, без regex.
> Это достаточно удобно и без лишней магии: [skip-review],
> [Skip-Review], [SKIP-REVIEW] работают.

**Применение:**

```python
if skip_marker.casefold() in prompt.casefold():
    # detected
```

`casefold()` правильнее `.lower()` для unicode. regex избыточен.

### #3 — Marker location: inline anywhere в UserPromptSubmit.prompt

> Это нормально, потому что prompt принадлежит пользователю. Не
> искать marker в last_assistant_message и не искать по всему
> transcript.

**Применение:** substring match только по `payload.prompt`. Не
читать `transcript_path`. Не парсить `last_assistant_message`. Это
закрывает security boundary — Claude не может self-bypass review.

### Дополнительные guardrails для 5a

> - Если нет session_id или prompt не строка → fail-open, empty
>   stdout, stderr diagnostic максимум.
> - Marker file писать только при user marker.
> - Stop consume match по session_id; transcript_path хранить как
>   metadata, не hard-check.
> - TTL 30 min сразу.
> - Битый marker файл: stderr warning + продолжить обычный audit
>   path.

**Все эти пункты явно зашиваются в код substep 5a и 5b.**

### Подтверждение по #6

> skipped в Stop hook → empty stdout, как решили. CLI audit run
> без изменений.

**Применение:** substep 5c как описано выше. Объединяется логически
с 5d (trivial-diff skip также ведёт к verdict=skipped → empty
stdout).

---

## 4. Marker file schema

`.ccbridge/skip-review.json`:

```json
{
  "session_id": "<UserPromptSubmit.session_id>",
  "transcript_path": "<UserPromptSubmit.transcript_path>",
  "cwd": "<UserPromptSubmit.cwd>",
  "created_at": "<ISO8601 UTC>",
  "marker": "[skip-review]",
  "reason": "user_marker"
}
```

**Lifetime:** между UserPromptSubmit (write) и Stop (consume) одного
turn'а. После Stop — удалён. Stale-сценарии:

- Claude crash между UserPromptSubmit и Stop → marker остаётся
  привязанным к умершему session_id. Если новая session_id
  стартует — match не сработает. TTL 30 min дополнительно
  гарантирует что stale marker не накопится.
- Двойной UserPromptSubmit без Stop между ними (теоретически) →
  второй marker overwrite первый, OK поведение.

**Stop hook check sequence:**

```python
def _check_skip_marker(skip_path, hook_input) -> bool:
    """True if Stop should skip review per a valid skip marker.
    Always returns False on any error (fail-open per audit guardrail).
    """
    if not skip_path.exists():
        return False
    try:
        data = json.loads(skip_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _emit_diagnostic(f"skip-marker unreadable: {exc}; ignoring")
        return False

    # session_id is the hard match.
    marker_session = data.get("session_id")
    input_session = hook_input.get("session_id")
    if not marker_session or marker_session != input_session:
        return False

    # TTL check.
    try:
        created = datetime.fromisoformat(data.get("created_at", ""))
    except (TypeError, ValueError):
        return False
    age = datetime.now(UTC) - created
    if age > timedelta(minutes=30):
        # Expired; clean up but don't apply.
        try:
            skip_path.unlink()
        except OSError:
            pass
        return False

    # Match. Consume (best-effort delete).
    try:
        skip_path.unlink()
    except OSError as exc:
        _emit_diagnostic(f"could not delete skip-marker: {exc}")
    return True
```

---

## 5. Точное состояние файлов (для возобновления)

```
  Файл                                          Изменён?  Должен ли быть в PR2c?
  ────────────────────────────────────────────  ────────  ─────────────────────
  src/ccbridge/cli.py                            ✅ да     должен (substep 5e+5f)
  src/ccbridge/transports/audit_invoker.py       ✅ new    готов; возможно
                                                            min_diff_lines добавить
  src/ccbridge/transports/stop_hook.py           ✅ да     должен (5b, 5c)
  src/ccbridge/transports/prompt_hook.py          📋 NEW    создать (5a)
  src/ccbridge/core/config.py                    📋        обновить (5d)
  src/ccbridge/core/orchestrator.py              📋        возможно обновить (5d)
  src/ccbridge/core/context_builder.py           📋        возможно обновить (5d
                                                            если skip-trivial-diff
                                                            проверка там)
  tests/integration/test_cli_init.py             ✅ да     должен (5e tests)
  tests/integration/test_cli_uninstall.py        ✅ да     должен (5e tests)
  tests/integration/test_cli_config_wiring.py    ✅ new    готов; возможно
                                                            tests для skip_trivial_
                                                            diff_max_lines
  tests/integration/test_prompt_hook.py           📋 NEW    создать (5a tests)
  tests/integration/test_stop_hook.py             ✅ да     должен (5b, 5c tests)
  CHANGELOG.md                                    📋        обновить (Minor #4)
  ARCHITECTURE.md                                 📋        проверить (Minor #4)
```

---

## 6. Continuation prompt для возобновления

После compact, **первое что нужно сделать в новой сессии**:

```bash
cd D:\Dev\CCBridge
git status -sb              # должно быть pr2b/transports-cli, clean
git log --oneline -5         # последний коммит 5d198d3
.venv/Scripts/python.exe -m pytest -q   # 291 passed, 3 deselected
```

Затем прочитать **этот файл** (`Discovery/logs/2026-05-03-pr2c-
checkpoint.md`) полностью.

Затем продолжить с **substep 5a** (`transports/prompt_hook.py`).
План каждого substep'а уже зафиксирован выше с TDD red→green
дисциплиной.

После substep 5 + Minor #4 → повторный аудит → этап 2 (codex survey
integration + reason improvement Path A).

---

## 7. Ключевые open vs closed (на момент checkpoint)

```
  Аудит item                    Status
  ────────────────────────────  ──────────────────────────────────────
  Major #1 (backup overwrite)    ✅ closed in 0b13fad
  Major #2 (config wiring)       ✅ closed in 5d198d3
  Major #3 (bare command)        ✅ closed in 0b13fad
  Minor #4 (docs cleanup)         📋 pending (substep 6)
  Skip-review (A+C)              📋 pending (substep 5)
  Stop hook fix #6               📋 pending (substep 5c)
```

```
  Codex survey integration (этап 2 — после этапа 1 merge'а)
  ────────────────────────────────────────────────────────
  --output-last-message primary verdict channel    📋 этап 2
  System prompt skills section                     📋 этап 2
  Tool-usage caveats (binary search)               📋 этап 2
  Native retry layer учтён (наш default → 1)        📋 этап 2
  Reason improvement Path A                         📋 этап 2
```

---

**Конец чекпоинта. Готово к compact.**
