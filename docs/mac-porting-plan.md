# macOS port plan

Status: **planned, nothing built yet.** Written from a read of the code, not from running it on a Mac. Items marked *(unverified)* need a real Mac to confirm.

The stack (Python, PySide6, faster-whisper, python-mpv, PyAV) all runs on macOS, so this is platform-branching and a new UI shell, not a rewrite. Keep Windows working at every step: branch on `sys.platform`, never delete the Windows path.

## Phase 1: make it launch (core, no UI changes)

| # | File | Problem | Mac fix |
|---|---|---|---|
| 1 | `src/core/auth.py` | `import winreg` at top (crashes on import) and `ctypes.windll` for RAM | Machine ID from `IOPlatformUUID` (via `ioreg`), RAM via `sysctl hw.memsize`. Keep fingerprint format stable per platform |
| 2 | `src/core/engine_manager.py` | Downloads `libmpv-2.dll` / `ffmpeg.exe` into `%LOCALAPPDATA%` | Use `~/Library/Application Support/com.canefly.knot/bin`. libmpv is `libmpv.dylib`, easiest is Homebrew `mpv`. Decide: bundle or detect |
| 3 | `src/core/ffmpeg_encoder.py` | Recycle-bin delete uses `SHFileOperationW` | `send2trash` or NSFileManager |
| 4 | `src/core/logger.py`, `cookie_manager.py`, `media_probe.py` | `%APPDATA%` / `%LOCALAPPDATA%` paths, Windows browser profile locations | `~/Library/Logs`, `~/Library/Application Support`, macOS browser cookie paths |
| 5 | `src/core/mcp_server.py` | Windows named pipe `\\.\pipe\...` | Unix domain socket under the app support dir |
| 6 | `src/core/vo_engine.py` | CUDA DLL loading is Windows-specific | Skip on Mac. faster-whisper runs CPU there (no CUDA). *(unverified: speed on Apple Silicon, consider whisper.cpp/MLX later)* |
| 7 | `CREATE_NO_WINDOW` flags | Already guarded by `os.name == "nt"` | No change |

Already half done: `ffmpeg_encoder.py` / `media_probe.py` pick `ffmpeg` without `.exe` off Windows, and `mcp_installer.py` has a `darwin` branch.

## Phase 2: native window chrome

Today the app is built on `qframelesswindow` with a custom caption bar (`src/ui/components/titlebar.py`, pin button is a 46x32 Windows-style caption button).

- **Replace the top nav bar with the native macOS title bar.** Use the traffic lights (close/minimize/zoom) on the left and drop the custom min/max/close buttons. `qframelesswindow` ships a Mac variant (`MacFramelessWindow`) *(unverified: check the installed version)*.
- **Move the pin button and status dot** into a toolbar region, since they currently sit in the Windows-style caption area.
- Windows: keep the existing titlebar as is.
- Affected windows: main window, `about_dialog.py`, `data_vault.py`, `preferences_dialog.py`, `drawers.py`.

## Phase 3: glass (replace DWM with macOS vibrancy)

The Windows glass lives in `ThemeManager.enable_window_backdrop` (`src/ui/theme.py`): DWM Acrylic/Mica via `DwmSetWindowAttribute`, gated to Windows 11 build 22000+. On macOS this is a no-op today, so the glass themes would render as flat tinted surfaces.

Plan:
1. Add a macOS branch next to the Windows one, and keep `paint_window_backdrop` (tint, orbs, noise veil) as the cross-platform painter on top.
2. Get the native `NSView` from `window.winId()` via PyObjC, insert an `NSVisualEffectView` behind the Qt content, and set `WA_TranslucentBackground` so Qt paints over it. This is the classic macOS vibrancy and works on every supported macOS.
3. **Liquid Glass (macOS 26+):** try `NSGlassEffectView` through PyObjC on macOS 26, and fall back to `NSVisualEffectView` on older systems. *(unverified: needs macOS 26 SDK-built PyObjC, and Qt may not composite cleanly over it. Prototype before committing.)*
4. Map the existing `glass_transparency` slider (0-100%) onto the base tint alpha. Vibrancy blur radius is system-controlled, so the slider changes tint rather than blur amount.
5. "Pro Solid" themes (Adobe Gray, OLED Black) stay fully opaque: no effect view.
6. Rename the preference copy ("Windows 11 DWM Acrylic" in `preferences_dialog.py`) to be platform-neutral.

## Phase 4: packaging and distribution

- Nuitka + Inno Setup (`scripts/build_nuitka.*`, `installer/setup.iss`) is Windows-only. Nuitka can't cross-compile, so builds need a Mac.
- Build a `.app` bundle (Nuitka `--macos-create-app-bundle`, or PyInstaller as a fallback).
- Sign and notarize (needs an Apple Developer account), then ship a `.dmg`.
- Bundle `ffmpeg`, `ffprobe` and libmpv inside the bundle, or detect Homebrew.
- Licensing endpoints on the server (`rainy-api`) need to accept a macOS fingerprint.

## Phase 5: verify on a Mac

- Import media, play preview (libmpv inside Qt embedding on macOS is the riskiest part, *unverified*).
- Whisper transcription, waveform snap, VO review.
- XML export, then import into Premiere, check that file paths survive.
- MCP bridge connect from Claude Desktop.
- Test on both Apple Silicon and, if relevant, Intel.

## Open questions

- Minimum macOS version to support (decides whether Liquid Glass needs a fallback at all).
- Ship libmpv/ffmpeg bundled, or depend on Homebrew?
- Apple Developer account for signing and notarization?
- Is Mac a day-one target or a "when I get hardware" thing?

## Suggested order

Phase 1, then 2, then 3 on a Mac. Phase 1 can be done and tested from Windows (it only needs `sys.platform` branches plus unit tests), so it's safe to start before any Mac is available.
