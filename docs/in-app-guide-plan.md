# In-app user guide (planned for 1.3.1)

## Decision (2026-11-23)

- **No public guide or landing page.** `https://rainy.canefly.xyz/` stays an empty 404. Reason: Rainy is closely tied to the owner's work (Rainy), and a public manual would lay out every feature to anyone who might copy the concept.
- **The manual lives inside the app.** Only approved users ever see it. Needs a new build, so it ships as 1.3.1, bundled with whatever else real use turns up (one build, not one per idea).
- The installer download link is public by design (the app's own updates use it). The gate is device approval, not the link.

## Shape

- **Where:** a "User guide" entry in the existing **Help** menu (title bar), and a link from the first-run "Rainy is ready" note. Opens a normal Rainy window (same theme and look as About / Settings), not a browser.
- **Layout:** a list of topics on the left, the page on the right, with a search box (the Settings window already has a "Find a setting" box to copy the pattern from).
- **Content:** plain pages written as text files shipped inside the app (`assets/guide/*.md` or one structured file). Plain text only, no network needed, works offline. It is the app's own bundled content, so a bad update to the text can never break the app.
- **Later option (not for 1.3.1):** load the guide text from the server for approved devices only (like announcements), so the text can change without a release. Only worth it if the guide changes often.

## Draft outline (to be filled in with the owner)

1. Getting started (install, name, waiting for approval, the first-run download)
2. Opening a script (drop a .docx or .rainyproj) and what the parser reads
3. A-roll cards (timestamps, durations, checking cards off)
4. The clip player (looping, editing In/Out, shortcuts)
5. Editor Notes (script notes vs your own notes, delete, jump to line)
6. Source links and linking footage
7. Voiceover pipeline and the script-vs-VO check
8. Exporting (Premiere XML / sending to Premiere)
9. The Rainy MCP bridge (using it with an AI assistant)
10. Settings, themes, and where your files live
11. Shortcuts and tips
12. If something goes wrong (Report a Problem, the log folder)

## Open questions for the owner

- Which of the topics above are really worth writing, and which features should NOT be explained in detail at all?
- Tone: short and practical, or friendly and chatty?
- Screenshots in the guide, or text only? (Screenshots make the installer bigger and go out of date.)
- Should the guide be visible before approval? (Currently no: the app shows only the waiting screen until you approve, so it would be unreachable anyway.)

## Steps once the content is decided

1. Write the pages.
2. Build the guide window and the Help entry, with tests (opens, search finds a page, missing page doesn't crash).
3. Look at it in the real app, light and dark mode.
4. Follow `docs/release-cycle.md` for 1.3.1.
