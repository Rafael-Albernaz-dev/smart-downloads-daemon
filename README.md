# smart-downloads-daemon 📂

[![Linux](https://img.shields.io/badge/OS-Linux-FCC624?logo=linux&logoColor=black)](#)
[![Python 3](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)](#)
[![CI](https://github.com/Rafael-Albernaz-dev/smart-downloads-daemon/actions/workflows/ci.yml/badge.svg)](https://github.com/Rafael-Albernaz-dev/smart-downloads-daemon/actions/workflows/ci.yml)
[![Systemd](https://img.shields.io/badge/Service-systemd%20--user-lightgrey)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A high-performance, zero-dependency Linux background daemon that automatically organizes your `~/Downloads` directory into categorized `~/Documents` folders using direct kernel `inotify` syscalls and an intelligent **grace-period cooldown**.

---

## The Problem: Why Traditional File Organizers Fail

Most automated download organizers suffer from critical usability and architectural flaws:

1. **The "Instant-Move" Annoyance**: You download a PDF or image to immediately drag-and-drop it into an email, browser form, or Slack. A naive script moves it 0.5 seconds later, breaking your upload and making you go hunting through folders.
2. **CPU & Battery Drain (Busy-Waiting)**: Many scripts use infinite `while True: sleep(5)` loops that continuously poll directory trees, spinning CPU cycles and keeping the CPU out of low-power sleep states.
3. **Heavy Dependency Bloat**: Popular tools rely on heavy third-party file watcher packages (`watchdog`) or background servers that consume 50MB–100MB of RAM for a task that Linux kernels natively support.

---

## The Solution: Native inotify with Grace-Period Cooldown

`smart-downloads-daemon` resolves all three problems:

* **Direct `libc inotify` Syscalls via `ctypes`**: Communicates directly with the Linux kernel via `inotify_init1(0)` and `inotify_add_watch` with binary struct decoding (`struct inotify_event`). Zero third-party dependencies.
* **0% Idle CPU (`select.poll`)**: The event loop sleeps in the kernel until an actual I/O event occurs or the next file cooldown expires. Zero busy-waiting.
* **Smart Grace Period (5-Minute Cooldown)**: Files remain immediately available in `~/Downloads` for quick access. Only after 5 minutes of inactivity are they silently moved to their permanent home in `~/Documents`.
* **In-Flight Download Guards**: Automatically ignores incomplete browser downloads (`.crdownload`, `.part`, `.tmp`, `.download`) until writing has completely finished (`IN_CLOSE_WRITE` / `IN_MOVED_TO`).
* **Timestamped Collision Protection**: If `report.pdf` already exists in `~/Documents/PDF`, the new file is automatically renamed to `report_YYYYMMDD_HHMMSS.pdf` without overwriting data.
* **Native `systemd --user` Integration**: Runs seamlessly as a background user service on Linux login.

---

## Architecture

```mermaid
flowchart TD
    Kernel[Linux Kernel inotify\nIN_CLOSE_WRITE | IN_MOVED_TO] -->|Event Trigger| Poller[select.poll Event Multiplexer]

    subgraph DaemonLoop [smart-downloads-daemon Event Loop]
        Poller -->|Raw struct inotify_event| Parser[Binary Struct Parser\nstruct.unpack_from]
        Parser --> Guard{Guard Rails\nCheck In-Flight / Hidden?}
        Guard -->|Ignored: .crdownload / .tmp / .part| Drop[Ignore Event]
        Guard -->|Valid File| Scheduler[Grace-Period Scheduler\nDefault: 300s cooldown]

        Scheduler --> DynamicWait[Dynamic Poll Timeout\nSleep until next file expires or 5s]
        DynamicWait --> Expired{Cooldown\nExpired?}
        Expired -->|No| DynamicWait
        Expired -->|Yes| Sorter[Categorization Engine]
    end

    Sorter --> Collision{File exists in\nDestination?}
    Collision -->|Yes| Rename[Append Timestamp\n_YYYYMMDD_HHMMSS]
    Collision -->|No| Move[shutil.move to Category Folder]
    Rename --> Move
    Move --> Dest[~/Documents/Category/]
```

---

## Default Category Mapping

| Category | File Extensions |
| :--- | :--- |
| **PDF** | `pdf` |
| **Textos** | `txt`, `md`, `doc`, `docx`, `odt`, `rtf`, `log` |
| **Planilhas** | `xls`, `xlsx`, `csv`, `ods`, `tsv` |
| **Imagens** | `png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`, `bmp`, `ico`, `tiff` |
| **Videos** | `mp4`, `mkv`, `avi`, `mov`, `webm`, `flv`, `wmv`, `m4v` |
| **Audios** | `mp3`, `wav`, `ogg`, `flac`, `m4a`, `aac`, `wma` |
| **Compactados** | `zip`, `tar`, `gz`, `bz2`, `7z`, `rar`, `xz`, `iso` |
| **Outros** | Any uncategorized extension |

---

## Repository Structure

```text
smart-downloads-daemon/
├── .github/workflows/ci.yml       # Automated GitHub Actions test suite
├── pyproject.toml                 # Standard PEP 621 packaging & CLI entrypoints
├── install.sh                     # Installer with systemd --user service setup
├── systemd/
│   └── smart-downloads-daemon.service # Systemd user service unit
├── src/smart_downloads_daemon/
│   ├── __init__.py                # Package version
│   ├── __main__.py                # python -m smart_downloads_daemon support
│   ├── cli.py                     # Command-line interface & argument parser
│   ├── config.py                  # JSON config loader & default category definitions
│   ├── inotify.py                 # POSIX libc inotify ctypes wrapper & struct unpacking
│   ├── sorter.py                  # Category classification, guards & collision renaming
│   └── watcher.py                 # select.poll dynamic event loop & grace scheduling
└── tests/
    ├── test_config.py             # Config loading & defaults tests
    ├── test_inotify.py            # Struct size & initialization tests
    ├── test_sorter.py             # Classification, ignore rules & collision tests
    └── test_watcher.py            # File scheduling & queue expiration tests
```

---

## Installation & Setup

### 1. Quick Install

Clone the repository and run the installer:

```bash
git clone https://github.com/Rafael-Albernaz-dev/smart-downloads-daemon.git
cd smart-downloads-daemon
./install.sh
```

### 2. Enable as a Systemd Service (Runs on boot)

```bash
# Enable and start the background service immediately:
systemctl --user enable --now smart-downloads-daemon

# Check service status:
systemctl --user status smart-downloads-daemon

# View live daemon logs:
journalctl --user -u smart-downloads-daemon -f
```

---

## CLI Usage

The executable provides commands for daemon execution, configuration, and batch storage migration:

```bash
# View active configuration, directories, and storage free space:
smart-downloads-daemon config --show

# Change destination directory on the fly (saves config and reloads daemon):
smart-downloads-daemon config --set-destination /media/toru/96A10007A0FFEB9D

# Preview batch migration of existing organized folders (dry-run):
smart-downloads-daemon migrate --to /media/toru/96A10007A0FFEB9D

# Execute batch migration and update config automatically:
smart-downloads-daemon migrate --to /media/toru/96A10007A0FFEB9D --apply

# Run daemon in foreground:
smart-downloads-daemon

# Perform a single scan to organize eligible files, then exit:
smart-downloads-daemon --scan-once

# Override grace period cooldown for a session (e.g. 120 seconds):
smart-downloads-daemon --grace-period 120
```

---

## Batch Migration & Storage Management

When moving organized categories to a secondary drive (or freeing up space on your primary disk):

1. **Safety First**: The migrator only moves category directories managed by the daemon (`PDF`, `Textos`, `Planilhas`, `Imagens`, `Videos`, `Audios`, `Compactados`, `Outros`). Any personal repos or unmanaged files in the root folder remain strictly untouched.
2. **Atomic Verification**: Files are copied and verified for size before unlinking source files.
3. **Collision Immune**: If a file already exists on the target disk, it is preserved and the incoming file receives an incremental timestamp counter (`_YYYYMMDD_HHMMSS_N`).
4. **Mount Guard**: If a secondary/external drive is unmounted or disconnected, the daemon automatically holds downloads safely in `~/Downloads` until the mount returns, preventing root partition contamination.

---

## Custom Configuration

Settings are saved in `~/.config/smart-downloads-daemon/config.json`:

```json
{
  "downloads_dir": "/home/toru/Downloads",
  "destination_dir": "/media/toru/96A10007A0FFEB9D",
  "grace_period_seconds": 300,
  "categories": {
    "PDF": ["pdf"],
    "Textos": ["txt", "md", "doc", "docx", "odt", "rtf", "log"],
    "Planilhas": ["xls", "xlsx", "csv", "ods", "tsv"],
    "Imagens": ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "tiff"],
    "Videos": ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v"],
    "Audios": ["mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"],
    "Compactados": ["zip", "tar", "gz", "bz2", "7z", "rar", "xz", "iso"]
  },
  "ignore_extensions": [".aria2", ".crdownload", ".download", ".opdownload", ".part", ".tmp"]
}
```

---

## Running Unit Tests

```bash
# Run the test suite with pytest:
pytest -v
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
