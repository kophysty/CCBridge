# Development Setup

## Prerequisites

- Python 3.11+
- `uv` (рекомендуется) или `pip`
- Git
- Опционально для integration-тестов: Claude Code CLI и Codex CLI в PATH

## Установка для разработки

```bash
# Клонировать репо
cd D:\Dev\CCBridge

# Создать виртуальное окружение и установить зависимости
# Вариант с uv (рекомендуется — быстрее):
uv venv
uv pip install -e ".[dev]"

# Вариант с pip:
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # Linux/Mac
pip install -e ".[dev]"
```

## Команды

```bash
# Запустить все тесты
pytest

# Тесты с coverage
pytest --cov=ccbridge --cov-report=term-missing

# Линтер
ruff check .

# Auto-fix
ruff check . --fix
ruff format .

# Типы
mypy src/ccbridge

# Все проверки разом (pre-commit)
ruff check . && ruff format --check . && mypy src/ccbridge && pytest
```

## Структура тестов

```
tests/
├── unit/                # Чистые unit-тесты, без I/O
│   ├── test_events.py
│   ├── test_verdict.py
│   └── ...
├── integration/         # С временной файловой системой через tmp_path
│   ├── test_lockfile.py
│   ├── test_audit_log.py
│   └── test_state_recovery.py
└── e2e/                 # End-to-end (требуют claude и codex в PATH)
    └── test_full_cycle.py  # помечены @pytest.mark.e2e, skip по умолчанию
```

## Запуск только unit-тестов (быстро)

```bash
pytest tests/unit
```

## Запуск integration (требует tmp_path, fast)

```bash
pytest tests/integration
```

## Запуск e2e (медленно, требует CLI tools)

```bash
pytest tests/e2e -m e2e
```

## Стиль кода

- 100-символьная ширина строки
- Type hints везде (`disallow_untyped_defs = true`)
- Docstrings только когда WHY неочевидно
- Нет emoji в коде
- Нет лишних комментариев — самодокументируемое именование

См. `tool.ruff` и `tool.mypy` в `pyproject.toml`.
