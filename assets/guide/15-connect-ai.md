id: connect-ai
title: Connect an AI assistant
part: 3
part_title: The automation pipeline
order: 15

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
