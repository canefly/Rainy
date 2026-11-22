# Rainy docs

Plain-language explanations of how the bigger features work, so you can pick a feature back up after time away.

| Doc | What it covers |
|---|---|
| [Voiceover pipeline](vo-pipeline.md) | Transcribe → snap to the waveform (silence deleted) → Review tab → AI places A-roll → export |
| [macOS port plan](mac-porting-plan.md) | Planned (not built): core fixes, native title bar, vibrancy / Liquid Glass instead of DWM, packaging |
| [rainy-api plan](rainy-api-plan.md) | Spec for the FastAPI backend: approval gate, signed licenses, WebSocket presence, announcements, bugs, analytics (becomes real docs once built) |
| [App folder](app-folder.md) | Built: the one folder for the database, engines, models, cache, logs and updates, and how engines are found |
| [Rebrand plan](rebrand-plan.md) | In progress (phases 1-4, 6, 7 done; build and final sweep left): Rainy → Rainy (what users see) and knot (internal names), full rename map, phases, gotchas |
| [Workspace layout](workspace-layout.md) | How the native app, the legacy `click` HTML version and the new `rainy-api` FastAPI folder relate |

Add a page here whenever a feature has more than one moving part.
