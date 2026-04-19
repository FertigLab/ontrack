# ontrack

[![Tests](https://github.com/FertigLab/ontrack/actions/workflows/tests.yml/badge.svg)](https://github.com/FertigLab/ontrack/actions/workflows/tests.yml)

A command-line tool that scans directory trees and reports file statistics (file count, total size) for locations defined in a YAML configuration file. Supports Unix group-based filtering.

## Requirements

- Python 3.10+
- [PyYAML](https://pyyaml.org/) >= 6.0
- [tqdm](https://tqdm.github.io/) >= 4.0

## Installation

```bash
pip install git+https://github.com/FertigLab/ontrack.git
```

## Configuration

Create a YAML config file (see [`ontrack.config`](ontrack.config) for a template):

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

# Named tracks used to categorise project directories (optional)
track:
  rna-seq:
    description: RNA sequencing analysis projects
  cnv-pipeline:
    description: Copy number variation pipeline projects
```

| Key | Description |
|---|---|
| `paths` | List of top-level paths to scan (required) |
| `groups` | List of Unix group names; enables group mode (optional, overridden by `--groups`) |
| `ignore` | Glob patterns matched against base names to exclude from all scans (optional) |
| `track` | Map of valid track names; each key may have optional subfields such as `description` (optional) |

## Usage

```bash
ontrack --config ontrack.config [OPTIONS]
```

| Option | Description |
|---|---|
| `--config FILE` | Path to the YAML config file |
| `--groups GROUP [GROUP ...]` | One or more Unix group names; overrides the `groups` key in the config file |
| `--light` | Skip file-count and size scanning; only report directory and owner |
| `--progress` | Show progress bars while scanning |
| `--output FILE` | Write the report as YAML to `FILE` instead of printing to stdout |
| `--find VALUE` | Return only entries where any output field exactly matches `VALUE` |

### Configuration File Resolution

If `--config` is not provided, ontrack uses the `ONTRACK_CONFIG` environment variable when set; otherwise it falls back to `ontrack.config`.

## Operating Modes

**Default mode** — reports stats directly for each configured directory:

```bash
ontrack --config ontrack.config
```

**Group mode** — for each configured directory, finds and reports subdirectories owned by members of the specified Unix groups. Descends until a directory containing at least one file is found:

```bash
ontrack --config ontrack.config --groups researchers
ontrack --config ontrack.config --find alice
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

## Metadata Tracking (`ontrack.yml`)

ontrack supports an optional metadata store file named **`ontrack.yml`**. When this file is found in a directory during descent, it has two effects:

1. **Signals reporting directories** — all non-ignored subdirectories at that level become reporting directories. Descent stops; the `ontrack.yml` file itself is never counted as a visible file.
2. **Declares per-directory metadata** — each subdirectory can have an entry in the store. A directory is considered *on track* when it has an entry containing all three required fields.

### ontrack.yml format

```yaml
# ontrack.yml – place this file inside a directory that contains project subdirectories
project1:
  track: "rna-seq"
  owner: "alice"
  created: "2024-01-15"

project2:
  track: "cnv-pipeline"
  owner: "bob"
  created: "2024-03-20"
  # Any extra fields (pi, grant, status, …) are allowed and will be printed
  grant: "NIH-R01-CA123456"
```

### Required metadata fields

| Field | Type | Purpose |
|---|---|---|
| `track` | string | Track name matching a key in `ontrack.config`'s `track` section |

A directory is **on track** when the `track` field is present with a non-empty value and — when the `track` section is present in `ontrack.config` — the value matches a recognised track name. All other fields (`owner`, `created`, etc.) are optional and will be included in both stdout and YAML output when present.

### On-track status in output

**stdout:**
```
Directory : /data/projects/alice/project1
Username  : alice
Group     : researchers
Files     : 1042
Total size: 3.57 GB
On track  : Yes
Track     : rna-seq
Owner     : alice
Created   : 2024-01-15
```

**YAML (`--output`):**
```yaml
- directory: /data/projects/alice/project1
  username: alice
  on_track: true
  metadata:
    track: rna-seq
    owner: alice
    created: '2024-01-15'
  file_count: 1042
  total_size: 3833540608
  total_size_human: 3.57 GB
```

When no `ontrack.yml` is found in the parent directory, `on_track` is `false` and no `metadata` key is emitted.
