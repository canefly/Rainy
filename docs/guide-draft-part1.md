# User guide, draft: Part 1, the basics

DRAFT for the owner to review. Text only, short and practical. `[[link: ...]]` marks a clickable jump to another page or a Settings tab. Items marked **CONFIRM** are things I could not fully verify from the code. Button and menu names are copied from the app.

---

## P1. Open a script

Drop a **.docx** script or a **.rainyproj** project anywhere on the Home screen, or click the **Script Parser** card to browse. You can also use **File, Open Document…** or **File, Open Project (.rainyproj)…**. Scripts you opened before are listed under **Recent scripts**.

**What Rainy reads in your script**

- A line with an **@editor** note and a **timestamp** becomes an A-roll card. Example: `@editor play the clip 0:01:36 - 0:01:49`.
- A timestamp can be a range (`0:01:36 - 0:01:49`, or with "to") or a single time.
- An @editor note with **no timestamp** is moved to [[link: Editor Notes]] and Rainy warns you.
- Links in the script are collected as [[link: Source links]].

Your work is saved as a **.rainyproj** project in `Documents\Rainy\Projects` as you go. To pull in changes after you edit the document, use **File, Re-import / Refresh Script from DOCX…**.

Next: [[link: A-roll cards]]

---

## P2. A-roll cards

Each A-roll becomes a card on the right of the workspace, in order.

| On the card | What it does |
|---|---|
| Round box at the left | Tick it when you have finished that cue |
| Number and label chip | The cue's row number and its show label, for example **S10E20** (Season 10, Episode 20) |
| **▶** (Play clip on loop) | Opens the clip player and plays the cut on a loop. **Needs the footage to be linked** |
| Start time, **DUR**, end time | The cut's In point, its length and its Out point. **Click a time to copy it** for Premiere Pro |

**Times are conformed for you.** Once your footage is linked, Rainy automatically converts each time to the exact frame (HH:MM:SS:FF) of your clip, so what you copy is what Premiere expects. This is on by default (**Settings, Timecode Conforming Engine, Auto-Conform to SMPTE Timecode**). Without linked footage, the cards show the raw times from the script and the player cannot play the clip. To link your footage: [[link: Link your footage]].

**Fix a wrong cut precisely.** Press **▶** to open the clip player, then **Edit**. Now you can move the cut exactly where it should be:

| Do this | Keys |
|---|---|
| Set the In point at the playhead | **I** |
| Set the Out point at the playhead | **O** |
| Step one frame back or forward | **←** and **→** |
| Play or pause | **Space** |
| Undo the last change | **Ctrl+Z** |
| Go back to the script writer's original times | **R** |
| Move to the previous or next card | **↑ / P** and **↓ / N** |
| Apply the change and go back to looping | **Edit** button, **Ctrl+S**, **E** or **Esc** |

The timeline strip under the video shows the cut as brackets that you can see and adjust, with zoom, so a wrong cut is easy to spot and fix. The card updates by itself when you apply.

Handy: **Edit, Mark All Items Done** ticks everything, and **Edit, Reset All Checklists** clears it.

Next: [[link: Editor Notes]]

---

## P3. Editor Notes

Open them with the pencil icon in the left bar. Notes are the instructions from the script that are not a cue, plus any note you add yourself.

- **Filter** the list: All, Done or Pending.
- **Tick** a note when it is handled, or use **Mark all as done**.
- **Magnifier** jumps to that place in the script.
- **Copy** copies the note text.
- **Add Note** adds your own note to the list.
- **Trash can** deletes a note. It only shows on notes **you** added. Notes that come from the script cannot be deleted here, because they would come back the next time the script is read.

Next: [[link: Source links]]

---

## P4. Source links

Open them with the link icon in the left bar. Think of it as a smart checklist: it lists every link found in your script and tells you about each one.

- **Tick** a link when you have handled it.
- **Info** shows what the link says (its title and details), and whether its footage is linked on this computer.
- You **cannot link footage here.** This window only tells you the state. If a link shows its footage is **missing**, go back to the Footage Manager and fix it there: [[link: Link your footage]].

**Link details not showing?** For private Google Drive links, Rainy needs permission to read them. Set it up in [[link: Settings, Cookies & Accounts]] by importing your Firefox cookies, using a Firefox that is signed in to the Google account that can open those files.

Next: [[link: Export Premiere XML on its own]]

---

## P5. Export Premiere XML on its own

The simple export. Choose **Export, Premiere Pro XML Sequence (.xml)…** (shortcut **Ctrl+E**) with a script open.

What you get: your **A-roll cards laid out flat** on a timeline, ready to import into Premiere Pro. That is all. **No voiceover, no matching, no automatic placement, no assistant needed.**

Want the voiceover cut and the A-roll placed for you? That is the automatic way: [[link: Which way do I want to work?]]

**Export, Export Timestamps (.txt / .csv)…** gives you the cue times as a plain text or spreadsheet file instead.

Next (the rest of the guide): [[link: Part 2, configuration]]
