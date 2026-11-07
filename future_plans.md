# Rainy Tool Native — Future Plans

## Rich Link Preview Embeds & Unfurl Architecture

> **Status:** Planned / Deferred for Future Implementation  
> **Target Workspace:** `knot-tool-native` (`PySide6` + `QFluentWidgets`)

---

### 1. Background Link Unfurl Engine (`QThreadPool`) — ✅ Implemented (`src/core/unfurl_engine.py`)
- **Pure Python Metadata Fetcher:** Asynchronous background worker (`UnfurlWorker` on `QThreadPool`) that fetches Open Graph (`og:title`, `og:description`, `og:image`, `og:site_name`) and `oEmbed` metadata without relying on a heavy embedded browser engine.
- **Google Drive Deep Unfurling (`drive.google.com`):**
  - Extracts the **real filename** (e.g., `Sam Rivera Jr. 1-2-20 INTERVIEW.mp4`) and **file type badge** (`VIDEO`, `AUDIO`, `DOC`, `FOLDER`) instead of displaying raw unreadable Drive IDs or generic `"Google Drive Asset"` fallback strings.
  - Fetches actual video/file frame thumbnails via `og:image` / Google Drive's thumbnail endpoint (integrating with the app's Cookie Auth Manager when authenticated sessions are available).
- **YouTube & General Web Support:**
  - Pulls video/channel titles, author names, and 16:9 thumbnails via YouTube `oEmbed` and Open Graph tags (supporting unlisted or restricted videos when cookies are active).
  - Parses standard Open Graph tags for Dropbox, Vimeo, news articles, and court documents.
- **Restricted Link Detection:**
  - Displays a clear `🔒 Restricted — Configure Cookies` status when a private folder or file requires authentication rather than silently failing.

---

### 2. Rich Embed Hover Card & Inline UI — ✅ Implemented (`src/ui/components/link_embed_card.py`)
- **Floating Fluent Hover Embed Card:**
  - **Trigger Locations:**
    - Hovering over any URL (`https://...`) or timestamp (`20:52-21:07`) inside `src/ui/components/script_viewer.py` (supporting both **Reading Mode** and **Document Print Layout Mode**).
    - Hovering over the domain/title chip or external link button on timeline cards in `src/ui/components/timeline_card.py`.
    - Hovering over link rows inside `LinksDrawer` in `src/ui/components/drawers.py`.
  - **Smart Hover Dwell (~250ms):** Prevents accidental popup spam when moving the cursor quickly across the workspace.
  - **Interactive Grace Window (~200ms):** Keeps the floating card open when the user moves the cursor onto the card itself, allowing direct clicks on:
    - **`Copy Link`**
    - **`Copy Title`**
    - **`↻ Refresh`**
    - **`Open in Browser`**
- **Inline Timeline Card Chip (`src/ui/components/timeline_card.py`):**
  - Replaces the static `DRIVE.GOOGLE.COM` domain text on widened A-Roll timeline cards with a compact inline preview chip (`[🎬 Real Filename / Video Title]`).

---

### 3. Persistent SQLite Cache (`src/core/database.py`) — ✅ Implemented (`PRAGMA user_version = 2`)
- **Zero-Latency Startup Cache:**
  - Stores unfurled link metadata (`url`, `title`, `description`, `author`, `provider`, `file_type`, `thumbnail_blob` / `thumbnail_url`, `updated_at`) in a dedicated `link_metadata` table inside `knot.db` using `PRAGMA user_version` micro-migrations.
  - Reopening a previously parsed script loads all link embeds and thumbnails in **0ms** without re-fetching over the network.
  - Supports manual invalidation/refresh per-link or globally when cookie settings are updated.

---

### 4. Companion Chrome Extension Cookie Bridge (Deferred — Security & Simplicity)
- **Concept:**
  - A lightweight Manifest V3 Chromium extension (`Rainy Bridge`) using `chrome.cookies.getAll({})` to read decrypted session cookies from Chrome, Brave, Edge, or Opera without requiring Firefox or manual `.txt` exports.
  - **Multi-Account Support (`u/0` .. `u/9`):** Uses Google's `ListAccounts` / `?authuser=N` index switching to display signed-in accounts in an HTML popup and automatically retry `authuser=0, 1, 2...` when a private Google Drive folder is restricted to a specific secondary account.
  - **Guided Drag-and-Drop Setup:** Allows dragging an unpacked extension card directly from Rainy Settings into `chrome://extensions` (when Developer Mode is enabled) using Qt's native `QDrag` (`CF_HDROP`).
- **Why Deferred (Security Rationale):**
  - Running a local HTTP listener (`127.0.0.1`) to receive browser cookies introduces potential cross-origin / localhost CSRF / DNS-rebinding attack surfaces if a user visits a malicious website while the app is open.
  - Until native OS IPC (`chrome.runtime.connectNative` stdio pipe with strict extension ID allowlisting) is warranted, Rainy relies strictly on **zero-port, 100% local cookie ingestion** (`Firefox SQLite shadow-read`, `cookies.txt` file import, and `Raw Cookie Paste`).

