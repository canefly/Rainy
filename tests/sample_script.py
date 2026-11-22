"""A made-up documentary script, built with python-docx the first time a test asks for it.

It exercises what real scripts do: a title (Heading 1), numbered section headings (Heading 2, one with a
double space), @vo and @editor lines, clip lines with a source code and a timestamp, Google-Drive-style
hyperlinks (several clips share one file, so they must share one colour), bold narration, and
episode-numbered sources like S10E20 whose number must never leak into the timestamp's hours.

Nothing here is real: the show, the episodes and the links are invented.
"""
import os
import tempfile

from docx import Document
from docx.shared import Inches
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

SAMPLE_NAME = "Every Time the Lighthouse Went Dark.docx"
TITLE = "Every Time the Lighthouse Went Dark"
FIRST_SECTION = "Hold The Line"                 # its heading has a double space, like real scripts

DRIVE = "https://drive.google.com/file/d/{}/view?usp=sharing"
FILE_A = DRIVE.format("1LanternBayS10E20aaaaaaaaaaaaaaaaaaaa")
FILE_B = DRIVE.format("1LanternBayS03E07bbbbbbbbbbbbbbbbbbbb")
FILE_C = DRIVE.format("1LanternBayS05E01cccccccccccccccccccc")
FILE_D = DRIVE.format("1LanternBayIntrodddddddddddddddddddd")
FILE_E = DRIVE.format("1LanternBayS02E09eeeeeeeeeeeeeeeeeeee")

# (source code, start, end, file, narration that follows)
SECTIONS = [
    (f"1/3. {FIRST_SECTION}  - Static", "S10E20", FILE_A, [
        ("00:37", "00:48", "The keeper hears the radio crackle for the first time."),
        ("1:36", "1:49", "Nobody believes him, of course."),
        ("2:10", "2:21", "Then the beam stutters."),
        ("3:02", "3:15", "Every boat in the bay turns back."),
        ("4:40", "4:52", "He climbs the stairs one more time."),
        ("5:55", "6:08", "And the lamp goes out."),
    ]),
    ("2/3. The Night Shift, S03E07", "S03E07", FILE_B, [
        ("0:12", "0:24", "A new keeper arrives with a very old map."),
        ("1:05", "1:19", "The logbook has pages nobody wrote."),
        ("2:48", "3:01", "Somebody has been winding the clock."),
    ]),
    ("3/3. Last Light, S05E01", "S05E01", FILE_C, [
        ("0:30", "0:41", "At dawn the tower is empty."),
        ("1:22", "1:35", "Only the lamp is warm."),
    ]),
    # a long section so the script is long enough to scroll and to span several pages
    ("4/4. The Long Watch, S02E09", "S02E09", FILE_E, [
        (f"{i // 6}:{(i * 7) % 60:02d}", f"{i // 6}:{(i * 7) % 60 + 9:02d}", f"The watch drags on, hour {i + 1}.")
        for i in range(36)
    ]),
]

CLIP_COUNT = sum(len(clips) for _, _, _, clips in SECTIONS) + 3       # +3 intro clips
LINK_COUNT = 5                                                        # distinct files above
S10E20_CLIPS = len(SECTIONS[0][3])


def _link(paragraph, url, text):
    rid = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), rid)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    r.append(t)
    h.append(r)
    paragraph._p.append(h)


def _clip_line(doc, code, start, end, url):
    p = doc.add_paragraph()
    _link(p, url, f"@editor play clip, {code} {start} - {end}")
    return p


def build_sample_script(path: str) -> str:
    doc = Document()
    for section in doc.sections:                  # 1 inch all round, like a normal script
        section.left_margin = section.right_margin = Inches(1)
        section.top_margin = section.bottom_margin = Inches(1)
    doc.add_heading(TITLE, level=1)
    p = doc.add_paragraph()
    p.add_run("@vo don't read the text in bold or pink").bold = False
    doc.add_paragraph()
    intro = doc.add_paragraph()
    intro.add_run("The old lighthouse on Lantern Bay has gone dark more than once. ")
    doc.add_paragraph()
    doc.add_paragraph("(@editor play the three intro clips over the narration at the same time as b roll)")
    doc.add_paragraph()
    for label, (start, end) in zip(("From the storm", "Through the fog", "And the silence"),
                                   (("6:37", "6:43"), ("4:31", "4:41"), ("9:33", "9:44"))):
        doc.add_paragraph(f"{label}...")
        doc.add_paragraph()
        _clip_line(doc, "S13E01", start, end, FILE_D)
        doc.add_paragraph()
    bold = doc.add_paragraph()
    bold.add_run("Today we look at every time the light failed.").bold = True
    doc.add_paragraph()
    for heading, code, url, clips in SECTIONS:
        doc.add_heading(heading, level=2)
        doc.add_paragraph()
        for start, end, line in clips:
            _clip_line(doc, code, start, end, url)
            doc.add_paragraph()
            doc.add_paragraph(line)
            doc.add_paragraph()
    doc.add_paragraph("Thanks for watching.")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    doc.save(path)
    return path


_CACHE = {}


def sample_docx_path() -> str:
    """Path of the sample script, built once per test run in a temp folder."""
    if "path" not in _CACHE or not os.path.exists(_CACHE["path"]):
        folder = tempfile.mkdtemp(prefix="knot_sample_")
        _CACHE["path"] = build_sample_script(os.path.join(folder, SAMPLE_NAME))
    return _CACHE["path"]
