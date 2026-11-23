id: open-script
title: Open a script
part: 1
part_title: The basics
order: 1

Drop a **.docx** script or a **.rainyproj** project anywhere on the Home screen, or click the **Script Parser** card to browse. You can also use **File, Open Document…** or **File, Open Project (.rainyproj)…**. Scripts you opened before are listed under **Recent scripts**.

**What Rainy reads in your script**

- A line with an **@editor** note and a **timestamp** becomes an A-roll card. Example: `@editor play the clip 0:01:36 - 0:01:49`.
- A timestamp can be a range (`0:01:36 - 0:01:49`, or with "to") or a single time.
- An @editor note with **no timestamp** is moved to [Editor Notes](guide:editor-notes) and Rainy warns you.
- Links in the script are collected as [Source links](guide:source-links).

Your work is saved as a **.rainyproj** project in `Documents\Rainy\Projects` as you go. To pull in changes after you edit the document, use **File, Re-import / Refresh Script from DOCX…**.

Next: [A-roll cards](guide:aroll-cards)
