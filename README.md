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

# Unix group whose members' subdirectories should be reported (optional)
group: your_group_name

# Shell-style glob patterns for files/directories to exclude (optional)
ignore:
  - '.*'
  - '*.tmp'
```

| Key | Description |
|---|---|
| `paths` | List of top-level paths to scan (required) |
| `group` | Unix group name; enables group mode (optional, overridden by `--group`) |
| `ignore` | Glob patterns matched against base names to exclude from all scans |

## Usage

```bash
python3 ontrack.py --config config.yaml [OPTIONS]
```

| Option | Description |
|---|---|
| `--config FILE` | Path to the YAML config file (default: `config.yaml`) |
| `--group GROUP` | Unix group name; overrides the `group` key in the config file |
| `--light` | Skip file-count and size scanning; only report directory and owner |
| `--progress` | Show progress bars while scanning |
| `--output FILE` | Write the report as YAML to `FILE` instead of printing to stdout |

## Operating Modes

**Default mode** — reports stats directly for each configured directory:

```bash
python3 ontrack.py --config config.yaml
```

**Group mode** — for each configured directory, finds and reports subdirectories owned by members of the specified Unix group. Descends until a directory containing at least one file is found:

```bash
python3 ontrack.py --config config.yaml --group researchers
```

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