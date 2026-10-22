import os
import sys
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ui.window import MainWindow
from src.ui.components.timeline_card import TimelineCard
from src.core.parser import parse_docx_file
from src.core.database import db

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app

def test_mainwindow_initialization(qapp):
    window = MainWindow()
    assert window is not None
    assert "KNOT TOOLS" in window.windowTitle()
    assert window.title_branding is not None
    assert window.title_stats is not None
    assert window.sidebar is not None
    window.close()

def test_ui_view_switching(qapp):
    window = MainWindow()
    
    # Check default home view
    window.go_home()
    assert window.stack.currentWidget() == window.home_view
    assert window.sidebar.isHidden()

    # Switch to workspace
    window.open_workspace()
    assert window.stack.currentWidget() == window.workspace_view
    assert not window.sidebar.isHidden()

    window.close()

def test_split_and_drawer_toggles(qapp):
    window = MainWindow()
    window.open_workspace()

    # Test Focus / Split view toggle
    assert not window.workspace_view._is_focus_mode
    window.workspace_view.toggle_split_view()
    assert window.workspace_view._is_focus_mode
    assert window.workspace_view.timeline_list.isHidden()
    window.workspace_view.toggle_split_view()
    assert not window.workspace_view._is_focus_mode
    assert not window.workspace_view.timeline_list.isHidden()

    # Test Outline Panel toggle
    assert window.workspace_view.outline_panel.isHidden()
    window.workspace_view.toggle_outline()
    assert not window.workspace_view.outline_panel.isHidden()
    window.workspace_view.toggle_outline()
    assert window.workspace_view.outline_panel.isHidden()

    # Test Notes Drawer toggle
    assert window.workspace_view.notes_drawer.isHidden()
    window.workspace_view.toggle_notes_drawer()
    assert not window.workspace_view.notes_drawer.isHidden()
    window.workspace_view.toggle_notes_drawer()
    assert window.workspace_view.notes_drawer.isHidden()

    # Test Links Drawer toggle
    assert window.workspace_view.links_drawer.isHidden()
    window.workspace_view.toggle_links_drawer()
    assert not window.workspace_view.links_drawer.isHidden()
    window.workspace_view.toggle_links_drawer()
    assert window.workspace_view.links_drawer.isHidden()

    window.close()

def test_document_load_and_stats_parity(qapp):
    sample_docx = os.path.abspath(r"d:\Codes\knot-parser\test_docs\Every Time Family Guy Felt Like Another Show.docx")
    assert os.path.exists(sample_docx)

    doc_state = parse_docx_file(sample_docx)
    assert len(doc_state.items) == 165

    window = MainWindow()
    window.workspace_view.load_document(doc_state)

    # Check that cards were populated
    assert len(window.workspace_view.timeline_list.card_list) == 165
    assert window.workspace_view.script_viewer.title_label.text() == doc_state.name
    assert hasattr(window.workspace_view.script_viewer, "middle_scroller")
    assert hasattr(window.workspace_view.timeline_list, "middle_scroller")

    # Check stats label contains A-Roll and total count
    assert "A-Roll" in window.title_stats.a_roll_pill.text()
    assert "165" in window.title_stats.a_roll_pill.text()

    # Check checking off an item updates stats
    first_card = window.workspace_view.timeline_list.card_list[0]
    first_card.set_checked(True)
    assert "165" in window.title_stats.a_roll_pill.text()

    # Check top notification suppression per Cane's instruction
    window.title_stats.trigger_copied("00:01:23:00")
    assert window.title_stats.copied_toast.isHidden()

    # Test font size zoom scaling
    sv = window.workspace_view.script_viewer
    sv.zoom_slider.setValue(20)
    assert sv.current_font_size == 20
    assert sv.browser.font().pointSize() == 20

    # Test dropdown filter (All | Done | Pending)
    tl = window.workspace_view.timeline_list
    assert tl.filter_combo.count() == 3
    assert tl.filter_combo.currentText() == "All"
    assert tl.filter_combo.itemText(2) == "Pending"

    # Filter by Done (first_card is checked, index 2 is unchecked)
    tl.filter_combo.setCurrentText("Done")
    assert not first_card.isHidden()
    unchecked_card = tl.card_list[2]
    assert unchecked_card.isHidden()

    # Filter by Pending
    tl.filter_combo.setCurrentText("Pending")
    assert first_card.isHidden()
    assert not unchecked_card.isHidden()

    # Reset to All
    tl.filter_combo.setCurrentText("All")
    assert not first_card.isHidden()
    assert not unchecked_card.isHidden()

    window.close()

def test_preferences_dialog_modal(qapp):
    from src.ui.components.preferences_dialog import PreferencesDialog
    from src.ui.theme import ThemeVariant, ThemeManager

    window = MainWindow()
    
    # Test instantiating PreferencesDialog
    dlg = PreferencesDialog(window, initial_tab=0)
    assert dlg is not None
    assert dlg.nav_list.count() == 5
    assert dlg.stack.count() == 5

    # Test tab switching
    dlg.nav_list.setCurrentRow(1)
    assert dlg.stack.currentIndex() == 1

    dlg.nav_list.setCurrentRow(4)
    assert dlg.stack.currentIndex() == 4

    # Test theme signal emission
    themes_received = []
    dlg.theme_changed.connect(lambda v, d: themes_received.append((v, d)))
    dlg.theme_changed.emit(ThemeVariant.PINK, True)
    assert len(themes_received) == 1
    assert themes_received[0] == (ThemeVariant.PINK, True)

    # Ensure Consumer Glass theme is active for orb slider testing
    dlg._on_theme_toggled(True, ThemeVariant.PINK)

    # Test Ambient Mica Orb Glow slider, magnetic snap zones, presets, and SQLite persistence
    assert hasattr(dlg, 'orb_slider')
    dlg._on_orb_slider_changed(24)  # Within ±2% magnetic snap zone of Subtle (25%)
    assert dlg.orb_slider.value() == 25
    assert ThemeManager.orb_glow_intensity == 25
    assert db.get_setting("orb_glow_intensity") == "25"
    assert "Subtle" in dlg.orb_value_badge.text()

    dlg._on_orb_slider_changed(42)  # Free-sliding custom value outside snap zones
    assert dlg.orb_slider.value() == 42
    assert ThemeManager.orb_glow_intensity == 42
    assert "Custom" in dlg.orb_value_badge.text()

    dlg._set_orb_preset(55)  # Middle preset
    assert dlg.orb_slider.value() == 55
    assert ThemeManager.orb_glow_intensity == 55
    assert "Middle" in dlg.orb_value_badge.text()

    # Restore default Subtle (25%)
    dlg._set_orb_preset(25)

    dlg.close()
    window.close()

def test_notes_and_links_drawer_checklist(qapp):
    sample_docx = os.path.abspath(r"d:\Codes\knot-parser\test_docs\Every Time Family Guy Felt Like Another Show.docx")
    doc_state = parse_docx_file(sample_docx)

    window = MainWindow()
    window.workspace_view.load_document(doc_state)

    notes_drawer = window.workspace_view.notes_drawer
    assert len(notes_drawer.notes) == len(doc_state.notes)

    # Check first note
    if notes_drawer.note_cards:
        first_note_card = notes_drawer.note_cards[0]
        cb = first_note_card.property("checkbox")
        cb.setChecked(True)
        assert doc_state.checked_notes.get(notes_drawer.notes[0].id) is True

    # Test Notes Drawer dropdown filter and toggle all button
    assert notes_drawer.filter_combo.count() == 3
    assert notes_drawer.filter_combo.currentText() == "All"
    # Since the only note was checked above, it should now say "Mark all as not done"
    assert notes_drawer.toggle_all_btn.text() == "Mark all as not done"

    # Click toggle all -> uncheck all
    notes_drawer.toggle_all_btn.click()
    assert notes_drawer.toggle_all_btn.text() == "Mark all as done"
    assert not any(card.property("is_checked") for card in notes_drawer.note_cards)

    # Click toggle all again -> check all
    notes_drawer.toggle_all_btn.click()
    assert notes_drawer.toggle_all_btn.text() == "Mark all as not done"
    assert all(card.property("is_checked") for card in notes_drawer.note_cards)

    # Test links drawer checklist
    links_drawer = window.workspace_view.links_drawer
    if links_drawer.link_cards:
        first_link_card = links_drawer.link_cards[0]
        cb = first_link_card.property("checkbox")
        cb.setChecked(True)
        assert doc_state.checked_links.get(links_drawer.links[0]) is True

    assert links_drawer.filter_combo.count() == 3
    # If not all links are checked yet, it should say "Mark all as done"
    if len(links_drawer.links) > 1:
        assert links_drawer.toggle_all_btn.text() == "Mark all as done"
        links_drawer.toggle_all_btn.click()
        assert links_drawer.toggle_all_btn.text() == "Mark all as not done"
    else:
        assert links_drawer.toggle_all_btn.text() == "Mark all as not done"
    links_drawer.toggle_all_btn.click()
    assert links_drawer.toggle_all_btn.text() == "Mark all as done"

    window.close()

def test_timeline_card_responsive_domain(qapp):
    sample_docx = os.path.abspath(r"d:\Codes\knot-parser\test_docs\Every Time Family Guy Felt Like Another Show.docx")
    doc_state = parse_docx_file(sample_docx)

    card = TimelineCard(doc_state.items[0], doc_state.file_signature)
    card.show()
    card.resize(650, 44)
    qapp.processEvents()

    # Verify micro-spacing parity
    assert card.cb.width() == 18
    assert card.cb.height() == 18
    assert card.meta_pill.height() == 22
    assert card.tc_container.height() == 32
    assert card.tc_container.width() == 248
    assert card.index_lbl.geometry().x() - (card.cb.geometry().x() + card.cb.geometry().width()) == 6

    if hasattr(card, 'domain_lbl') and card.domain_lbl:
        assert not card.domain_lbl.isHidden()

    card.resize(500, 44)
    qapp.processEvents()
    if hasattr(card, 'domain_lbl') and card.domain_lbl:
        assert card.domain_lbl.isHidden()

    # Verify compact squashed mode when width < 420
    card.resize(360, 50)
    qapp.processEvents()
    assert card.is_compact is True
    assert card.index_lbl.isHidden()
    assert card.tc_container.width() == 130
    assert card.tc_container.height() == 40
    assert card.height() == 50
    assert card.in_lbl.isVisible()
    assert card.out_lbl.isVisible()
    assert card.dur_lbl.isHidden()

    # Verify restoration to normal wide mode when widened back
    card.resize(650, 44)
    qapp.processEvents()
    assert card.is_compact is False
    assert not card.index_lbl.isHidden()
    assert card.tc_container.width() == 248
    assert card.tc_container.height() == 32
    assert card.height() == 44
    assert not card.dur_lbl.isHidden()
    assert card.in_lbl.isHidden()
    assert card.out_lbl.isHidden()

    card.close()

def test_independent_windows_and_non_modal_spawning(qapp):
    window = MainWindow()
    window.open_workspace()

    # Verify Notes and Links are independent top-level windows
    notes_win = window.workspace_view.notes_drawer
    links_win = window.workspace_view.links_drawer
    assert notes_win.isWindow()
    assert links_win.isWindow()
    assert "Editor Notes" in notes_win.windowTitle()
    assert "Source Links" in links_win.windowTitle()

    # Verify that closing one drawer does not close the other
    notes_win.show()
    links_win.show()
    assert not notes_win.isHidden()
    assert not links_win.isHidden()
    notes_win.close()
    assert notes_win.isHidden()
    assert not links_win.isHidden()
    links_win.close()
    assert links_win.isHidden()

    # Verify Preferences is independent window
    window.open_settings(initial_tab=0)
    assert hasattr(window, 'pref_window')
    assert window.pref_window.isWindow()
    assert "Preferences" in window.pref_window.windowTitle()
    assert not window.pref_window.isHidden()

    # Verify Data Vault independent window and tab focus
    window.open_vault()
    assert hasattr(window, 'vault_window')
    assert window.vault_window.isWindow()
    assert "Data Vault" in window.vault_window.windowTitle()
    assert not window.vault_window.isHidden()
    assert window.pref_window.nav_list.currentRow() == 1
    window.vault_window.close()
    window.pref_window.close()

    # Verify About is independent window
    window.open_about()
    assert hasattr(window, 'about_window')
    assert window.about_window.isWindow()
    assert "About" in window.about_window.windowTitle()
    assert not window.about_window.isHidden()
    window.about_window.close()

    window.close()

def test_timestamp_click_and_graphic_highlight(qapp):
    sample_docx = os.path.abspath(r"d:\Codes\knot-parser\test_docs\Every Time Family Guy Felt Like Another Show.docx")
    doc_state = parse_docx_file(sample_docx)

    window = MainWindow()
    window.workspace_view.load_document(doc_state)

    sv = window.workspace_view.script_viewer
    tl = window.workspace_view.timeline_list
    assert len(tl.card_list) > 0

    first_card = tl.card_list[0]
    
    # Test clicking card triggers sync_script_requested and highlights script
    sync_called = []
    first_card.sync_script_requested.connect(lambda sid, meta: sync_called.append((sid, meta)))
    
    # Simulate mouse press on card
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF, QEvent
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(10, 10), QPointF(10, 10), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    first_card.mousePressEvent(ev)

    assert len(sync_called) == 1
    assert sync_called[0][0] == first_card.item.sync_id

    # Test scroll_to_sync pulse highlight
    sv.scroll_to_sync(first_card.item.sync_id, first_card.item.meta)
    selections = sv.browser.extraSelections()
    assert len(selections) > 0
    # Yellow pulse color check
    color = selections[0].format.background().color()
    assert color.red() == 251 and color.green() == 191 and color.blue() == 36

    # Verify that clicking a timestamp link does NOT erase the document text in the script viewer
    initial_text_len = len(sv.browser.toPlainText())
    assert initial_text_len > 0
    from PySide6.QtCore import QUrl
    sv._on_anchor_clicked(QUrl("time:00:03:28:00"))
    assert len(sv.browser.toPlainText()) == initial_text_len
    assert sv.browser.openLinks() is False

    window.close()

def test_light_mode_and_theme_parity(qapp):
    from src.ui.theme import ThemeManager, ThemeVariant

    # Dark mode
    qss_dark = ThemeManager.apply_theme(ThemeVariant.PINK, dark=True)
    assert "QScrollBar:vertical" in qss_dark
    assert "border: none" in qss_dark
    assert "QSplitter::handle:horizontal" in qss_dark
    assert "QFrame#PrefSidebar QListWidget" in qss_dark
    assert "QTableWidget" in qss_dark
    assert "QFrame#NoteCard" in qss_dark

    # Light mode
    qss_light = ThemeManager.apply_theme(ThemeVariant.PINK, dark=False)
    assert "QScrollBar:vertical" in qss_light
    assert "#faf5f6" in qss_light or "#ffffff" in qss_light
    pal = ThemeManager.get_palette()
    assert not pal["is_dark"]
    assert pal["text"] == "#1e1318"
    assert pal["header"] == "#d8c8ce"

    # Adobe Gray light mode
    ThemeManager.apply_theme(ThemeVariant.GRAY, dark=False)
    pal_gray = ThemeManager.get_palette()
    assert pal_gray["header"] == "#cfcfd6"

def test_home_layout_margins_and_fixed_heights(qapp):
    window = MainWindow()
    home = window.home_view

    # Verify Typewriter has fixed height (preventing massive empty gaps)
    assert home.typewriter.height() == 54
    assert home.splash_lbl.height() == 22

    # Verify bottom action cards exist
    assert hasattr(home, 'updates_card')
    assert hasattr(home, 'copyright_card')
    assert home.updates_card is not None
    assert home.copyright_card is not None

    # Verify top alignment
    alignment = home.layout().alignment()
    assert bool(alignment & Qt.AlignTop)

    # Verify workspace margins parity (uniform 8px outer and gutter margins, flush 0px on left dock and top)
    margins = window.workspace_view.layout().contentsMargins()
    assert margins.left() == 0
    assert margins.top() == 0
    assert margins.right() == 8
    assert margins.bottom() == 8

    window.close()

def test_fluent_smooth_scrolling_and_micro_interactions(qapp):
    from qfluentwidgets import SmoothScrollArea, FluentIcon
    from src.ui.components.sidebar import BadgedIconButton
    from PySide6.QtGui import QMouseEvent, QImage
    from PySide6.QtCore import QPointF, QEvent

    window = MainWindow()
    window.open_workspace()

    # 1. Verify Smooth Kinetic Scrolling on TimelineList and Home
    assert isinstance(window.workspace_view.timeline_list.scroll_area, SmoothScrollArea)
    assert isinstance(window.home_view.recents_card.scroll, SmoothScrollArea)
    assert isinstance(window.workspace_view.notes_drawer.scroll, SmoothScrollArea)
    assert isinstance(window.workspace_view.links_drawer.scroll, SmoothScrollArea)

    # 2. Verify BadgedIconButton click forwarding from badge
    btn = BadgedIconButton(FluentIcon.EDIT)
    btn.resize(42, 42)
    clicks = []
    btn.clicked.connect(lambda: clicks.append(1))
    btn.set_badge_count(166)
    assert not btn.badge.isHidden()
    # Ensure badge does not clip beyond widget bounds
    assert btn.badge.x() + btn.badge.width() <= btn.width()

    # Simulate mouse click on the badge itself
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    btn.badge.mousePressEvent(ev)
    assert len(clicks) == 1

    # 3. Verify TimelineCueCard native paintEvent rendering without CSS conflicts
    from src.ui.components.timeline_card import TimelineCueCard
    from src.core.models import ScriptItem
    item = ScriptItem(
        id='test-1', row_index=1, raw_timestamp='00:00:01:00',
        start_timecode='00:00:01:00', start_seconds=1.0,
        end_timecode='00:00:02:00', end_seconds=2.0,
        duration_seconds=1.0, duration_display='1s',
        text='test', is_checked=True
    )
    card = TimelineCueCard(item)
    card.resize(600, 44)
    img = QImage(600, 44, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    card.render(img)
    # Check green tint pixel in checked state
    center_col = img.pixelColor(300, 22)
    assert center_col.green() > center_col.red()  # Confirms emerald tint

    # Test highlight state with Pink theme
    from src.ui.theme import ThemeManager, ThemeVariant
    ThemeManager.apply_theme(ThemeVariant.PINK)
    card.set_highlighted(True)
    img.fill(Qt.transparent)
    card.render(img)
    hl_col = img.pixelColor(300, 22)
    assert hl_col.red() > hl_col.green()  # Theme accent highlight (red dominant for pink/accent)

    card.close()
    btn.close()
    window.close()

def test_gpu_smooth_scroll_on_timestamp_click(qapp):
    from src.ui.components.widgets import GpuSmoothScroller, GpuGlowSplitter, GpuGlowSplitterHandle
    from PySide6.QtGui import QImage
    sample_docx = os.path.abspath(r"d:\Codes\knot-parser\test_docs\Every Time Family Guy Felt Like Another Show.docx")
    doc_state = parse_docx_file(sample_docx)

    window = MainWindow()
    window.show()
    window.open_workspace()
    window.workspace_view.load_document(doc_state)
    qapp.processEvents()

    sv = window.workspace_view.script_viewer
    tl = window.workspace_view.timeline_list
    splitter = window.workspace_view.splitter

    assert isinstance(sv.smooth_scroller, GpuSmoothScroller)
    assert isinstance(tl.smooth_scroller, GpuSmoothScroller)
    assert isinstance(splitter, GpuGlowSplitter)
    handle = splitter.handle(1)
    assert isinstance(handle, GpuGlowSplitterHandle)

    # Verify middle glow handle renders both idle grip pill and hover glow beam
    handle.resize(12, 400)
    handle._hover_t = 1.0
    img = QImage(12, 400, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    handle.render(img)
    assert img.pixelColor(6, 200).alpha() > 150

    # 1. Clicking a timestamp in ScriptViewer smoothly animates TimelineList toward a later card
    later_card = tl.card_list[40]
    tl.scroll_to_timestamp(later_card.item.start_timecode)
    assert later_card.is_highlighted is True
    assert later_card._highlight_alpha > 0.0
    assert tl.smooth_scroller.target_value > 0

    # 2. Clicking a later card smoothly animates ScriptViewer toward that block without instant teleport
    sv.scroll_to_sync(later_card.item.sync_id, later_card.item.raw_timestamp or later_card.item.start_timecode)
    assert len(sv.browser.extraSelections()) > 0
    assert sv.smooth_scroller.target_value >= 0

    window.close()


def test_themes_detent_sliders_and_pro_solid_modes(qapp):
    from src.ui.theme import ThemeManager, ThemeVariant
    from src.ui.components.preferences_dialog import PreferencesWindow
    from src.ui.components.widgets import MagneticDetentSlider
    from src.core.database import db
    from PySide6.QtGui import QImage, QPainter

    ThemeManager.current_variant = ThemeVariant.PINK
    db.set_setting("theme_variant", "pink")
    ThemeManager.experimental_pure_dwm_glass = False
    db.set_setting("experimental_pure_dwm_glass", "false")
    pref = PreferencesWindow()
    pref._on_theme_toggled(True, ThemeVariant.PINK)
    pref.show()
    qapp.processEvents()

    # 1. Verify both sliders are MagneticDetentSlider instances (no legacy preset PushButtons)
    assert isinstance(pref.orb_slider, MagneticDetentSlider)
    assert isinstance(pref.glass_slider, MagneticDetentSlider)
    assert ThemeVariant.GREEN in pref.theme_radios
    assert hasattr(pref, "exp_dwm_switch")
    assert pref.exp_dwm_switch.isChecked() is False
    assert ThemeManager.experimental_pure_dwm_glass is False

    # Test Experimental Pure DWM See-Through Glass toggle
    pref._on_exp_dwm_glass_changed(True)
    assert ThemeManager.experimental_pure_dwm_glass is True
    assert db.get_setting("experimental_pure_dwm_glass") == "true"
    assert "Pure DWM" in pref.glass_value_badge.text()

    # Reset back to default Off
    pref._on_exp_dwm_glass_changed(False)
    assert ThemeManager.experimental_pure_dwm_glass is False
    assert db.get_setting("experimental_pure_dwm_glass") == "false"

    # 2. Test Matcha Green Consumer Glass Theme
    pref.theme_radios[ThemeVariant.GREEN].setChecked(True)
    qapp.processEvents()
    assert ThemeManager.current_variant == ThemeVariant.GREEN
    assert ThemeManager.supports_glass_effects() is True
    assert pref.orb_slider.isEnabled() is True
    assert pref.glass_slider.isEnabled() is True
    pal_green = ThemeManager.get_palette()
    assert pal_green["accent"] == "#7ec89b"
    assert len(pal_green["orbs"]) == 3

    # 3. Test MagneticDetentSlider magnetic snapping (±3%) and free-sliding
    pref.orb_slider.setValue(54, apply_snap=True)
    assert pref.orb_slider.value() == 55  # Snapped to 55% "Middle" detent
    assert ThemeManager.orb_glow_intensity == 55
    assert db.get_setting("orb_glow_intensity") == "55"

    pref.orb_slider.setValue(42, apply_snap=True)
    assert pref.orb_slider.value() == 42  # Free-sliding between detents
    assert ThemeManager.orb_glow_intensity == 42

    pref.glass_slider.setValue(34, apply_snap=True)
    assert pref.glass_slider.value() == 35  # Snapped to 35% "Subtle" detent
    assert ThemeManager.glass_transparency == 35
    assert db.get_setting("glass_transparency") == "35"

    pref.glass_slider.setValue(64, apply_snap=True)
    assert pref.glass_slider.value() == 65  # Snapped to 65% "Balanced" detent
    assert ThemeManager.glass_transparency == 65
    assert db.get_setting("glass_transparency") == "65"

    # 4. Test Pro Studio Solid Themes (Adobe Gray & OLED Black)
    pref.theme_radios[ThemeVariant.GRAY].setChecked(True)
    qapp.processEvents()
    assert ThemeManager.current_variant == ThemeVariant.GRAY
    assert ThemeManager.supports_glass_effects() is False
    assert pref.orb_slider.isEnabled() is False
    assert pref.glass_slider.isEnabled() is False
    assert pref.exp_dwm_switch.isEnabled() is False
    assert "Disabled in Pro Solid Themes" in pref.orb_value_badge.text()
    assert "Disabled in Pro Solid Themes" in pref.glass_value_badge.text()
    pal_gray_dark = ThemeManager.get_palette()
    assert pal_gray_dark["accent"] == "#4391E0"
    assert pal_gray_dark["bg"] == "#1d1d1f"
    assert len(pal_gray_dark["orbs"]) == 0

    pref.theme_radios[ThemeVariant.BLACK].setChecked(True)
    qapp.processEvents()
    assert ThemeManager.current_variant == ThemeVariant.BLACK
    assert ThemeManager.supports_glass_effects() is False
    assert pref.orb_slider.isEnabled() is False
    assert pref.glass_slider.isEnabled() is False
    pal_black_dark = ThemeManager.get_palette()
    assert pal_black_dark["bg"] == "#000000"
    assert pal_black_dark["accent"] == "#f4f4f5"

    # Verify solid 100% opaque backdrop painting in OLED Black
    img = QImage(200, 120, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    ThemeManager.paint_window_backdrop(pref, painter)
    painter.end()
    px = img.pixelColor(100, 60)
    assert px.red() == 0 and px.green() == 0 and px.blue() == 0 and px.alpha() == 255

    # 5. Switch back to Femboy Pink and verify sliders unlock with saved percentages intact
    pref.theme_radios[ThemeVariant.PINK].setChecked(True)
    qapp.processEvents()
    assert ThemeManager.supports_glass_effects() is True
    assert pref.orb_slider.isEnabled() is True
    assert pref.glass_slider.isEnabled() is True
    assert pref.orb_slider.value() == 42
    assert pref.glass_slider.value() == 65
    assert "42%" in pref.orb_value_badge.text()
    assert "65%" in pref.glass_value_badge.text()

    # 6. Verify stationary desktop hover (no ElevatedCardWidget upward translation) & global theme binding on Drawers/Home/TitleBar
    from qfluentwidgets import ElevatedCardWidget
    from src.core.models import EditorNote
    window = MainWindow()
    assert not isinstance(window.home_view.drop_card, ElevatedCardWidget)
    assert not isinstance(window.home_view.updates_card, ElevatedCardWidget)
    assert not isinstance(window.home_view.copyright_card, ElevatedCardWidget)

    # Load a note and link into drawers, switch to Light Mode Adobe Gray, then Dark Mode OLED Black
    notes_drawer = window.workspace_view.notes_drawer
    links_drawer = window.workspace_view.links_drawer
    notes_drawer.load_notes([EditorNote(id="n1", block_id=1, text="Check audio levels")], {})
    links_drawer.load_links(["https://drive.google.com/file/d/123"], {})

    window.apply_palette(ThemeVariant.GRAY, dark=False)
    assert "#18181b" in window.title_branding.title_lbl.styleSheet()
    assert "#18181b" in window.home_view.typewriter.styleSheet()
    assert "#18181b" in window.home_view.drop_card.title_lbl.styleSheet()
    assert "#ffffff" in notes_drawer.note_cards[0].styleSheet()

    window.apply_palette(ThemeVariant.BLACK, dark=True)
    # In OLED Black Dark Mode, NoteCard and LinkCard must be dark (#0a0a0c), NEVER #ffffff!
    assert "#0a0a0c" in notes_drawer.note_cards[0].styleSheet()
    assert "#ffffff" not in notes_drawer.note_cards[0].styleSheet()
    assert "#0a0a0c" in links_drawer.link_cards[0].styleSheet()
    assert "#ffffff" not in links_drawer.link_cards[0].styleSheet()
    assert "#f4f4f5" in notes_drawer.count_badge.styleSheet()
    assert "#f4f4f5" in links_drawer.count_badge.styleSheet()

    window.close()
    pref.close()

def test_middle_click_autoscroll_and_pane_titles(qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLabel
    from src.ui.components.widgets import MiddleClickAutoScroller
    sample_docx = os.path.abspath(r"d:\Codes\knot-parser\test_docs\Every Time Family Guy Felt Like Another Show.docx")
    doc_state = parse_docx_file(sample_docx)

    window = MainWindow()
    window.open_workspace()
    window.workspace_view.load_document(doc_state)

    sv = window.workspace_view.script_viewer
    tl = window.workspace_view.timeline_list

    # 1. Verify SCRIPT title replaced by imported file name
    assert sv.title_label.text() == doc_state.name
    sv.set_file_name("custom_script_name.docx")
    assert sv.title_label.text() == "custom_script_name.docx"

    # 2. Verify A-Roll Timestamps title replaced by TIMESTAMPS
    title_widgets = [child for child in tl.findChildren(QLabel) if child.objectName() == "PaneTitle"]
    assert len(title_widgets) >= 1
    assert title_widgets[0].text() == "TIMESTAMPS"

    # 3. Verify filter dropdown in timestamp has Pending (instead of Not Done)
    assert [tl.filter_combo.itemText(i) for i in range(tl.filter_combo.count())] == ["All", "Done", "Pending"]

    # 4. Verify MiddleClickAutoScroller is installed on both script and timeline
    assert isinstance(sv.middle_scroller, MiddleClickAutoScroller)
    assert isinstance(tl.middle_scroller, MiddleClickAutoScroller)
    assert not sv.middle_scroller.is_active
    assert not tl.middle_scroller.is_active

    # Test activating and deactivating middle click autoscroll
    sv.middle_scroller.start(QPoint(200, 200))
    assert sv.middle_scroller.is_active
    assert sv.middle_scroller._indicator is not None
    assert not sv.middle_scroller._indicator.isHidden()

    sv.middle_scroller._on_frame()
    sv.middle_scroller.stop()
    assert not sv.middle_scroller.is_active
    assert sv.middle_scroller._indicator.isHidden()

    window.close()


def test_theme_sliders_multi_drag_responsiveness_and_zero_lag(qapp):
    import time
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from src.ui.theme import ThemeManager, ThemeVariant
    from src.ui.components.preferences_dialog import PreferencesWindow
    from src.core.database import db

    pref = PreferencesWindow()
    pref.resize(840, 620)
    pref.show()
    qapp.processEvents()

    pref.theme_radios[ThemeVariant.PINK].setChecked(True)
    qapp.processEvents()

    def _simulate_slide(slider, start_val: int, end_val: int, steps: int = 25):
        start_x = slider._val_to_x(start_val)
        end_x = slider._val_to_x(end_val)
        y = 13.0

        press_ev = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(start_x, y),
            slider.mapToGlobal(QPointF(start_x, y)),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        slider.mousePressEvent(press_ev)
        assert slider.is_dragging is True

        t0 = time.perf_counter()
        for i in range(1, steps + 1):
            cur_x = start_x + (end_x - start_x) * (i / steps)
            move_ev = QMouseEvent(
                QEvent.MouseMove,
                QPointF(cur_x, y),
                slider.mapToGlobal(QPointF(cur_x, y)),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
            slider.mouseMoveEvent(move_ev)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        rel_ev = QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(end_x, y),
            slider.mapToGlobal(QPointF(end_x, y)),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        )
        slider.mouseReleaseEvent(rel_ev)
        assert slider.is_dragging is False
        return elapsed_ms

    # Slide 1 on glass_slider (0 -> 100) and drop, then Slide 2 (100 -> 35) and drop
    ms_glass_1 = _simulate_slide(pref.glass_slider, 0, 100, steps=40)
    assert pref.glass_slider.value() == 100
    assert ThemeManager.glass_transparency == 100
    assert db.get_setting("glass_transparency") == "100"

    ms_glass_2 = _simulate_slide(pref.glass_slider, 100, 35, steps=40)
    assert pref.glass_slider.value() == 35
    assert ThemeManager.glass_transparency == 35
    assert db.get_setting("glass_transparency") == "35"

    # Slide 1 on orb_slider (0 -> 100) and drop, then Slide 2 (100 -> 25) and drop
    ms_orb_1 = _simulate_slide(pref.orb_slider, 0, 100, steps=40)
    assert pref.orb_slider.value() == 100
    assert ThemeManager.orb_glow_intensity == 100
    assert db.get_setting("orb_glow_intensity") == "100"

    ms_orb_2 = _simulate_slide(pref.orb_slider, 100, 25, steps=40)
    assert pref.orb_slider.value() == 25
    assert ThemeManager.orb_glow_intensity == 25
    assert db.get_setting("orb_glow_intensity") == "25"

    # Verify 40 drag steps complete well under 100ms total (< 2.5ms per step)
    assert ms_glass_1 < 100.0
    assert ms_glass_2 < 100.0
    assert ms_orb_1 < 100.0
    assert ms_orb_2 < 100.0

    pref.close()

def test_sizing_state_machine_left_edge_optimization(qapp):
    """Verifies that Win32 sizing loop state machine suspends heavy child layouts and duplicate paints during left resize."""
    from src.ui.window import MainWindow
    from src.core.models import DocumentState, ScriptItem
    from ctypes.wintypes import MSG
    import win32con

    win = MainWindow()
    win.show()
    qapp.processEvents()

    # Load mock doc to switch to WorkspaceView
    doc = DocumentState(id='d1', file_signature='s1', name='Test Doc', file_path='t.docx', raw_html='<p>Sample HTML text content</p>')
    doc.items = [ScriptItem(
        id=str(i), row_index=i, raw_timestamp='01:00:00:00',
        start_timecode='01:00:00:00', start_seconds=0.0,
        end_timecode='01:00:05:00', end_seconds=5.0,
        duration_seconds=5.0, duration_display='5s',
        text=f'Timeline Card {i}'
    ) for i in range(25)]
    win._on_parse_finished(doc)
    qapp.processEvents()

    wv = win.workspace_view
    assert win.stack.currentWidget() == wv
    assert wv.layout().isEnabled() is True
    assert wv.script_viewer.browser.updatesEnabled() is True
    assert wv.timeline_list.container.updatesEnabled() is True

    class FakeMessage:
        def __init__(self, hwnd, msg, wparam, lparam):
            self._msg = MSG(hwnd, msg, wparam, lparam, 0)
        def __int__(self):
            import ctypes
            return ctypes.addressof(self._msg)

    hwnd = int(win.winId())

    # 1. Simulate WM_ENTERSIZEMOVE
    win.nativeEvent(b'windows_generic_MSG', FakeMessage(hwnd, win32con.WM_ENTERSIZEMOVE, 0, 0))
    assert win._is_resizing is False
    assert win._is_left_resizing is False

    # 2. Simulate WM_SIZING from LEFT edge (WMSZ_LEFT = 1)
    win.nativeEvent(b'windows_generic_MSG', FakeMessage(hwnd, win32con.WM_SIZING, win32con.WMSZ_LEFT, 0))
    assert win._is_resizing is True
    assert win._is_left_resizing is True
    # Layouts and updates MUST remain enabled so WorkspaceView resizes and refreshes cleanly
    assert wv.layout().isEnabled() is True
    assert wv.script_viewer.browser.updatesEnabled() is True
    assert wv.timeline_list.container.updatesEnabled() is True
    # Verify fine-grained reflow hold during left drag
    assert wv.script_viewer.browser._is_reflow_held is True
    sizes_before = wv.splitter.sizes()

    # 3. Simulate window resize during drag - WorkspaceView and Splitter must expand dynamically
    win.resize(1340, 840)
    qapp.processEvents()
    assert wv.width() > 1000
    assert wv.splitter.width() > 1000
    # Right pane (Timestamps) maintains steady width (stretchFactor=0)
    sizes_after = wv.splitter.sizes()
    assert sizes_after[1] == sizes_before[1]

    # 4. Simulate WM_EXITSIZEMOVE
    win.nativeEvent(b'windows_generic_MSG', FakeMessage(hwnd, win32con.WM_EXITSIZEMOVE, 0, 0))
    assert win._is_resizing is False
    assert win._is_left_resizing is False
    assert wv.script_viewer.browser._is_reflow_held is False
    assert wv.layout().isEnabled() is True
    assert wv.script_viewer.browser.updatesEnabled() is True
    assert wv.timeline_list.container.updatesEnabled() is True

    win.close()
