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

## How engines are found

1. `app home\bin` first.
2. In a source checkout only: the repo's `bin\` (checked before the app home) and the system `PATH`.
3. For now an installed build also looks in its own install folder (`bin\` next to the exe), because the installer still bundles libmpv and ffprobe. That goes away once every engine is pinned on the server and downloaded into `app home\bin`.

An installed build never searches `PATH`, so a stray copy elsewhere on the machine can't be loaded.
Downloads are written to `app home\bin` (or the repo's `bin\` in a source checkout).

## Developer switches

The licence, update, engine and `KNOT_HOME` switches are ignored in installed builds.

`KNOT_HOME` moves the whole app home (the tests point it at a temp folder). The rest: `KNOT_DB_PATH`, `KNOT_API_URL`,
`KNOT_LICENSE_KEYS`, `KNOT_SKIP_LICENSE`, `KNOT_NO_UPDATE_CHECK`, `KNOT_ALLOW_UNPINNED_ENGINES`, `KNOT_MCP_PIPE`,
`KNOT_PHOTOS_DIR`, `KNOT_CENSOR_MODEL_DIR`, `KNOT_NO_AUTO_DOWNLOAD`, `KNOT_CUT_PREVIEW_DIR`, `KNOT_VO_ENVELOPE_DIR`.
