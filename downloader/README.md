# ORCA-DL OneDrive downloader

`download_predictions.py` downloads data from the public
[ORCA-DL release share](https://1drv.ms/f/c/49d761d10f0b201d/Emi9scIyaWBCrNTgRo6t12oBLnF2qGDRGj0M7-g0ekRM1A)
on OneDrive, one file at a time, without going through the web interface
(whose "download folder" button produces a broken zip for folders this large).

## Requirements

Python ≥ 3.10 and `requests`:

```bash
pip install requests
```

## Usage

By default the script downloads `predictions_init_with_godas` (~147 GB) into
`<repo root>/predictions_init_with_godas`, wherever you run it from:

```bash
python claude_downloader/download_predictions.py
```

Options:

| Option | Description |
| --- | --- |
| `--leads SPEC` | Only download some lead times (months), e.g. `--leads 1-6,12,24`. Default: all 24. |
| `--remote PATH` | Folder inside `ORCA-DL-release` to download, e.g. `stat`, `model_weights` or `predictions_init_with_godas/tos` (a single variable). |
| `--out DIR` | Local destination. Default: `<repo root>/<remote>`. |
| `--dry-run` | List what would be downloaded (and its total size) without downloading. |

Examples:

```bash
# See what's left to download
python claude_downloader/download_predictions.py --dry-run

# First 6 months of lead time for all variables (~36 GB)
python claude_downloader/download_predictions.py --leads 1-6

# Only sea surface temperature, all leads (~2.2 GB)
python claude_downloader/download_predictions.py --remote predictions_init_with_godas/tos
```

For long downloads, run it in `tmux`/`screen` or with
`nohup python claude_downloader/download_predictions.py > download.log 2>&1 &`.

## Remote contents

`ORCA-DL-release` contains:

| Folder | Size |
| --- | --- |
| `predictions_init_with_godas` | 147 GB |
| `train_data` | 314 GB |
| `valid_test_data` | 44 GB |
| `model_weights` | 22 GB |
| `stat` | 0.3 GB |

`predictions_init_with_godas` has one subfolder per variable: `so`, `thetao`,
`uo`, `vo` (~1.5 GB per lead) and `tos`, `zos` (~93 MB per lead), each holding
`lead_01.nc` … `lead_24.nc`.

## Relaunching and interruptions

The script can be stopped and relaunched safely:

- Files already present locally with the same size as the remote are skipped.
- A file being downloaded is written as `<name>.part` and renamed only once
  complete; a relaunch resumes it from where it stopped.
- Network errors, throttling and expired tokens are retried automatically.

## How it works

OneDrive no longer allows fully anonymous API access to shared folders. The
script obtains a temporary guest ("Badger") token the same way the OneDrive
web client does, then lists and downloads files through the OneDrive API.
Files are fetched in 100 MB byte ranges, because OneDrive answers
`HTTP 500` to requests covering the whole of a large (~1.5 GB) file.
This relies on undocumented Microsoft behaviour and may break if they change it.
