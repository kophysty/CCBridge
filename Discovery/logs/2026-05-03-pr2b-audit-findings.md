# PR2b — повторный аудит и Codex CLI survey

**Дата:** 2026-05-03
**Состояние ветки на момент аудита:** `pr2b/transports-cli`,
HEAD `648c4ca` (после ruff fix amend).
**Тестовый baseline на момент аудита:** 273 passed, 3 e2e deselected,
coverage 91%, ruff clean, mypy strict ok.
**Версия Codex CLI на которой делался survey:** 0.125.0.

> Этот файл — immutable record двух событий: повторный аудит PR2b
> от пользователя и параллельный Codex CLI capability survey.
> Используется как evidence для PR2c-этап-1 и этап-2 коммитов.

---

## 1. Audit findings (от пользователя, повторный аудит)

### Major #1 — повторный init ломает uninstall

**Где:** `src/ccbridge/cli.py:629` (создание backup
`settings.json.ccbridge.bak`).

**Воспроизведение пользователя:**

```
exit_codes 0 0 0
backup_exists_after_second_init True
backup_contains_ccbridge True
settings_after_uninstall_contains_ccbridge True
```

**Причина:** backup создаётся в `_patch_settings_json` ДО проверки
idempotency (`already_present and not force`). При повторном init на
уже инициализированном проекте:
1. settings.json существует и содержит наш ccbridge entry
2. backup создаётся → `settings.json.ccbridge.bak` теперь содержит
   уже-пропатченный settings
3. idempotency check: already_present=True, не force → return
   `(False, True)` — patched=False, backed_up=True
4. uninstall видит backup → restore → settings.json вернулся в
   уже-пропатченное состояние → ccbridge stop-hook остался

**Fix shape (этап 1):**
- В `_patch_settings_json` создавать backup ТОЛЬКО если действительно
  собираемся писать (после idempotency check passes).
- Backup должен отражать pre-CCBridge состояние, не любое предыдущее.
- Regression test: `init → init → uninstall --yes` ⇒ ccbridge
  stop-hook отсутствует в settings.json.

---

### Major #2 — audit run / stop-hook игнорируют config.toml + identity.json

**Где:**
- `src/ccbridge/cli.py:220` (cli.audit_run → run_audit)
- `src/ccbridge/transports/stop_hook.py:185` (stop_hook → run_audit)

В обоих местах `run_audit(...)` вызывается только с
`project_dir, ccbridge_dir, bus, run_uuid`. Не пробрасываются:
- `project_id` (из identity.json) → AC-7 формально не закрыт
- `project_name` (из config.toml `[project] name`)
- `rules_paths` (из config.toml `[review] include_rules`) — Codex
  НЕ получает Rulebook автоматически
- `max_iterations` (из config.toml `[review]`) — hardcoded default
  3 в orchestrator
- `max_diff_lines` (из config.toml `[review]`) — hardcoded default
  2000 в context_builder

**Воспроизведение пользователя:**

```
identity_project_id 8a5449c8-51e8-4466-9353-4b090353eb8d
started_project_id ''
started_project_name 'untitled'
```

**Импликации:**
- AC-7 (project_id стабилен после переноса) фактически не закрыт —
  identity создаётся, но не появляется в audit events. Невозможно
  найти runs одного проекта при копировании папки между машинами.
- config.toml сейчас декоративен — пользователь редактирует но это
  не влияет на поведение.
- Rulebook не передаётся Codex'у даже если в config.toml указано.

**Fix shape (этап 1):**
- В `cli.audit_run` и `stop_hook._run_audit_for_hook`:
  ```python
  config = load_config(project_dir)
  identity = load_identity(ccbridge_dir / "identity.json")
  rules_paths = _resolve_include_rules(
      project_dir, config.review.include_rules
  )
  outcome = run_audit(
      project_dir=project_dir,
      ccbridge_dir=ccbridge_dir,
      bus=bus,
      run_uuid=...,
      project_id=identity.project_id if identity else "",
      project_name=config.project.name,
      rules_paths=rules_paths,
      max_iterations=config.review.max_iterations,
      max_diff_lines=config.review.max_diff_lines,
  )
  ```
- `_resolve_include_rules` — новая helper'ка: glob/path resolution
  относительно project_dir. include_rules в config.toml — список
  glob-паттернов (e.g. `["Rulebook/R-*.md"]`).
- Regression test: после `init` + edit `config.toml` (изменить
  project_name, добавить include_rules), запуск audit run → проверить
  что StartedEvent.project_id, project_name приходят правильные;
  ContextBuiltEvent.rules_count > 0 если include_rules дали матчи.

---

### Major / Security #3 — Stop hook command — bare `ccbridge stop-hook`

**Где:** `src/ccbridge/cli.py:574` (`_build_stop_hook_entry`).

**Issue:** В settings.json пишется `"command": "ccbridge stop-hook"`
без absolute path. Claude Code docs описывают hook command как
shell command, выполняется с правами пользователя; best practice —
absolute paths. Источник:
https://code.claude.com/docs/en/hooks

**Риск:**
- PATH hijack: если в PATH пользователя добавится подставной
  ccbridge ранее настоящего, Claude вызовет атакующий binary.
- Особенно актуально для project-level `.claude/settings.json`
  (хранится в git) — clone проекта на чужой машине + крафтнутый
  PATH = problem.
- На Windows shell-resolution через `cmd.exe` может найти неожиданные
  расширения.

**Fix shape (этап 1):**
- Записывать командой `sys.executable + " -m ccbridge.cli stop-hook"`.
  Это deterministic путь к python venv'у где сделан init, и тому же
  пакету. Длинно в settings.json, но auditable и безопасно.
- Альтернатива (отвергнута): `shutil.which("ccbridge")` — может
  вернуть путь который изменится после переустановки или потеряется
  при переключении venv'ов.
- Regression test: после `init`, settings.json `command` поле
  - содержит абсолютный путь
  - не содержит просто `ccbridge stop-hook` без префикса

**Open question (для пользователя):** Quoting on Windows. Если путь
содержит пробелы (`Program Files`), команда требует кавычек. Click +
Claude Code shell parsing — нужно проверить как Claude Code парсит
command field. На macOS/Linux пробелы редки в Python venv путях,
на Windows возможны. **Решение:** обернуть путь в double quotes
безусловно — universally safe.

---

### Minor #4 — docs stale перед merge

**Где:**
- `CHANGELOG.md:29` — показывает PR2a Active / PR2b Queued (устарело
  после shipping audit fixes 61dfbc5 и работы над PR2b).
- `src/ccbridge/cli.py:1` (module docstring) — упоминает "PR2b step
  6a", "deferred to 6b", и т.д. — это уже не актуально, init/
  uninstall реализованы.
- `ARCHITECTURE.md:572` — "stop hook stdout" формулировки которые
  должны были быть исправлены в коммите f24cb53 (RichRenderer
  destination clarification), но были пропущены.

**Не runtime blocker, но по AGENTS / Definition-of-Done — fixable
перед merge.**

**Fix shape (этап 1):**
- CHANGELOG: добавить секцию [Unreleased] с PR2b items, отметить
  что shipped в commit X (после реального merge).
- cli.py docstring: заменить "PR2b step 6a — read/run-only" на
  фактическое описание модуля (Click CLI с audit/init/uninstall/
  stop-hook/status команды).
- ARCHITECTURE.md:572 — конкретно проверить ту строку и привести
  к согласованности с диаграммой §2.9.

---

## 2. Codex CLI survey (запрос от пользователя, raw evidence)

**Полный raw-снимок:**
`C:\Users\kophy\AppData\Local\Temp\ccbridge-codex-cli-survey-evidence.txt`

**Версия:** codex-cli 0.125.0.

### Подтверждённые предположения

1. **argv `codex exec --json --sandbox read-only` через stdin
   работает.** Smoke test EXIT=0, last-message OK. Это валидирует
   текущий контракт `codex_runner.run_codex`.

2. **JSONL формат event stream подтверждён точь-в-точь.** Минимальный
   sample:
   ```jsonl
   {"type":"thread.started","thread_id":"019dead8-..."}
   {"type":"turn.started"}
   {"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"OK"}}
   {"type":"turn.completed","usage":{"input_tokens":23925,"cached_input_tokens":6528,"output_tokens":210,"reasoning_output_tokens":203}}
   ```
   Наш `extract_verdict_from_event_stream` парсит это правильно.

3. **`--output-last-message <file>` существует и работает.**
   Это подтверждает что в первом дизайне отказ от него был
   преждевременным. Этап-2: переходим на него как primary verdict
   channel, JSONL stdout оставляем как diagnostic stream.

4. **Config precedence:** CLI flag > profile > project `.codex/
   config.toml` > user `~/.codex/config.toml` > defaults. Подтверждает
   что **наследование codex config работает по дефолту** — мы НЕ
   передаём `--model`, codex использует `~/.codex/config.toml`
   пользователя (например `gpt-5.5 high`). CCBridge не должен
   override.
   Источник: https://developers.openai.com/codex/config-basic#configuration-precedence

5. **Native retry слой Codex'а:** `request_max_retries` default 4,
   `stream_max_retries` default 5 (источник: Codex official config
   reference). Наш `max_rate_limit_retries=3` — поверх native — даёт
   total до 12 attempts. **Этап-2 fix:** снизить наш default до 1
   как safety net, native retries уже есть.

### Новые знания, не учтённые в коде

6. **Stderr может содержать ERROR diagnostic при exit 0.** Real
   sample:
   ```
   ERROR codex_core::session: failed to record rollout items: thread ... not found
   ```
   JSONL stdout остаётся чистым. **Не считать stderr-error как
   failure при returncode == 0.** Текущий `codex_runner` уже корректно
   проверяет только returncode, **этап-2 действие:** добавить явный
   комментарий + опционально логирование stderr на WARNING когда
   returncode=0 но stderr непустой.

7. **`codex exec --help` flags inventory:**
   - `--model` / `-m`
   - `--sandbox` / `-s`
   - `--profile` / `-p`
   - `--skip-git-repo-check`
   - `--ephemeral`
   - `--ignore-user-config`
   - `--ignore-rules`
   - `--output-schema`
   - `--color`
   - `--json`
   - `--output-last-message` / `-o`

   **Нет flag'а** для timeout, max output tokens, retries, skill
   forcing/listing. Это значит Skills invocation остаётся **на
   усмотрение Codex'а** через model reasoning + system prompt —
   не можем форсировать.

8. **Skills installed на машине пользователя:** установлены в
   `~/.codex/skills/`. Поддерживается `[[skills.config]]` в
   config.toml для path/enabled. Среди installed:
   - architecture-principles
   - discovery-log
   - dispatching-parallel-agents
   - ocr-processor
   - pdf
   - security-best-practices
   - strategic-planning
   - subagent-driven-development
   - verification-before-completion

   **Этап-2 действие:** в `templates/codex-system-prompt.md`
   добавить секцию "Tool usage" с конкретными именами relevant
   skills и trigger conditions ("trigger when diff touches X").

9. **Subagents:** официальная documentation подтверждает
   `agents.<name>.description`, `agents.<name>.config_file`,
   `agents.max_threads`. У пользователя в локальном config.toml
   custom `[agents.*]` НЕ настроены. **Этап-2 действие:** в system
   prompt пишем общую формулировку про subagents без конкретных
   имён, добавляем ремарку что user's codex config может иметь свои
   agents.

10. **Footgun из survey #3 (nested codex):** при rg-поиске в
    project root Codex может попасть в бинарные файлы (включая
    `codex.exe` сам), получить огромный binary stream output и
    свалиться. **Этап-2 действие:** в system prompt добавить tool-
    usage caveat с явным указанием exclude binary в read tools
    (rg --type-not binary, find -not -path '*/.git/*' и т.д.).

11. **Approval flag:** `codex exec --ask-for-approval never` rejected
    в этой версии CLI; для CCBridge не критично если approval flag
    не нужен (мы и не используем).

12. **Skip-git-repo-check:** новый знакомый flag. Может быть полезен
    если CCBridge в будущем позволит auditing вне git-репо
    (currently context_builder требует git). **Этап-2 действие:**
    отметить как future option, в PR2c не использовать.

### Неизвестное (требует реального воспроизведения)

13. **Format 429 stderr / Retry-After hint.** Real 429 не
    воспроизведён в survey. Текущий `codex_runner._is_rate_limited`
    использует regex по тексту stderr (`r"\b429\b|rate[\s_-]?limit"`).
    Без real evidence — оставляем как есть, но **этап-2:** добавить
    TODO в коде на проверку при первом реальном 429.

### Sources used

- Local: `codex --help`, `codex exec --help`, codex features list,
  `~/.codex/config.toml`
- Official: https://developers.openai.com/codex/cli/reference
- Config docs: https://developers.openai.com/codex/config-reference#configtoml
- Config precedence: https://developers.openai.com/codex/config-basic#configuration-precedence
- Sandbox/approvals: https://developers.openai.com/codex/agent-approvals-security#sandbox-and-approvals

---

## 3. Skip-review (новая фича, запрошена пользователем)

**User request (verbatim, 2026-05-03):** "хотел бы добавить
возможность запустить задачу с какой-нибудь командой, чтобы без
аудита дополнительного. Выключить эту функцию возможность хочется,
потому что фишки могут быть какими-то маленькими, или может быть
просто мой какой-то банальный вопрос."

**Use case:** мелкий вопрос или тривиальная правка, аудит-лоп
overkill. Хочется ad-hoc opt-out на конкретный turn.

**Возможные shapes (нужно решить с пользователем):**

A. **Prompt prefix.** Пользователь начинает свой prompt с маркера
   типа `[ccbridge: skip]` или `/no-review`. CCBridge stop_hook видит
   маркер в hook input и no-op'ит. Минус: маркер должен прийти в
   stop_hook input — нужно проверить что Claude Code прокидывает
   prompt в stop event.

B. **Marker файл.** Пользователь делает `ccbridge mute` (новая CLI
   subcommand) → создаётся `.ccbridge/mute` файл с TTL. stop_hook
   проверяет файл, если есть и не expired — no-op. Minus: явный шаг,
   надо помнить unmute (или auto-expire).

C. **Settings option.** В `.ccbridge/config.toml` flag
   `skip_review_for_short_diffs = N` — если diff менее N строк, skip.
   Это автоматический threshold, не ad-hoc opt-out.

D. **Env var.** `CCBRIDGE_SKIP_REVIEW=1 claude ...` — но это требует
   пользователю запускать Claude из shell с env, неудобно.

**Моя рекомендация — комбинация A + C:**

- A: `/skip-review` в начале промпта (или в любом месте) — для
  ad-hoc.
- C: `min_diff_lines` опция в config.toml — для автоматических
  skip'ов когда diff меньше threshold (например, 3 строки).

A покрывает "банальный вопрос" / "мелкая правка по моей команде".
C покрывает "Claude сам сделал тривиальную правку" без участия
пользователя.

**Open questions для пользователя (этап-1 design):**
1. A vs B vs A+C? (Я бы A+C.)
2. Если A — формат маркера? `/skip-review`, `[no-review]`, `--no-audit`?
   Что чаще пишет пользователь?
3. Если C — default min_diff_lines? 0 (не отключаем по умолчанию) или
   3 (skip trivial)?

---

## 4. Plan для PR2c

### Этап 1 (~75 мин)

```
  Шаг   Что                                               Время    Closes
  ────  ────────────────────────────────────────────────  ───────  ─────────
  1     Major #1: backup только при actual write           10 мин   audit #1
        + regression test
  ────  ────────────────────────────────────────────────  ───────  ─────────
  2     Major #2: load_config + load_identity в            25 мин   audit #2
        cli.audit_run и stop_hook                                     AC-7
        + _resolve_include_rules helper                              proper
        + 3-4 regression tests
  ────  ────────────────────────────────────────────────  ───────  ─────────
  3     Major #3: absolute path в _build_stop_hook_entry   10 мин   audit #3
        + regression test
  ────  ────────────────────────────────────────────────  ───────  ─────────
  4     Minor #4: docs cleanup (CHANGELOG, cli.py           10 мин   audit #4
        docstring, ARCHITECTURE.md:572)
  ────  ────────────────────────────────────────────────  ───────  ─────────
  5     Skip-review (shape: A+C, ждём подтверждения):       20 мин   user
        - stop_hook check prompt for /skip-review marker             request
          → no-op exit 0 без audit
        - config.toml min_diff_lines, проверка в
          stop_hook + cli.audit_run
        - 3-4 tests
  ────  ────────────────────────────────────────────────  ───────  ─────────
        ИТОГО этап 1                                       ~75 мин
```

После этапа 1 — повторный короткий аудит от пользователя.

### Этап 2 (~45 мин, отдельным набором коммитов)

```
  Шаг   Что                                               Время
  ────  ────────────────────────────────────────────────  ───────
  6     codex_runner: --output-last-message primary       15 мин
        verdict channel, JSONL только для diagnostic
  ────  ────────────────────────────────────────────────  ───────
  7     templates/codex-system-prompt.md: skills section   10 мин
        с конкретными именами + tool usage caveats
  ────  ────────────────────────────────────────────────  ───────
  8     codex_runner: max_rate_limit_retries default 1     5 мин
        (native retries уже есть)
  ────  ────────────────────────────────────────────────  ───────
  9     stop_hook reason improvement Path A: summary +     15 мин
        severity counts из last_verdict_event
  ────  ────────────────────────────────────────────────  ───────
        ИТОГО этап 2                                      ~45 мин
```

После этапа 2 — финальный аудит → merge → push → PR3.
