id: settings
title: Settings and themes
part: 2
part_title: Configuration
order: 7

Open **Settings** from the left bar or the Home screen. The sections:

| Section | What it is for |
|---|---|
| **Home** | A summary and quick switches |
| **Data Vault** | Your saved projects, the activity log, the media cache and the list of scripts you opened. See below |
| **Media & Transcoding** | The video encoder used when Rainy converts clips |
| **Core Engines** | The helper programs (video player, video tools, speech engine). Check, download or fix them here |
| **Personalization** | Themes and looks (below) |
| **Shortcuts** | The keyboard and mouse shortcuts: [Shortcuts and tips](guide:shortcuts) |
| **Cookies & Accounts** | Lets Rainy read private Google Drive links: [Settings, Cookies & Accounts](app:settings/cookies) |
| **AI Models** | The speech models for transcribing, and the optional photo-censoring model |
| **MCP** | Connecting an AI assistant: [Connect an AI assistant](guide:connect-ai) |
| **About & Updates** | Your version, and **Check for Updates** |

There is a search box at the top: type what you are looking for and it jumps to the right place.

**Themes (Personalization, Appearance & Themes)**

- **Dark Mode** switch. The first time you open Rainy it starts in dark.
- **Color Palette Preset:** Femboy Pink, Cloudy Blue, Matcha Green, Adobe Gray or OLED Black. **Adobe Gray is the default.**
- **Ambient Mica Orb Glow** and **Window Glass Transparency** sliders for the coloured glow and see-through look. They apply to the glassy palettes (Pink, Blue, Green). Adobe Gray and OLED Black are solid.

**Data Vault**

- **Memory File Save (.rainyproj):** Rainy saves your checklist progress, your own notes and your linked footage paths into a project file as you work. Your project lives on without the original .docx, so losing the document does not lose your work. **Open Projects Folder** takes you to the files.
- **Activity & Diagnostics Logs:** a small log (it never grows past 5 MB) that helps when something goes wrong. **Open Folder**, **View Live Log…**, **Export (.zip)** and **Clear** are here. Use the **Level** dropdown to choose how much it records.
- **Integrated Media Probe Engine:** shows the video tools Rainy uses to read your clips, and how many clips it remembers. **Clear Media Cache** forgets them, and Rainy re-reads them when needed.
- **Cached Scripts:** every script you have opened. The folder button opens its saved project.

**Where Rainy keeps your things**

- **App data** (settings, downloaded helper programs and models, logs): `%LOCALAPPDATA%\com.canefly.knot`. Everything is in this one folder, so it is easy to back up or delete.
- **Your projects:** `Documents\Rainy\Projects` (.rainyproj files).
- **Photos pulled from scripts:** `Documents\Rainy\Photos Temp`.
