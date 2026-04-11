# ontrack

[![Tests](https://github.com/FertigLab/ontrack/actions/workflows/tests.yml/badge.svg)](https://github.com/FertigLab/ontrack/actions/workflows/tests.yml)

A command-line tool that scans directory trees and reports file statistics (file count, total size) for locations defined in a YAML configuration file. Supports Unix group-based filtering.

## Requirements

- Python 3.12+
- [PyYAML](https://pyyaml.org/) >= 6.0
- [tqdm](https://tqdm.github.io/) >= 4.0

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Create a YAML config file (see [`config.yaml`](config.yaml) for a template):

```yaml
# Top-level directories to scan
paths:
  - /path/to/data

# Unix groups whose members' subdirectories should be reported (optional)
groups:
  - your_group_name

# Shell-style glob patterns for files/directories to exclude (optional)
ignore:
  - '.*'
  - '*.tmp'
```

| Key | Description |
|---|---|
| `paths` | List of top-level paths to scan (required) |
| `groups` | List of Unix group names; enables group mode (optional, overridden by `--groups`) |
| `ignore` | Glob patterns matched against base names to exclude from all scans |

## Usage

```bash
python3 ontrack.py --config config.yaml [OPTIONS]
```

| Option | Description |
|---|---|
| `--config FILE` | Path to the YAML config file (default: `config.yaml`) |
| `--groups GROUP [GROUP ...]` | One or more Unix group names; overrides the `groups` key in the config file |
| `--light` | Skip file-count and size scanning; only report directory and owner |
| `--progress` | Show progress bars while scanning |
| `--output FILE` | Write the report as YAML to `FILE` instead of printing to stdout |

## Operating Modes

**Default mode** — reports stats directly for each configured directory:

```bash
python3 ontrack.py --config config.yaml
```

**Group mode** — for each configured directory, finds and reports subdirectories owned by members of the specified Unix groups. Descends until a directory containing at least one file is found:

```bash
python3 ontrack.py --config config.yaml --groups researchers
```

## Reporting Directory & Descent

In **group mode**, ontrack does not simply report statistics for the immediate subdirectory owned by a group member. Instead it descends into that subdirectory to find the *reporting directory* — the deepest directory that is actually meaningful to report.

**What is a reporting directory?**  
A directory is considered a *reporting directory* when it contains at least one *visible* file: a file whose base name is **not** matched by any pattern in the `ignore` list. File counts and sizes are then computed for that directory (recursively).

**How descent works:**  
Starting from an owned subdirectory, ontrack inspects the directory's contents:

1. If the directory contains at least one visible file, descent stops and this directory is the reporting directory.
2. If the directory contains only ignored files, only subdirectories (no visible files), or is completely empty, ontrack recurses into each non-ignored subdirectory and repeats the process.
3. An empty directory (no files and no subdirectories) is used as the reporting directory as-is.
4. If every subdirectory raises a permission error and cannot be scanned, the current directory is used as the fallback reporting directory.

**How the ignore list affects descent:**  
The `ignore` key accepts shell-style glob patterns matched against base names (not full paths). During descent:

- Any file whose base name matches an ignore pattern is treated as invisible (it does not satisfy the "visible file" condition that stops descent).
- Any subdirectory whose base name matches an ignore pattern is **skipped entirely** — ontrack will not descend into it, and it is never selected as a reporting directory.

This means an `ignore` list such as `['.*', '*.tmp']` will cause ontrack to look past hidden directories (e.g. `.git`) and treat directories that contain only dotfiles or `.tmp` files as if they were empty, continuing descent into their non-ignored siblings.

## Example Output

```
Directory : /data/projects/alice
Username  : alice
Group     : researchers
Files     : 1042
Total size: 3.57 GB

Directory : /data/projects/bob
Username  : bob
Group     : researchers
Files     : 204
Total size: 512.00 MB
```

Use `--output report.yaml` to save results as structured YAML instead.