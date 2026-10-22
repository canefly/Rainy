# Clickables Parser — Native PySide6 Migration Blueprint (Fortified Edition)

> **Target Platform:** Windows 11 Native (`PySide6` + `QFluentWidgets` + `python-mpv`)  
> **Source Project:** `clickables-parser` (Tauri v2 + React)  
> **New Workspace:** `clickables-tool-native` (Pure Python)  
> **Document Status:** Approved & Finalized with Cane (October 2026)

---

## 1. Visual Layout Architecture (Ground Truth from Production)

The native application precisely reproduces the visual hierarchy and design language of Clickables Tools:

```
┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
│ [🦊 CLICKABLES TOOLS | Made by canefly]            [🟢 CANEF] [A ROLL 80/82 98%] [EST TOTAL 21m 26s] [📌] [—] [□] [✕] │
├────┬──────────────────────────────────────────┬───────────────────────────────────────────────────────┤
│    │ SOURCE SCRIPT               [Zoom Slider]│ A-ROLL TIMELINE                                       │
│ 🏠 │ ──────────────────────────────────────── │ ───────────────────────────────────────────────────── │
│    │ 🟢 Play clip 20:48-20:50                 │ [✓] 1. [CLIP] [DRIVE.GOOGLE.COM GDrive Asset]         │
│ 📑 │    @editor play droning ambient under... │        00:20:48:00  [DUR 2s]  00:20:50:00        [↗]  │
│    │                                          │                                                       │
│ 📝 │ 🟢 Play clip 20:52-20:54                 │ [✓] 2. [CLIP] [DRIVE.GOOGLE.COM GDrive Asset]         │
│ 13 │    "911 What is the address of the... "  │        00:20:52:00  [DUR 2s]  00:20:54:00        [↗]  │
│    │                                          │                                                       │
│ 🔗 │ 🟢 Play gun shot audio 3:48-3:49 @ 20:52  │ [✓] 3. [CLIP] [🖼️ Shooting 45 acp 100 Yards]          │
│ 74 │    of cctv add text *Gun shot*           │        00:03:48:00  [DUR 1s]  00:03:49:00        [↗]  │
│ ── │                                          │                                                       │
│ ◫  │ 🟢 Play 911 over CCTV clip 0:00-0:01     │ [✓] 4. [CLIP] [🖼️ Shooting 45 acp 100 Yards]          │
│ 🗄️ │                                          │        00:20:52:00                                [⚠️] │
│ ⚙️ │ (Infinite scrollable script view)        │ (Scrollable interactive checklist timeline)           │
└────┴──────────────────────────────────────────┴───────────────────────────────────────────────────────┘
```

---

## 2. The 6 Architectural Shields (Proactive Failure Prevention)

To prevent breaking bugs, lag, and data corruption, these 6 architectural rules are baked into the foundation from Day 1:

1. **Zero-Work-on-Main-Thread (`QThreadPool` + `WorkerSignals`):**
   * The main GUI thread *only* paints pixels and handles input.
   * All file reading, DOCX parsing, hashing, and database queries run on background threads.
   * Live progress signals (`signals.progress.emit(percentage)`) feed real-time visual progress spinners, preventing any `(Not Responding)` freeze.
2. **Day-1 Nuitka Smoke Compilation:**
   * Packaging is verified immediately upon standing up the initial PySide6 window rather than waiting until the end.
   * Verifies MSVC compilation, Qt plugin bundling, and standalone execution upfront.
3. **Shadow Document & Non-Destructive Delta Staging:**
   * Inspired by Microsoft Word's shadow-copy pattern (`~$document.docx`).
   * The user's original `.docx` file is treated as immutable read-only source truth.
   * All inline timestamp fixes, notes, and checklist updates are saved as non-destructive **Delta Overrides** in SQLite with a local auto-backup file. Reverting to original is always 1 click away.
4. **Optimized Virtualized Checklist (Up to 400 Items):**
   * Real-world scripts top out around 300–400 timestamp items.
   * Uses lightweight card delegates with cached drawing so scrolling through 400 items stays locked at a silky 120 FPS.
5. **Self-Contained `mpv-2.dll` Pinning & Pre-Flight Check:**
   * Dedicated 64-bit `mpv-2.dll` is vendored directly into `assets/bin/`—never relying on system PATH.
   * Boot-time pre-flight check validates GPU drivers and DLL handles before loading the video widget, failing gracefully with a diagnostic modal if drivers are missing.
6. **SQLite Micro-Migrations (`PRAGMA user_version`):**
   * Native database connection automatically inspects schema version and runs forward `ALTER TABLE` migrations without wiping user progress or settings.

---

## 3. Color Theme Preservation (1:1 with `globals.css`)

All color palettes are mapped directly from the React CSS variables into `src/ui/theme.py`:

| Palette | Background (`bg`) | Surface (`surface`) | Border (`border`) | Accent (`accent`) | Text |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Femboy Pink** | `#1e1b1e` | `#2b262b` | `#4c3d4c` | `#fb7185` | `#ffffff` |
| **Adobe Gray** | `#181818` | `#232323` | `#3f3f3f` | `#e8e8ed` | `#ffffff` |
| **OLED Black** | `#000000` | `#0f0f0f` | `#262626` | `#38bdf8` | `#ffffff` |
| **Cloudy Blue** | `#0b0f1a` | `#161b2a` | `#2d3748` | `#38bdf8` | `#ffffff` |

---

## 4. Phased Implementation Roadmap

### 🟢 Phase 1: Pure Native Port & Day-1 Smoke Test
*Focus: Reproduce the exact UI and parser in native PySide6 with zero UI lag and full theme fidelity.*
1. **Bootstrap & Day-1 Smoke Test:** Initialize `clickables-tool-native`, setup virtual environment, launch minimal `MSFluentWindow`, and compile immediately with Nuitka to prove the standalone `.exe` pipeline.
2. **TitleBar & Stats:** Custom titlebar with fox logo, `CANEF` badge, `A ROLL 80/82 98%` counter, and `EST TOTAL` pill.
3. **Sidebar Dock:** Left navigation with badge counters for Notes (`13`) and Source Links (`74`).
4. **Theme Engine:** Complete 1:1 port of Femboy Pink, Adobe Gray, OLED Black, and Cloudy Blue.
5. **Worker-Threaded Parser:** Non-blocking `QRunnable` parser with typo tolerance (`00;09`, `00.09`, `00 09` ➔ `HH:MM:SS:FF`) and live import progress spinner.
6. **Dual Split-Pane Workspace:**
   * Left: Source Script reader with neon highlights and top-right font zoom slider.
   * Right: A-Roll Timeline checklist cards (up to 400 items) with checkboxes, `CLIP` pills, GDrive/YouTube badges, and blue `[DUR 2s]` badges.
7. **SQLite Persistence with Versioning:** Store document checklist states and recent files in `clickables.db` with `PRAGMA user_version`.
8. **Flyout Drawers:** Slide-out panels for Editor Notes and Source Links.

> **Checkpoint:** Cane launches `python src/main.py`, drops in a test `.docx`, tests themes, and verifies that the app is 100% stable, responsive, and visually identical to the production screenshot.

---

### 🟡 Phase 2: Native Broadcast Video Engine (Post-Phase 1)
*Focus: Integrate hardware-accelerated video playback directly into the workspace.*
1. **Pre-flight & Vendored `mpv-2.dll`:** Embedded GPU-accelerated video monitor (DirectX 11, zero RAM dump).
2. **Timeline Card Synchronization:** Clicking any A-Roll timeline card jumps the video directly to that timecode.
3. **NLE Keyboard Navigation:** `Space` (Play/Pause), `J-K-L` (Reverse/Stop/Fast-Forward), `Left/Right` (1-frame stepping).

---

### ⚪ Phase 3: Automated Footage Probing & Clip Binding (Deferred)
*Explicitly deferred. Clip downloading, file-to-block binding, and automated PTS-to-SMPTE conforming will be designed as a dedicated module once the video playback foundation is mature.*

---

### 🟣 Phase 4: Production Security & FastAPI Server Deployment
*Focus: Deploy the backend and compile the final installer.*
1. **Server API (`canefly-server`):** Deploy lightweight FastAPI + SQLite backend to `/home/canefly/apps/clickables-api/` on port **8001** via Cloudflare Tunnel (`click.canefly.xyz`).
2. **Licensing & Version Endpoints:** Ed25519 cryptographic signing, machine fingerprinting, and changelog/announcement feeds.
3. **No-OTA Update Policy:** In-app update notification badge with release notes and direct installer link.
4. **Final Nuitka & Inno Setup Build:** Generates standalone `ClickablesSetup.exe` installer for Windows.
