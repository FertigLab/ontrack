# Copilot Instructions for ontrack

## Project Overview

**ontrack** is a Python 3.12+ command-line utility that scans directory trees and reports file statistics (file count, total size) for locations defined in a YAML configuration file. It supports Unix group-based filtering and two operating modes:

- **Group mode** (`--group` flag or `group:` in config): For each configured directory, finds subdirectories owned by users belonging to the specified Unix group and reports stats, descending until a directory containing at least one file is found.
- **Default mode**: Reports stats directly for each configured directory listed in the config.

## Repository Structure

```
ontrack.py          # Main application (single-file CLI tool, ~357 lines)
tests/
  test_ontrack.py   # pytest test suite (~793 lines, 51 tests)
requirements.txt    # Runtime dependencies: PyYAML>=6.0, tqdm>=4.0
config.yaml         # Example YAML configuration file
README.md           # Project documentation
```

## How to Build, Run, and Test

### Install dependencies
```bash
pip install -r requirements.txt
pip install pytest  # for running tests
```

### Run the application
```bash
python3 ontrack.py --config config.yaml
python3 ontrack.py --config config.yaml --group <unix-group>
python3 ontrack.py --config config.yaml --light
python3 ontrack.py --config config.yaml --output report.yaml
python3 ontrack.py --config config.yaml --progress
```

### Run tests
```bash
python3 -m pytest tests/ -v
```

All 51 tests must pass. There is no build step — this is a pure Python project.

## Code Style and Conventions

- **Language**: Python 3.12+ with full type annotations on every function and return type.
- **Style**: Follow the existing code style — functional (no classes), snake_case naming, explicit imports (no `import *`).
- **Docstrings**: Google-style docstrings. Every public and private function must have a docstring with a summary line and, where needed, a description of parameters, return values, and exceptions.
- **Private functions**: Prefix with a single underscore (e.g., `_uid_to_username`, `_find_reporting_directories`).
- **Error handling**: Use narrow `except` clauses (e.g., `except (OSError, KeyError):`). Silently skip inaccessible filesystem entries rather than raising errors to the user.
- **Caching**: Use `@functools.lru_cache(maxsize=None)` for expensive repeated lookups (e.g., UID → username).
- **Type hints**: Use the modern union syntax (`str | None`) not `Optional[str]`.
- **Logging**: Use the module-level `logger = logging.getLogger(__name__)` instance; do not call `print()` for informational logging (use `print(..., file=sys.stderr)` only for user-facing warnings).
- **pathlib**: Prefer `pathlib.Path` for filesystem operations rather than `os.path` string manipulation.

## Testing Conventions

- Tests live in `tests/test_ontrack.py` and use **pytest**.
- Naming: `test_<function_name>_<scenario>` (e.g., `test_get_directory_stats_empty_dir`).
- Use `tempfile.TemporaryDirectory()` for any test that needs real filesystem fixtures — no persistent test data files.
- Use `pytest`'s `capsys` and `caplog` fixtures for capturing stdout/stderr and log output.
- Mock system calls (`pwd`, `grp`, `os.scandir`) via `unittest.mock.patch` to keep tests isolated from the host system.
- Every new function should have tests covering the happy path, edge cases, and error/exception paths.

## Key Design Principles

- The application is intentionally a **single-file tool** (`ontrack.py`). Do not refactor it into a package unless the issue explicitly requires it.
- Keep dependencies minimal — only add a new dependency if it is strictly necessary and update `requirements.txt` accordingly.
- Filesystem access is **read-only**; the tool never modifies the directories it scans.
- The YAML configuration is loaded with `yaml.safe_load()` — never use `yaml.load()` without a Loader.
