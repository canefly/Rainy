# Where the app keeps its files

Everything the app downloads or creates lives in **one folder**, decided in one place (`src/core/app_paths.py`):

```
%LOCALAPPDATA%\com.canefly.knot\
    knot.db            your settings, project index and checklist state
    knot.db.bak        latest quick-recovery copy
    backups\           one snapshot per day, newest 7 kept
    bin\               engines: libmpv, ffmpeg, ffprobe
    models\            AI models: photo_censor, whisper
    cache\             waveforms and speech envelopes (safe to delete, rebuilds)
    logs\              knot.log (rotated)
    updates\           downloaded installers (old ones are cleaned up)
    ui_config.json     the UI library's theme settings (see below)
```

Back up that one folder and you have everything. It is writable without admin rights and survives reinstalls and updates.
Without `LOCALAPPDATA` (not Windows) it is `~/.knot`.

Not in there on purpose:
- **Your projects and extracted photos** are in `Documents\Rainy\` where you can see and back them up.
- **The device key** is in the OS keyring (Windows Credential Manager, service `com.canefly.knot`), never in a file.
- **Throw-away temp files** (thumbnails, cut previews) use the system temp folder.

## How engines (libmpv, FFmpeg, FFprobe) get here and are found

**Nothing is bundled in the installer.** The installer only shows two pages before installing: "What Rainy needs"
(what the helper programs are for and what gets downloaded) and "Approval and your information" (what Rainy sends so
Cane can approve you, who sees it, how it is protected, ownership), which the person must agree to.

The downloads start only once the install is **approved and unlocked**, never before. Then one calm window,
"Getting Rainy ready", explains what and why, lists each step (video player libmpv, video tools FFmpeg/FFprobe,
and the optional ~155 MB photo-censor model) and shows progress. It never blocks the app, "Hide" or the X just hides it,
and it closes itself a few seconds after everything is ready. While it is busy the other first-start notices
(announcements, the "help improve Rainy?" question) are held and come back afterwards, so a first start is not a wall
of pop-ups. If a required download fails the window comes back with the reason and **Try again**; the optional
photo-censor step may be skipped or fail without failing the setup (it can be fetched later in Settings > AI Models).
The photo-censor model used to start downloading silently 4 seconds after launch, even before approval; it now only
runs from this window. Preferences > Engines can still install things later.

What makes the download safe: the server keeps a **signed list** of the exact files to fetch (address, SHA-256
fingerprint, size). The app downloads only what that list names, discards anything whose fingerprint or size
differs, and only then extracts the programs it needs. Currently pinned (platform `win-x64`):

| Engine | Build | Source file | Size |
|---|---|---|---|
| libmpv | `20261107` | shinchiro/mpv-winbuild-cmake release `20261107`, `mpv-dev-x86_64-20261107-git-9028187538.7z` | 30.3 MB |
| FFmpeg + FFprobe | `7.1.1` | GyanD/codexffmpeg release `7.1.1`, `ffmpeg-7.1.1-essentials_build.7z` | 26.8 MB |

These are the exact builds the app was developed against (the libmpv DLL and ffprobe are byte-identical to the
ones in a developer checkout), with the x264, NVENC, QSV and AMF encoders.

Extraction (`src/core/archive_extract.py`): both archives are 7z files that use a filter Python's py7zr cannot
read, so Windows' own `tar.exe` (bsdtar) does it. Only the needed files are pulled out and the rest of the
archive is never unpacked, so no headers or docs end up in `bin\`. A different `tar` on PATH (Git for
Windows ships GNU tar, which cannot read 7z) is never used. A file only counts if it has real content, and FFmpeg
is only accepted if both `ffmpeg.exe` and `ffprobe.exe` arrive.

Lookup order: `app home\bin`; in a source checkout only, the repo's `bin\` first and the system `PATH` last.
An installed build looks nowhere else, so a stray copy on the machine can't be loaded.

**To change a pinned build** (new version, or an upstream file disappears), on the server:
`python -m app.cli add-engine <libmpv|ffmpeg> win-x64 <build label> --url <https address> --format 7z --fetch`
(the server downloads it once and records the fingerprint), then `withdraw-engine <id>` for the old one. Apps pick
up the new list the next time they need an engine. Pinning to a permanent release asset matters: if upstream
deletes the file, new installs can't set up until a replacement is pinned.

## Installed for all users (Program Files)

An all-users install puts the program in `C:\Program Files\Rainy`, which a normal Windows user cannot write to. Everything the app
writes therefore goes to the app folder above, never next to the program. (Found by installing it: the UI library
wrote `config\config.json` into the folder it started in, the save failed, and the app quit with no message. Its settings
file is now `ui_config.json` in the app folder, a failed save is logged and skipped, and any error while starting is
written to `logs\knot.log` and shown in a message box.)

## Certificates (HTTPS)

Python only trusts the root certificates already in the Windows store, and Windows fetches a missing root only when a
Windows program (a browser) asks. On a fresh Windows (a new PC, Windows Sandbox) the app said "can't reach the server"
and could not download its engines or AI models while Edge worked. So the app trusts the Windows roots **and** a bundled
public list (`certifi`, in `src/core/net_tls.py`), for the license server, signed downloads, the AI model download and
the live connection. Certificates are still verified (the bundle only adds known public authorities). If a certificate
cannot be verified the message says so (work or school network, antivirus that scans web traffic) instead of "check
your connection", and the reason is written to the log.

## Developer switches

The licence, update, engine and `KNOT_HOME` switches are ignored in installed builds.

`KNOT_HOME` moves the whole app home (the tests point it at a temp folder). The rest: `KNOT_DB_PATH`, `KNOT_API_URL`,
`KNOT_LICENSE_KEYS`, `KNOT_SKIP_LICENSE`, `KNOT_NO_UPDATE_CHECK`, `KNOT_ALLOW_UNPINNED_ENGINES`, `KNOT_MCP_PIPE`,
`KNOT_PHOTOS_DIR`, `KNOT_CENSOR_MODEL_DIR`, `KNOT_NO_AUTO_DOWNLOAD`, `KNOT_CUT_PREVIEW_DIR`, `KNOT_VO_ENVELOPE_DIR`.
