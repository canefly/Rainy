# User guide, draft: Part 2, configuration

DRAFT for the owner to review. Text only, short and practical. `[[link: ...]]` marks a clickable jump to another page or a Settings tab. Items marked **CONFIRM** are things I could not fully verify from the code. Menu and button names are copied from the app.

---

## P6. The clip player

Press **▶** on any A-roll card. A separate window opens and plays the cut on a loop. It is always dark, whatever theme the rest of Rainy uses, so it is easy on the eyes while you check footage.

- **Top left:** the clip's file name, a **VFR** badge if the clip has a variable frame rate (those can drift out of sync in Premiere), and an **Anchor** dropdown (zoom around the cut's middle or around the playhead).
- **Under the video:** the In time on the left, the playhead time in the middle, the Out time on the right, and a timeline strip with the cut shown as brackets.
- **Bottom:** frame step, play, and previous / next card.
- **Edit** (top right) switches to trimming. Everything you can do there is on [[link: A-roll cards]].

If it says **Unlinked Footage** or **No local footage file linked to this timestamp card**, the clip is not linked yet: [[link: Link your footage]].

---

## P7. Settings and themes

Open **Settings** from the left bar or the Home screen. The sections:

| Section | What it is for |
|---|---|
| **Home** | A summary and quick switches |
| **Data Vault** | Your saved projects, the activity log, the media cache and the list of scripts you opened. See below |
| **Media & Transcoding** | The video encoder used when Rainy converts clips |
| **Core Engines** | The helper programs (video player, video tools, speech engine). Check, download or fix them here |
| **Personalization** | Themes and looks (below) |
| **Shortcuts** | The keyboard and mouse shortcuts: [[link: Shortcuts and tips]] |
| **Cookies & Accounts** | Lets Rainy read private Google Drive links: [[link: Settings, Cookies & Accounts]] |
| **AI Models** | The speech models for transcribing, and the optional photo-censoring model |
| **MCP** | Connecting an AI assistant: [[link: Connect an AI assistant]] |
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

---

## P8. Shortcuts and tips

**Anywhere**

| Keys | What it does |
|---|---|
| **Ctrl+O** | Open a document |
| **Ctrl+Shift+S** | Save a project copy as… |
| **Ctrl+F** | Find in the script |
| **Ctrl+E** | Export the Premiere XML |
| **F1** | Show the keyboard shortcuts |

**On a card or in the script**

| Do this | What happens |
|---|---|
| Click a timecode | Copies it |
| Double-click a timecode | Edit it by hand. **Enter** saves |
| Click the label chip | Copies the tag and scrolls the script to that section |
| Drag an extracted image | Drop it straight into Premiere or DaVinci |

**In the clip player**

**Space** play or pause, **I** and **O** set In and Out, **← →** step a frame, **↑ ↓** previous and next card, **Ctrl+Z** undo, **R** reset to the script's original times. Full table: [[link: A-roll cards]].

---

## P9. If something goes wrong

1. **Report a Problem.** **Help, Report a Problem…** Say what you were doing and what happened. Keep **Include my recent app log** ticked, it helps a lot. Press **Show what will be sent** to see exactly what goes. Nothing is sent until you press **Send report**.
2. **Helper programs missing or broken.** Open **Settings, Core Engines**. They can be checked and downloaded again there.
3. **It will not connect or unlock.** The screen tells you why (offline, clock wrong, access revoked). Fix that and press **Retry**.
4. **Need the log yourself?** Go to **Settings, Data Vault, Activity & Diagnostics Logs**. **Open Folder** shows it, and **Export (.zip)** bundles the logs into one file you can send to Cane. The log itself is a text file: `%LOCALAPPDATA%\com.canefly.knot\logs\knot.log`.
5. **Still stuck?** Message Cane.
