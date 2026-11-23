"""Light-mode and first-boot fixes: default theme, readable headers and tooltips, Editor Notes buttons, dark clip player."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src.core.models import EditorNote
from src.ui.theme import PALETTES, ThemeManager, ThemeVariant


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def restore_theme():
    v, d = ThemeManager.current_variant, ThemeManager.is_dark
    yield
    ThemeManager.current_variant, ThemeManager.is_dark = v, d


def test_adobe_gray_is_the_default_on_first_boot():
    assert ThemeManager.current_variant == ThemeVariant.GRAY
    src = open(os.path.join("src", "ui", "window.py"), encoding="utf-8").read()
    assert 'get_setting("theme_variant", "gray")' in src


@pytest.mark.parametrize("dark", [True, False])
def test_tooltips_are_readable_in_every_mode(dark):
    qss = ThemeManager.apply_theme(ThemeVariant.GRAY, dark)
    block = qss[qss.index("QToolTip"):].split("}", 1)[0]
    pal = ThemeManager.get_palette()
    assert pal["text"] in block and pal["surface"] in block and pal["text"] != pal["surface"]


def test_settings_headers_follow_the_mode_instead_of_freezing_a_colour(qapp):
    from src.ui.components.preferences_dialog import PreferencesDialog
    ThemeManager.apply_theme(ThemeVariant.GRAY, True)
    dlg = PreferencesDialog()
    from PySide6.QtWidgets import QLabel
    for lbl in dlg.findChildren(QLabel):
        if lbl.objectName() in ("PrefTabTitle", "PrefTabSubtitle"):
            assert "color:" not in lbl.styleSheet()      # an inline colour would stay white after switching to light


def _drawer(qapp):
    from src.ui.components.drawers import NotesDrawer
    d = NotesDrawer()
    return d


def test_only_notes_the_editor_typed_can_be_deleted(qapp):
    d = _drawer(qapp)
    mine, script = EditorNote(id="m", block_id=0, text="mine"), EditorNote(id="s", block_id=3, text="from script")
    d.set_custom_ids(["m"])
    d.load_notes([script, mine])
    by_id = {c.property("note_id"): c for c in d.note_cards}
    assert by_id["m"].property("delete_btn") is not None
    assert by_id["s"].property("delete_btn") is None


def test_delete_button_asks_for_that_note_to_be_deleted(qapp):
    d = _drawer(qapp)
    d.set_custom_ids(["m"])
    d.load_notes([EditorNote(id="m", block_id=0, text="mine")])
    got = []
    d.note_deleted.connect(got.append)
    d.note_cards[0].property("delete_btn").click()
    assert got == ["m"]


def test_add_note_button_has_no_icon_to_clip_the_label(qapp):
    d = _drawer(qapp)
    assert d.add_btn.icon().isNull() and d.add_btn.text() == "Add Note"


def test_workspace_deletes_a_custom_note_and_keeps_script_notes(qapp):
    from src.ui.views.workspace_view import WorkspaceView
    ws = WorkspaceView.__new__(WorkspaceView)

    class Doc:
        notes = [EditorNote(id="s", block_id=1, text="script")]
        custom_notes = [EditorNote(id="m", block_id=0, text="mine")]
        checked_notes = {"m": True}
    ws.current_doc = Doc()
    saved, loaded = [], []
    ws.schedule_save = lambda: saved.append(1)

    class Drawer:
        def set_custom_ids(self, ids): loaded.append(("ids", list(ids)))
        def load_notes(self, notes, checked=None): loaded.append(("n", [n.id for n in notes]))
    ws.notes_drawer = Drawer()

    class Sig:
        def emit(self, v): loaded.append(("count", v))
    ws.notes_count_changed = Sig()
    ws._on_delete_custom_note("s")                 # script notes are not deletable
    assert Doc.custom_notes[0].id == "m" and not saved
    ws._on_delete_custom_note("m")
    assert ws.current_doc.custom_notes == [] and "m" not in ws.current_doc.checked_notes and saved
    assert ("n", ["s"]) in loaded and ("count", 1) in loaded


@pytest.mark.parametrize("dark", [True, False])
def test_clip_player_stays_dark_in_light_mode(qapp, dark):
    from src.ui.components.aroll_trimmer_window import ARollTrimmerWindow
    ThemeManager.apply_theme(ThemeVariant.GRAY, dark)
    w = ARollTrimmerWindow()
    assert "#111114" in w.styleSheet() and "#f8f9fa" not in w.styleSheet()
    assert not w.windowIcon().isNull()


@pytest.mark.parametrize("dark", [True, False])
def test_button_hover_tips_contrast_in_every_mode(qapp, dark):
    from qfluentwidgets.components.widgets.tool_tip import ToolTip
    ThemeManager.apply_theme(ThemeVariant.GRAY, dark)
    tip = ToolTip("Play clip on loop")
    tip.show()
    qapp.processEvents()
    pal = ThemeManager.get_palette()
    css = tip.styleSheet()
    assert pal["surface"] in css and pal["text"] in css
    tip.hide()


@pytest.mark.parametrize("dark", [True, False])
def test_rainy_draws_its_own_hover_tips_from_the_theme(qapp, dark):
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QPushButton
    from src.ui import tooltips
    ThemeManager.apply_theme(ThemeVariant.GRAY, dark)
    f = tooltips.TipFilter(qapp)
    btn = QPushButton("x")
    btn.setToolTip("Play clip on loop")
    ev = QHelpEvent(QEvent.ToolTip, QPoint(2, 2), QPoint(20, 20))
    assert f.eventFilter(btn, ev) is True and f.bubble.isVisible()
    bg, fg, _ = f.bubble.colors()
    assert bg.name() == ThemeManager.get_palette()["surface"] and fg.name() == ThemeManager.get_palette()["text"]
    assert bg.name() != fg.name()
    f.eventFilter(btn, QEvent(QEvent.Leave))
    assert not f.bubble.isVisible()
    plain = QPushButton("y")
    assert f.eventFilter(plain, ev) is False                       # nothing to show: Qt handles it as before
