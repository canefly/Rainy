# User guide, draft: Part 1, the basics

DRAFT for the owner to review. Text only, short and practical. `[[link: ...]]` marks a clickable jump to another page or a Settings tab. Items marked **CONFIRM** are things I could not fully verify from the code. Button and menu names are copied from the app.

---

## P1. Getting started

1. **Install Rainy** and open it.
2. On the **Welcome to Rainy** screen, type the name you edit under and press **Request access**. Cane approves every editor before the app unlocks.
3. You will see **Waiting for approval**. Leave Rainy open. It unlocks by itself when Cane approves you (or press **Check now**).
4. The first time after approval, a small window called **Getting Rainy ready** appears. Rainy downloads the helper programs it needs (a video player and video tools, plus an optional photo-censoring model). This happens once. You can keep using Rainy while it works.

If you see **Access revoked**, **Can't reach the server** or **Your clock is off**, the screen tells you what to do. Anything else: message Cane.

Next: [[link: Open a script]]

---

## P2. Open a script

Drop a **.docx** script or a **.rainyproj** project anywhere on the Home screen, or click the **Script Parser** card to browse. You can also use **File, Open Document…** or **File, Open Project (.rainyproj)…**. Scripts you opened before are listed under **Recent scripts**.

**What Rainy reads in your script**

- A line with an **@editor** note and a **timestamp** becomes an A-roll card. Example: `@editor play the clip 0:01:36 - 0:01:49`.
- A timestamp can be a range (`0:01:36 - 0:01:49`, or with "to") or a single time.
- An @editor note with **no timestamp** is moved to [[link: Editor Notes]] and Rainy warns you.
- Links in the script are collected as [[link: Source links]].

Your work is saved as a **.rainyproj** project in `Documents\Rainy\Projects` as you go. To pull in changes after you edit the document, use **File, Re-import / Refresh Script from DOCX…**.

Next: [[link: A-roll cards]]

---

## P3. A-roll cards

Each A-roll becomes a card on the right of the workspace, in order.

| On the card | What it does |
|---|---|
| Round box at the left | Tick it when you have finished that cue |
| Number and label chip | The cue's row number and its label **CONFIRM: where the label (for example S10E20) comes from** |
| **▶** (Play clip on loop) | Opens the [[link: clip player]] to watch the cut on a loop. Needs the footage to be linked |
| Start time, **DUR**, end time | The cut's In point, its length and its Out point. **Click a time to copy it** for Premiere Pro |

Handy: **Edit, Mark All Items Done** ticks everything, and **Edit, Reset All Checklists** clears it.

Next: [[link: Editor Notes]]

---

## P4. Editor Notes

Open them with the pencil icon in the left bar. Notes are the instructions from the script that are not a cue, plus any note you add yourself.

- **Filter** the list: All, Done or Pending.
- **Tick** a note when it is handled, or use **Mark all as done**.
- **Magnifier** jumps to that place in the script.
- **Copy** copies the note text.
- **Add Note** adds your own note to the list.
- **Trash can** deletes a note. It only shows on notes **you** added. Notes that come from the script cannot be deleted here, because they would come back the next time the script is read.

Next: [[link: Source links]]

---

## P5. Source links

Open them with the link icon in the left bar. It lists every link found in your script, as a checklist.

- **Tick** a link when you have downloaded or used it.
- **Relink** attaches a local footage file to a link, for when you already have the clip on this computer.
- **Info** shows details about a link.

To link all your footage at once instead, see [[link: Link your footage]].

**CONFIRM:** I summarised this from the code, not from using it. Please check the buttons on the Source Links window and tell me if anything is wrong or missing.

Next: [[link: Export Premiere XML on its own]]

---

## P6. Export Premiere XML on its own

The simple export. Choose **Export, Premiere Pro XML Sequence (.xml)…** (shortcut **Ctrl+E**) with a script open.

What you get: your **A-roll cards laid out flat** on a timeline, ready to import into Premiere Pro. That is all. **No voiceover, no matching, no automatic placement, no assistant needed.**

Want the voiceover cut and the A-roll placed for you? That is the automatic way: [[link: Which way do I want to work?]]

**Export, Export Timestamps (.txt / .csv)…** gives you the cue times as a plain text or spreadsheet file instead.

Next (the rest of the guide): [[link: Part 2, configuration]]
