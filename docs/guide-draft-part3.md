# User guide, draft: Part 3, the automation pipeline

DRAFT for the owner to review. Text only, short and practical. `[[link: ...]]` marks a clickable jump to another page or a Settings tab. Items marked **CONFIRM** are things I could not fully verify from the code; please check them or tell me what is wrong before this goes in the app. No prices anywhere, on purpose.

---

## P0. Which way do I want to work?

**Quick way (no assistant):** open your script, then use **Export, Premiere XML**. You get the A-roll cards laid flat on a timeline. No voiceover, no matching, nothing smart. See [[link: Export Premiere XML on its own]].

**Automatic way (this part):** Rainy lines up your voiceover with the script, cuts out all the silence, and places the A-roll where the script calls for it. For this you need an AI assistant connected to Rainy. Do these five steps in order:

1. [[link: Open the Footage Manager]]
2. [[link: Link your footage]]
3. [[link: Transcribe the voiceover]]
4. [[link: Check the voiceover]] (optional, but quick)
5. [[link: Connect an AI assistant]], then ask it to build the timeline

---

## P11. Open the Footage Manager

The **Footage Manager** is the video icon in the left bar. It opens a window called **Footage & Media** with four tabs, left to right:

| Tab | What it is for |
|---|---|
| **Relink** | Tell Rainy where your footage lives on this computer |
| **Library** | See every linked clip, find broken or odd ones |
| **Transcribe** | Turn your voiceover audio into text with timings |
| **Review** | Optional quick check of the voiceover cuts |

Open your script first. The Footage Manager works on the script that is open.

Next: [[link: Link your footage]]

---

## P12. Link your footage

Your script contains links to the clips. Rainy needs to know where those clips are on this computer.

1. Open the **Relink** tab.
2. Choose the folder that holds the downloaded footage (**Change folder…**).
3. Rainy scans the folder and matches files to the script's links by file size. When it is done, the line at the top tells you how many are linked and how many are missing.
4. For anything missing, use **Copy missing links** to get the source addresses so you can download them, then press the rescan button.
5. Tick **Auto-relink others in same folder** if you want one found file to pull in its neighbours.
6. Press **Open Library →** to check the result.

In the **Library** tab, a yellow banner points out clips with a variable frame rate (VFR). These can drift out of sync in Premiere. Select them and press **Re-encode Clips** to convert them.

If a clip is offline, the Library banner says so, with **Fix in Relink**.

Next: [[link: Transcribe the voiceover]]

---

## P13. Transcribe the voiceover

Rainy listens to your voiceover and writes down every line with its timing. Everything it does later depends on this step.

1. Open the **Transcribe** tab.
2. Press **Add audio parts** and pick your voiceover files, in order (Part 1, Part 2 …).
3. Press **Start queue**. Each file is transcribed on this computer; nothing is uploaded. Long files take a while, and you can keep working.
4. When it finishes, the bottom line shows the transcript, with a **Reveal** button to find the file.

What Rainy does automatically after each part, with nothing to click:
- It finds where you actually speak and **deletes all the silence** between.
- It keeps a tiny bit of room before and after each phrase so no word is clipped.
- It never changes your original audio file.

**CONFIRM:** if no speech model is installed yet, Rainy needs one before this works. Get it in [[link: Settings, AI Models]]. I did not check what the Transcribe tab says if none is installed.

Next: [[link: Check the voiceover]]

---

## P14. Check the voiceover (optional)

The **Review** tab is a quick pass over the things a script can spot but should not decide alone. You can skip it. If you skip it, everything with sound is kept and the assistant mentions that review was skipped.

Rainy flags:

| Flag | What it means | Your choice |
|---|---|---|
| Duplicate take | The same sentence recorded twice | Pick a take, or keep all |
| False start | A sentence abandoned and restarted | Keep or drop |
| No words | A loud sound with no words (laugh, shout) | Keep or drop |
| Breath | A short quiet sound | Keep or drop |

Keys: **Space** plays, **↑ ↓** moves, **K** keeps, **D** drops, **1–9** picks a take.

One slider, **Noise sensitivity**: raise it if hiss is being kept, lower it if quiet word endings are being cut. Greyed-out audio in the waveform is what will be deleted.

Silence is never flagged. It is always deleted.

Next: [[link: Connect an AI assistant]]

---

## P15. Connect an AI assistant

The assistant reads your script and the transcript, decides which gaps in the voiceover get A-roll, and asks Rainy to build the timeline. Rainy cuts exactly where the voiceover has a natural gap, so A-roll never lands in the middle of a word.

### What you need

- An AI assistant that can talk to a local MCP tool. Rainy sets up these: **Claude Code, Claude Desktop, Codex, Antigravity, Gemini CLI, Cursor, VS Code**.
- **Some of them need a paid plan, some have a free tier with limits.** Check each one's own page before you pick.
- You do not need to write long prompts. Rainy gives the assistant its own instructions, so "build the timeline" is enough to start.

### Set it up (once)

1. Open **Settings, MCP**.
2. In **Live bridge**, make sure the switch is **on** (badge says *Listening*).
3. In **Install in your AI apps**, find your assistant and press **Install**.
   - If it says *Not found*, you can still press **Install anyway**.
   - Rainy saves a backup of the assistant's settings file first and only touches its own entry.
4. **Restart the assistant** (or reload its tools) so it picks Rainy up.
5. Back in Rainy, press **Test connection**. Three lines should turn green: start, list tools, reach Rainy.

### Use it

1. Open your script in Rainy, with footage linked and the voiceover transcribed.
2. Keep Rainy open.
3. In your assistant, say hello or ask for the timeline. It will check your project and offer choices, for example:
   - build the timeline (XML for Premiere, or straight into Premiere if it is connected)
   - audit the script against the voiceover (what was read as written, rephrased, ad-libbed, retaken or skipped)
   - search the script or voiceover
   - check the project for problems
4. Pick the frame rate when it asks.
5. It builds the timeline and tells you what it did, including how many silent seconds were removed.

Watch it work live in **Settings, MCP, Live activity**: every call it makes, in order.

### If something goes wrong

- **Assistant says it cannot find Rainy:** restart it after installing, and check Rainy is open with the bridge switched on.
- **Test connection fails at step 1:** press **Install** again, then restart the assistant.
- **Still stuck?** Message Cane.
