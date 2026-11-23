# In-app user guide (planned: folded into 1.3.0, not a new version)

## Decision (2026-11-23)

- **No public guide or landing page.** `https://rainy.canefly.xyz/` stays an empty 404. Reason: Rainy is closely tied to the owner's work (Rainy), and a public manual would lay out every feature to anyone who might copy the concept.
- **The manual lives inside the app.** Only approved users ever see it. Needs a new build. **Decision: do not bump the version.** Rainy has not been announced yet, so 1.3.0 is still the first release; the guide and anything else real use turns up get folded into a rebuilt 1.3.0 (one build, not one per idea). Before replacing the published file, check how the server handles re-publishing the same version number (the release records a SHA-256 and size, and `publish-release` may refuse a duplicate version): likely withdraw the old record and publish again, and reinstall by hand on the few machines that already have 1.3.0, since they will not be offered an update for the same number.
- The installer download link is public by design (the app's own updates use it). The gate is device approval, not the link.

## Shape

- **Where:** a "User guide" entry in the existing **Help** menu (title bar), and a link from the first-run "Rainy is ready" note. Opens a normal Rainy window (same theme and look as About / Settings), not a browser.
- **Layout:** a list of topics on the left, the page on the right, with a search box (the Settings window already has a "Find a setting" box to copy the pattern from).
- **Content:** plain pages written as text files shipped inside the app (`assets/guide/*.md` or one structured file). Plain text only, no network needed, works offline. It is the app's own bundled content, so a bad update to the text can never break the app.
- **Later option (not for this build):** load the guide text from the server for approved devices only (like announcements), so the text can change without a release. Only worth it if the guide changes often.

## Decided content (owner's answers, 2026-11-23)

**Tone:** short and practical. Less noise, easy to follow. **Text only**, no screenshots. Not visible before approval (the app is private, which is the whole reason there is no public manual).

**Links inside the guide:** key phrases are links that jump straight to the matching section (for example "set up MCP" jumps to that page), and where it helps, to the matching Settings tab.

**Every page ends with:** "Stuck? Message Cane." plus the @yow_ph handle already shown on the About page.

### Part 1: The basics
1. Getting started (install, editor name, waiting for approval, the first-run download)
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

## Still to decide before writing
- Exact wording of each page: to be drafted by Claude in small batches and approved by the owner before it goes in the app.
- Whether the first-run "Rainy is ready" note links to the guide.

## Steps once the content is decided

1. Write the pages.
2. Build the guide window and the Help entry, with tests (opens, search finds a page, missing page doesn't crash).
3. Look at it in the real app, light and dark mode.
4. Follow `docs/release-cycle.md`, keeping the version at 1.3.0 (see the note above about re-publishing the same number).
