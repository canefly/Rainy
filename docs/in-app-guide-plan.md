# In-app user guide (planned: ships as Rainy 2.0.0)

## Decision (2026-11-23)

- **No public guide or landing page.** `https://rainy.canefly.xyz/` stays an empty 404. Reason: Rainy is closely tied to the owner's work (Rainy), and a public manual would lay out every feature to anyone who might copy the concept.
- **The manual lives inside the app.** Only approved users ever see it. Needs a new build. **Decision (2026-11-23): this build ships as Rainy 2.0.0.** 1.3.0 was a continuation number from the old project, but Rainy is an entire rewrite, so the first announced release is 2.0.0. 1.3.0 stays on the server only as a test build and gets withdrawn once 2.0.0 is published. Because 2.0.0 is a brand-new version number, there is no duplicate-version problem on the server.
- The installer download link is public by design (the app's own updates use it). The gate is device approval, not the link.

## Shape

- **Where:** a "User guide" entry in the existing **Help** menu (title bar), and a link from the first-run "Rainy is ready" note. Opens in **its own separate window like Settings, not a modal dialog** (decided 2026-11-23): it can stay open beside the app while you work, and it never blocks Rainy. Same theme and look as Settings, not a browser.
- **Layout:** a list of topics on the left, the page on the right, with a search box (the Settings window already has a "Find a setting" box to copy the pattern from).
- **Content:** plain pages written as text files shipped inside the app (`assets/guide/*.md` or one structured file). Plain text only, no network needed, works offline. It is the app's own bundled content, so a bad update to the text can never break the app.
- **Later option (not for this build):** load the guide text from the server for approved devices only (like announcements), so the text can change without a release. Only worth it if the guide changes often.

## Decided content (owner's answers, 2026-11-23)

**Tone:** short and practical. Less noise, easy to follow. **Text only**, no screenshots. Not visible before approval (the app is private, which is the whole reason there is no public manual).

**Links inside the guide:** key phrases are links that jump straight to the matching section (for example "set up MCP" jumps to that page), and where it helps, to the matching Settings tab.

**Every page ends with:** "Stuck? Message Cane." plus the @yow_ph handle already shown on the About page.

### Part 1: The basics
1. ~~Getting started~~ (removed from the guide: see the startup help below)
2. Opening a script (drop a .docx or .rainyproj) and what the parser reads
3. A-roll cards (timestamps, durations, checking cards off)
4. Editor Notes (script notes vs your own notes, delete, jump to line)
5. Source links (only the source links part of linking)
6. Export Premiere XML on its own: just the flat A-roll, with no voiceover, no assistance, no smart matching. Says so plainly.

### Part 2: Configuration
7. The clip player (looping, editing In/Out)
8. Settings and themes, and where your files live
9. Shortcuts and tips
10. If something goes wrong (Report a Problem, the log folder)

### Part 3: The automation pipeline (A-roll and voiceover), one linear walkthrough
11. Set up a footage workspace
12. Link footage
13. Transcribe the voiceover
14. Check the script against the voiceover (the VO check)
15. Connect an AI assistant over MCP, then run the automation. Includes how to set up MCP.

Must say plainly in part 3:
- An AI assistant that can talk to a local MCP server is required: Claude Code, Antigravity, Codex or similar.
- Some need a paid plan, some have a free tier with limits, and users should check each one. **Do not put prices or limits in the guide** (they change and go stale); the owner may name a free tier himself once confirmed.
- Some prompting skill helps, but the MCP tool itself carries guidance for the assistant, so users do not need to write long prompts.

## Startup help (decided 2026-11-23)

"Getting started" (request access, waiting for approval, the one-time download) is **not** a page of the full guide: it is too basic. Instead, the Welcome screen gets a small **"?" button**. Someone who has just installed Rainy, sees the editor-name box and does not know what to do clicks it and gets a short, plain explanation: pick your name, press Request access, wait for approval, then the first-run download. This is a separate small piece of work from the guide window.

## Still to decide before writing
- Exact wording of each page: to be drafted by Claude in small batches and approved by the owner before it goes in the app.
- Whether the first-run "Rainy is ready" note links to the guide.

## Steps once the content is decided

1. Write the pages.
2. Build the guide window and the Help entry, with tests (opens, search finds a page, missing page doesn't crash).
3. Look at it in the real app, light and dark mode.
4. Follow `docs/release-cycle.md` with version 2.0.0.

## Build phases (agreed 2026-11-23, nothing built yet)

The guide text is approved (`docs/guide-draft-part1.md`, `part2`, `part3`). Phases run in order, each one tested before the next. Nothing is rebuilt, published or pushed as a release until phase 6, and only with the owner's go-ahead.

| # | Phase | What gets built | Done when |
|---|---|---|---|
| 1 | **Guide content as data** | Turn the three drafts into bundled pages (`assets/guide/`), one file per page, plus a small loader. Links are written `[[link: ...]]` and resolved to a page id or a Settings/Footage target. Drop the "DRAFT" and "CONFIRM" lines. | Loader tests: every page loads, every link target exists, no dead links, no leftover "CONFIRM" or "DRAFT" text. |
| 2 | **Guide window** | A separate window like Settings (not modal): topic list on the left in the three parts, page on the right, search box, clickable links that jump between pages, back button. Follows the theme (light and dark). | Window tests: opens, search finds a page, links navigate, a missing page never crashes. |
| 3 | **Hook it up** | Help menu gets **User guide**. Links that point outside the guide open the right place: Settings tabs (AI Models, MCP, Cookies & Accounts), the Footage Manager. Not reachable before approval. | Tests: menu entry opens the window, each outside link opens its target. |
| 4 | **Startup "?" help** | A small **?** button on the Welcome screen with the first-steps text (pick a name, Request access, wait for approval, the one-time download). | Test: button exists on the Welcome screen only and shows the text. |
| 5 | **Check it for real** | Look at everything in the real app, light and dark: guide window, "?" button, the Data Vault counters after opening a new script. Also check the things flagged but not verified: the Core Engines speech-engine **update** button in an installed build (it runs `pip` through the app's own exe), and the Source links Info wording. Full test run. | Owner has seen it and the test run has only the known failures. |
| 6 | **Release 2.0.0** | Bump the version to **2.0.0** in `src/core/updater.py` (`APP_VERSION`) and `installer/setup.iss` (`MyAppVersion`), then follow `docs/release-cycle.md`: build, test the installer in Windows Sandbox, upload `RainySetup_v2.0.0.exe` and the portable zip, publish 2.0.0, then **withdraw 1.3.0** so 2.0.0 is the latest. Machines that have 1.3.0 (your own) will be offered 2.0.0 through the update system, which doubles as a real test of the update flow. | Installer tested on a clean machine, the download link serves the new file, and an installed 1.3.0 offers the update. |

**Decisions needed before phase 3:** the **F1** key already opens Keyboard Shortcuts. Proposal: leave F1 alone and give the guide its own entry in the Help menu with no shortcut (or a different key if the owner wants one).
