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
```

Back up that one folder and you have everything. It is writable without admin rights and survives reinstalls and updates.
Without `LOCALAPPDATA` (not Windows) it is `~/.knot`.

Not in there on purpose:
- **Your projects and extracted photos** are in `Documents\Rainy\` where you can see and back them up.
- **The device key** is in the OS keyring (Windows Credential Manager, service `com.canefly.knot`), never in a file.
- **Throw-away temp files** (thumbnails, cut previews) use the system temp folder.

## How engines (libmpv, FFmpeg, FFprobe) get here and are found

**Nothing is bundled in the installer.** The first time an installed Rainy is approved and unlocked, it checks
for the video player (libmpv) and the video tools (FFmpeg and FFprobe). Whatever is missing is downloaded once
(about 60 MB in total) into `app home\bin`, and a small notice in the corner shows the progress, with Cancel
while it runs and Try again if it fails (offline, blocked, file changed). It never blocks the app, and
Preferences > Engines can install them later.

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

## Developer switches

The licence, update, engine and `KNOT_HOME` switches are ignored in installed builds.

`KNOT_HOME` moves the whole app home (the tests point it at a temp folder). The rest: `KNOT_DB_PATH`, `KNOT_API_URL`,
`KNOT_LICENSE_KEYS`, `KNOT_SKIP_LICENSE`, `KNOT_NO_UPDATE_CHECK`, `KNOT_ALLOW_UNPINNED_ENGINES`, `KNOT_MCP_PIPE`,
`KNOT_PHOTOS_DIR`, `KNOT_CENSOR_MODEL_DIR`, `KNOT_NO_AUTO_DOWNLOAD`, `KNOT_CUT_PREVIEW_DIR`, `KNOT_VO_ENVELOPE_DIR`.
