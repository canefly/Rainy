id: transcribe
title: Transcribe the voiceover
part: 3
part_title: The automation pipeline
order: 13

Rainy listens to your voiceover and writes down every line with its timing. Everything it does later depends on this step.

1. Open the **Transcribe** tab.
2. Press **Add audio parts** and pick your voiceover files, in order (Part 1, Part 2 …).
3. Press **Start queue**. Each file is transcribed on this computer; nothing is uploaded. Long files take a while, and you can keep working.
4. When it finishes, the bottom line shows the transcript, with a **Reveal** button to find the file.

**Heads up: this is heavy.** Transcribing runs an AI model on your computer. If you have an NVIDIA graphics card, Rainy uses it, and it can take a big share of your video memory (VRAM). **Close other editing and creative programs first** (Premiere, After Effects, DaVinci Resolve, games) so they do not fight for it. How fast it goes depends on your card and how much VRAM it has: a stronger card is much quicker. With no usable NVIDIA card, Rainy uses the processor instead, which works but is slower.

What Rainy does automatically after each part, with nothing to click:
- It finds where you actually speak and **deletes all the silence** between.
- It keeps a tiny bit of room before and after each phrase so no word is clipped.
- It never changes your original audio file.

**Which speech model?** Transcribing uses a speech model. The default is a small one called **Base English**. If it is not on your computer yet, Rainy downloads it by itself the first time you press **Start queue**, and the progress shows in the terminal strip ("Downloading Base English…"). It only happens once. The dropdown next to **Add audio parts** lists the models you have. To see how big each model is, download others or remove them, open [Settings, AI Models](app:settings/ai-models). Bigger models are more accurate but slower and use more memory.

Next: [Check the voiceover](guide:check-voiceover)
