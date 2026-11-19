import os
import sys

import pytest
from docx import Document
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QColor, QImage, QTextCursor
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core import parser
from src.core.parser import parse_docx_file, build_raw_html
from src.ui.components.script_viewer import ScriptViewer


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def photo_docx(tmp_path, qapp):
    png = tmp_path / "source.png"
    img = QImage(640, 360, QImage.Format_RGB32)
    img.fill(QColor("#cc3344"))
    assert img.save(str(png))

    docx_path = tmp_path / "Photo Script.docx"
    doc = Document()
    doc.add_paragraph("Intro line before the photo.")
    doc.add_picture(str(png))
    doc.add_paragraph("Line after the photo.")
    doc.save(str(docx_path))
    return str(docx_path)


def _image_blocks(doc_state):
    return [b for b in doc_state.blocks if b.get("images")]


def test_photos_extract_to_stable_folder(photo_docx):
    first = _image_blocks(parse_docx_file(photo_docx))
    assert len(first) == 1 and len(first[0]["images"]) == 1
    img_path = first[0]["images"][0]

    photos_dir = os.path.join(parser.PHOTOS_TEMP_DIR, "Photo Script")
    assert os.path.dirname(img_path) == os.path.abspath(photos_dir)
    assert os.path.basename(img_path).startswith("Photo Script_")
    assert os.path.isfile(img_path)

    # Re-parsing reuses the same file: no new copies, same path for the NLE to link to.
    again = _image_blocks(parse_docx_file(photo_docx))
    assert again[0]["images"] == [img_path]
    assert len(os.listdir(photos_dir)) == 1


def test_raw_html_renders_photo_inline(photo_docx):
    doc_state = parse_docx_file(photo_docx)
    block = _image_blocks(doc_state)[0]
    out = build_raw_html(doc_state.blocks, {})
    assert out.count("<img ") == 1
    assert f"width='{parser.SCRIPT_IMAGE_MAX_WIDTH}'" in out
    # One <p>/<h*> per block, so the viewer's block-to-paragraph mapping still lines up.
    assert out.count("id='block-") == len(doc_state.blocks)

    # A photo whose file is gone renders nothing rather than a broken-image icon.
    missing = dict(block, images=[block["images"][0] + ".gone"])
    assert "<img" not in build_raw_html([missing], {})


@pytest.mark.parametrize("view_mode", ["reading", "document"])
def test_viewer_shows_photo_and_drags_it_as_file(photo_docx, qapp, view_mode):
    doc_state = parse_docx_file(photo_docx)
    img_path = _image_blocks(doc_state)[0]["images"][0]

    viewer = ScriptViewer()
    viewer.resize(1100, 900)
    viewer.show()
    viewer.load_document_state(doc_state)
    viewer.set_view_mode(view_mode)
    qapp.processEvents()
    browser = viewer.browser
    doc = browser.document()
    assert doc.blockCount() == len(doc_state.blocks)

    # Locate the image character and the centre of its rect in document coordinates.
    pos = doc.toPlainText().index("￼")
    block = doc.findBlock(pos)
    line = block.layout().lineForTextPosition(pos - block.position())
    block_rect = doc.documentLayout().blockBoundingRect(block)
    x0, _ = line.cursorToX(pos - block.position())
    x1, _ = line.cursorToX(pos - block.position() + 1)
    doc_pt = QPointF(block_rect.left() + (x0 + x1) / 2, block_rect.top() + line.y() + line.height() / 2)
    assert x1 - x0 == pytest.approx(parser.SCRIPT_IMAGE_MAX_WIDTH, abs=1)
    name = doc.documentLayout().imageAt(doc_pt)
    assert name, "image did not lay out where expected"

    if view_mode == "reading":
        vp_pt = QPointF(
            doc_pt.x() - browser.horizontalScrollBar().value(),
            doc_pt.y() - browser.verticalScrollBar().value(),
        )
        assert browser._image_file_at(vp_pt) == img_path
        assert browser._image_file_at(QPointF(vp_pt.x(), 4)) == ""
    else:
        # Scan the viewport for the photo: page mapping is the viewer's own business.
        hits = {
            browser._image_file_at(QPointF(x, y))
            for x in range(0, browser.viewport().width(), 12)
            for y in range(0, browser.viewport().height(), 12)
        }
        assert hits == {"", img_path}

    mime = browser._image_drag_mime(img_path)
    assert [u.toLocalFile() for u in mime.urls()] == [img_path.replace("\\", "/")]

    # The loaded image is the real pixels, not Qt's broken-image placeholder.
    cursor = QTextCursor(doc)
    cursor.setPosition(pos + 1)
    fmt = cursor.charFormat().toImageFormat()
    assert fmt.isValid() and fmt.width() == parser.SCRIPT_IMAGE_MAX_WIDTH
    grab = browser.viewport().grab().toImage()
    reds = sum(
        1
        for x in range(0, grab.width(), 6)
        for y in range(0, grab.height(), 6)
        if QColor(grab.pixel(x, y)).red() > 180 and QColor(grab.pixel(x, y)).green() < 90
    )
    assert reds > 200, "photo pixels not painted"
    viewer.close()


def test_edit_roundtrip_keeps_photo_and_clean_text(photo_docx, qapp):
    doc_state = parse_docx_file(photo_docx)
    img_path = _image_blocks(doc_state)[0]["images"][0]

    viewer = ScriptViewer()
    viewer.resize(1100, 900)
    viewer.show()
    viewer.load_document_state(doc_state)
    viewer.set_edit_mode(True)
    qapp.processEvents()
    blocks = viewer.extract_edited_blocks()
    assert all("￼" not in b["text"] for b in blocks)
    assert all("￼" not in s["text"] for b in blocks for s in b["spans"])
    assert [b["images"] for b in blocks if b["images"]] == [[img_path]]
    viewer.close()


# --- Photos bound to script links through footage linking ---

PHOTO_LINK = "https://drive.google.com/file/d/1B2C3D4E5F6G7H8I9J/view?usp=sharing"
VIDEO_LINK = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"


def _span(text, link=None):
    return {"text": text, "color": None, "bold": False, "italic": False, "size": None, "link": link}


def _linked_blocks():
    def block(i, spans):
        return {
            "id": i, "text": "".join(s["text"] for s in spans), "spans": spans, "images": [],
            "isEditorLine": False, "isHeader": False, "headerLevel": None,
        }
    return [
        block(0, [_span("Plain intro line.")]),
        block(1, [_span("Show the mugshot "), _span("here", PHOTO_LINK), _span(" and again "), _span("here", PHOTO_LINK)]),
        block(2, [_span("Then the clip "), _span("clip", VIDEO_LINK)]),
    ]


@pytest.fixture()
def footage(tmp_path, qapp):
    folder = tmp_path / "Footage Workspace"
    folder.mkdir()
    photo = folder / "mugshot.jpg"
    img = QImage(3000, 2000, QImage.Format_RGB32)
    img.fill(QColor("#cc3344"))
    assert img.save(str(photo))
    video = folder / "clip.mp4"
    video.write_bytes(b"not really a video")
    # Bindings are keyed by normalized link, like the footage relink dialog saves them.
    linked = {parser.normalize_link(PHOTO_LINK): str(photo), parser.normalize_link(VIDEO_LINK): str(video)}
    return str(photo), linked


def test_linked_photo_renders_inline_from_footage_folder(footage):
    photo, linked = footage
    blocks = _linked_blocks()
    assert "<img" not in build_raw_html(blocks, {})

    out = build_raw_html(blocks, {}, linked_footage=linked)
    # Once per block even though the link appears twice; the bound video is not rendered.
    assert out.count("<img ") == 1
    assert "mugshot.jpg" in out and "clip.mp4" not in out
    assert out.count("id='block-") == len(blocks)


def test_viewer_drags_linked_photo_from_its_footage_path(footage, qapp):
    from types import SimpleNamespace
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument

    photo, linked = footage
    viewer = ScriptViewer()
    viewer.resize(1100, 900)
    viewer.show()
    viewer.load_document_state(SimpleNamespace(blocks=_linked_blocks(), raw_html=""))
    qapp.processEvents()
    browser = viewer.browser
    doc = browser.document()
    assert "￼" not in doc.toPlainText()

    # Binding footage after the script is open re-renders with the photo.
    viewer.set_linked_footage(linked)
    qapp.processEvents()
    assert doc.toPlainText().count("￼") == 1
    assert doc.blockCount() == 3

    hits = {
        browser._image_file_at(QPointF(x, y))
        for x in range(0, browser.viewport().width(), 12)
        for y in range(0, browser.viewport().height(), 12)
    }
    # The draggable file is the original in the footage folder, not a copy.
    assert hits == {"", os.path.abspath(photo)}

    # The 3000px original is handed to the document downscaled.
    res = doc.resource(QTextDocument.ImageResource, QUrl.fromLocalFile(photo))
    assert not res.isNull() and res.width() == 720 and res.height() == 480

    viewer.set_linked_footage({})
    qapp.processEvents()
    assert "￼" not in doc.toPlainText()
    viewer.close()


# --- Auto / manual censoring of script photos ---

GORE_LINK = "https://drive.google.com/file/d/9Z8Y7X6W5V4U3T2S1R/view?usp=sharing"


@pytest.fixture()
def censor(qapp):
    """The shared censor service with a stand-in scorer: files named *gore* are sensitive."""
    from src.core.photo_censor import photo_censor
    from src.core.database import db

    def reset():
        photo_censor._pool.waitForDone()
        qapp.processEvents()
        photo_censor._load()
        with db._get_connection() as conn:
            conn.execute("DELETE FROM photo_sensitivity")
            conn.commit()
        photo_censor._records = None
        for bucket in (photo_censor._pending, photo_censor._failed, photo_censor._revealed, photo_censor._force_hidden):
            bucket.clear()
        db.set_setting("photo_censor_auto", "1")

    reset()
    photo_censor.set_scorer(lambda p: 0.93 if "gore" in os.path.basename(p) else 0.04)
    yield photo_censor
    photo_censor.set_scorer(None)
    reset()


def _settle(censor, qapp):
    censor._pool.waitForDone()
    qapp.processEvents()


@pytest.fixture()
def censor_viewer(tmp_path, qapp, censor):
    from types import SimpleNamespace

    folder = tmp_path / "Footage"
    folder.mkdir()
    paths = {}
    for name, colour in (("mugshot.png", "#2255cc"), ("gore.png", "#cc3344")):
        img = QImage(900, 600, QImage.Format_RGB32)
        img.fill(QColor(colour))
        assert img.save(str(folder / name))
        paths[name.split(".")[0]] = os.path.abspath(str(folder / name))

    def block(i, text, link):
        spans = [_span(text + " "), _span("link", link)]
        return {"id": i, "text": text + " link", "spans": spans, "images": [],
                "isEditorLine": False, "isHeader": False, "headerLevel": None}

    blocks = [block(0, "The suspect", PHOTO_LINK), block(1, "The scene", GORE_LINK)]
    linked = {parser.normalize_link(PHOTO_LINK): paths["mugshot"], parser.normalize_link(GORE_LINK): paths["gore"]}

    viewer = ScriptViewer()
    viewer.resize(1100, 1100)
    viewer.show()
    viewer.set_linked_footage(linked, refresh=False)
    viewer.load_document_state(SimpleNamespace(blocks=blocks, raw_html=""))
    qapp.processEvents()
    yield viewer, paths
    viewer.close()


def _shown_colour(viewer, path):
    """Centre pixel of the photo as the document currently holds it."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument
    img = viewer.browser.document().resource(QTextDocument.ImageResource, QUrl.fromLocalFile(path))
    return QColor(img.pixel(img.width() // 2, int(img.height() * 0.8)))


def _is_blurred(viewer, path):
    c = _shown_colour(viewer, path)
    return max(c.red(), c.green(), c.blue()) < 110   # darkened by the censor overlay


def _find_spot(viewer, path, want_eye):
    vp = viewer.browser.viewport()
    for y in range(0, vp.height(), 4):
        for x in range(0, vp.width(), 4):
            if viewer.browser._photo_hit(QPointF(x, y)) == (path, want_eye):
                return QPoint(x + 2, y + 2)
    raise AssertionError("photo spot not found on screen")


def test_auto_censor_blurs_only_flagged_photo(censor_viewer, censor, qapp):
    viewer, paths = censor_viewer
    _settle(censor, qapp)
    assert _is_blurred(viewer, paths["gore"])
    assert not _is_blurred(viewer, paths["mugshot"])
    assert _shown_colour(viewer, paths["mugshot"]).blue() > 180
    assert censor.auto_score(paths["gore"]) == pytest.approx(0.93)

    # The blurred preview is what actually gets painted.
    grab = viewer.browser.viewport().grab().toImage()
    vivid_red = sum(
        1 for x in range(0, grab.width(), 6) for y in range(0, grab.height(), 6)
        if QColor(grab.pixel(x, y)).red() > 180 and QColor(grab.pixel(x, y)).green() < 90
    )
    assert vivid_red == 0

    # Turning auto-censor off shows everything; nothing was manually censored.
    censor.set_auto_enabled(False)
    qapp.processEvents()
    assert not _is_blurred(viewer, paths["gore"])


def test_eye_and_click_toggle_censor(censor_viewer, censor, qapp):
    from PySide6.QtTest import QTest
    viewer, paths = censor_viewer
    _settle(censor, qapp)
    vp = viewer.browser.viewport()

    # Clicking the blurred photo reveals it; the dragged file is still the original.
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, _find_spot(viewer, paths["gore"], False))
    assert not _is_blurred(viewer, paths["gore"])
    assert viewer.browser._image_file_at(QPointF(_find_spot(viewer, paths["gore"], False))) == paths["gore"]

    # The corner eye hides it again, and can hide a photo the model passed as safe.
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, _find_spot(viewer, paths["gore"], True))
    assert _is_blurred(viewer, paths["gore"])
    eye = _find_spot(viewer, paths["mugshot"], True)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, eye)
    assert _is_blurred(viewer, paths["mugshot"])
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, eye)
    assert not _is_blurred(viewer, paths["mugshot"])


def test_manual_censor_overrides_model_and_persists(censor_viewer, censor, qapp):
    from src.core.photo_censor import PhotoCensorService, MANUAL_CENSOR, MANUAL_SAFE
    viewer, paths = censor_viewer
    _settle(censor, qapp)

    censor.set_manual(paths["mugshot"], MANUAL_CENSOR)   # the model missed it
    censor.set_manual(paths["gore"], MANUAL_SAFE)        # the model over-reacted
    qapp.processEvents()
    assert _is_blurred(viewer, paths["mugshot"])
    assert not _is_blurred(viewer, paths["gore"])

    # Manual censor holds even with the model switched off, and survives a restart.
    censor.set_auto_enabled(False)
    qapp.processEvents()
    assert _is_blurred(viewer, paths["mugshot"])
    fresh = PhotoCensorService()
    assert fresh.manual(paths["mugshot"]) == MANUAL_CENSOR
    assert fresh.manual(paths["gore"]) == MANUAL_SAFE

    censor.set_auto_enabled(True)
    censor.set_manual(paths["mugshot"], None)
    qapp.processEvents()
    assert not _is_blurred(viewer, paths["mugshot"])


def test_without_model_nothing_is_auto_censored(censor_viewer, censor, qapp):
    viewer, paths = censor_viewer
    _settle(censor, qapp)
    censor.set_scorer(None)                     # test model dir is empty -> model not ready
    from src.core.database import db
    with db._get_connection() as conn:          # and no verdicts on file from an earlier check
        conn.execute("DELETE FROM photo_sensitivity")
        conn.commit()
    censor._records = None
    assert not censor.model_ready()
    viewer._update_display()
    qapp.processEvents()
    assert not _is_blurred(viewer, paths["gore"])


def test_unchecked_photo_stays_covered_until_scored(tmp_path, censor, qapp):
    import threading
    photo = tmp_path / "unknown.png"
    img = QImage(300, 200, QImage.Format_RGB32)
    img.fill(QColor("#2255cc"))
    assert img.save(str(photo))

    gate = threading.Event()
    censor.set_scorer(lambda p: (gate.wait(10), 0.04)[1])
    changed = []
    censor.photo_changed.connect(changed.append)
    try:
        assert censor.is_hidden(str(photo)) and censor.is_pending(str(photo))
        gate.set()
        _settle(censor, qapp)
        assert changed == [str(photo)]
        assert not censor.is_hidden(str(photo)) and not censor.is_pending(str(photo))
    finally:
        gate.set()
        censor.photo_changed.disconnect(changed.append)


def test_photo_right_click_menu_manual_censor(censor_viewer, censor, qapp):
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from src.core.photo_censor import MANUAL_CENSOR
    viewer, paths = censor_viewer
    _settle(censor, qapp)
    assert not _is_blurred(viewer, paths["mugshot"])

    seen = []

    def drive_menu():
        popup = QApplication.activePopupWidget()
        if popup is None:
            return
        seen.extend(a.text() for a in popup.actions() if not a.isSeparator())
        QTest.keyClick(popup, Qt.Key_Down)      # Hide Photo
        QTest.keyClick(popup, Qt.Key_Down)      # Always Censor This Photo
        QTest.keyClick(popup, Qt.Key_Return)

    def bail_out():
        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.close()

    QTimer.singleShot(300, drive_menu)
    QTimer.singleShot(4000, bail_out)
    spot = _find_spot(viewer, paths["mugshot"], False)
    viewer._show_context_menu(spot)             # right-click on the photo
    qapp.processEvents()

    assert seen[:6] == [
        "Hide Photo", "Always Censor This Photo", "Never Censor This Photo",
        "Reset to Automatic", "Re-check Photo", "Copy File Path",
    ]
    assert any(t.startswith("Download Auto-Censor Model") for t in seen) is False   # stand-in scorer = ready
    assert censor.manual(paths["mugshot"]) == MANUAL_CENSOR
    assert _is_blurred(viewer, paths["mugshot"])
