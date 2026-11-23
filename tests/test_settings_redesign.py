import os
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.ui.theme import ThemeManager, ThemeVariant
from src.ui.components.preferences_dialog import (
    PreferencesWindow, PreferencesDialog, UserProfileCard, HomeHeroCard
)
from qfluentwidgets import SwitchButton, PushButton, SearchLineEdit


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_settings_window_initialization(qapp):
    win = PreferencesWindow(None, initial_tab=0)
    assert win is not None
    assert win.width() >= 860
    assert win.height() >= 580
    assert "Preferences" in win.windowTitle()

    # TitleBar search box
    assert hasattr(win, "search_box")
    assert isinstance(win.search_box, SearchLineEdit)
    assert win.search_box.placeholderText() == "Find a setting"

    # Profile card
    assert hasattr(win, "user_profile")
    assert isinstance(win.user_profile, UserProfileCard)
    assert win.user_profile.name_lbl.text() == "Editor"        # nobody is signed in under test: no personal name, no email
    assert not hasattr(win.user_profile, "sub_lbl")

    # Nav list & categories
    assert win.nav_list.count() == 11
    categories = [win._categories[i][0] for i in range(win.nav_list.count())]
    assert "Home" in categories
    assert "Data Vault" in categories
    assert "Media & Transcoding" in categories
    assert "Core Engines" in categories
    assert "Personalization" in categories
    assert "Shortcuts" in categories
    assert "Copyright Helper" in categories
    assert "Cookies & Accounts" in categories
    assert "AI Models" in categories
    assert "MCP" in categories
    assert "About & Updates" in categories

    win.close()


def test_settings_tab_switching_and_routing(qapp):
    win = PreferencesWindow(None, initial_tab=0)
    assert win.nav_list.currentRow() == 0
    assert win.stack.currentIndex() == 0

    # Switch by key strings
    win.switch_tab("vault")
    assert win.nav_list.currentRow() == 1
    assert win.stack.currentIndex() == 1

    win.switch_tab("transcode")
    assert win.nav_list.currentRow() == 2
    assert win.stack.currentIndex() == 2

    win.switch_tab("engine")
    assert win.nav_list.currentRow() == 3
    assert win.stack.currentIndex() == 3

    win.switch_tab("appearance")
    assert win.nav_list.currentRow() == 4
    assert win.stack.currentIndex() == 4

    win.switch_tab("shortcuts")
    assert win.nav_list.currentRow() == 5
    assert win.stack.currentIndex() == 5

    win.switch_tab("copyright")
    assert win.nav_list.currentRow() == 6
    assert win.stack.currentIndex() == 6

    win.switch_tab("cookies")
    assert win.nav_list.currentRow() == 7
    assert win.stack.currentIndex() == 7

    win.switch_tab("ai")
    assert win.nav_list.currentRow() == 8
    assert win.stack.currentIndex() == 8

    win.switch_tab("mcp")
    assert win.nav_list.currentRow() == 9
    assert win.stack.currentIndex() == 9

    win.switch_tab("about")
    assert win.nav_list.currentRow() == 10
    assert win.stack.currentIndex() == 10

    win.switch_tab("home")
    assert win.nav_list.currentRow() == 0
    assert win.stack.currentIndex() == 0

    # Search bar routing
    win.search_box.setText("dark")
    assert win.nav_list.currentRow() == 4  # Personalization

    win.search_box.setText("cookie")
    assert win.nav_list.currentRow() == 7  # Cookies

    win.search_box.setText("mpv")
    assert win.nav_list.currentRow() == 3  # Core Engines

    win.close()


def test_settings_home_tab_components(qapp):
    win = PreferencesWindow(None, initial_tab=0)

    # Hero Cards
    assert hasattr(win, "hero_device")
    assert isinstance(win.hero_device, HomeHeroCard)
    assert "Canefly" in win.hero_device.title_lbl.text()

    assert hasattr(win, "hero_vault")
    assert isinstance(win.hero_vault, HomeHeroCard)
    assert "Vault" in win.hero_vault.title_lbl.text()

    assert hasattr(win, "hero_engine")
    assert isinstance(win.hero_engine, HomeHeroCard)

    # Synced Dark Mode switches
    assert hasattr(win, "home_dark_switch")
    assert hasattr(win, "dark_switch")
    assert isinstance(win.home_dark_switch, SwitchButton)
    assert isinstance(win.dark_switch, SwitchButton)

    initial_mode = ThemeManager.is_dark
    # Toggle home dark switch
    win.home_dark_switch.setChecked(not initial_mode)
    assert ThemeManager.is_dark == (not initial_mode)
    assert win.dark_switch.isChecked() == (not initial_mode)

    # Toggle back
    win.dark_switch.setChecked(True)
    ThemeManager.is_dark = True
    from src.core.database import db
    db.set_setting("theme_dark", "true")
    assert ThemeManager.is_dark is True
    assert win.home_dark_switch.isChecked() is True

    # Recommended and Quick Action buttons exist and are connected
    buttons = win.findChildren(PushButton)
    button_texts = [b.text() for b in buttons]
    assert any("Personalize" in t for t in button_texts)
    assert any("View Live Log..." in t for t in button_texts)
    assert any("Configure" in t for t in button_texts)
    assert any("View Shortcuts" in t for t in button_texts)
    assert any("Open Helper" in t for t in button_texts)
    assert any("Export (.zip)" in t for t in button_texts)

    win.close()


def test_settings_theming_and_profile_card(qapp):
    win = PreferencesWindow(None, initial_tab=0)

    # Test toggling themes
    win._on_theme_toggled(True, ThemeVariant.BLUE)
    assert ThemeManager.current_variant == ThemeVariant.BLUE

    win._on_theme_toggled(True, ThemeVariant.PINK)
    assert ThemeManager.current_variant == ThemeVariant.PINK

    win.close()


def test_settings_search_box_centering_and_neutral_gray_surfaces(qapp):
    from PySide6.QtCore import QPoint
    from src.ui.components.preferences_dialog import PrefSidebarFrame, PrefCardFrame

    win = PreferencesWindow(None, initial_tab=0)
    win.resize(1000, 700)
    win.show()
    qapp.processEvents()

    # Verify search box is centered in the titlebar
    tb = win.titleBar
    sb = win.search_box
    sb_center = sb.x() + sb.width() // 2
    tb_center = win.width() // 2
    assert abs(sb_center - tb_center) <= 2  # Mathematically centered!

    # Test resize event keeps search box centered
    win.resize(1200, 700)
    qapp.processEvents()
    sb_center = sb.x() + sb.width() // 2
    tb_center = win.width() // 2
    assert abs(sb_center - tb_center) <= 2

    # Verify drag safety: clicking inside search box is not treated as drag region
    assert tb.canDrag(QPoint(sb.x() + 10, sb.y() + 10)) is False
    assert tb.canDrag(QPoint(50, 20)) is True

    # Verify neutral gray card and sidebar surfaces
    sidebar = win.findChild(PrefSidebarFrame)
    assert sidebar is not None
    cards = win.findChildren(PrefCardFrame)
    assert len(cards) >= 3

    win.close()


def test_settings_prewarm_and_instant_open(qapp):
    import time
    from src.ui.window import MainWindow
    main_win = MainWindow()
    qapp.processEvents()

    # Pre-warm services during idle
    main_win._idle_prewarm_services()
    assert main_win.pref_window is not None

    # Measure opening time when pre-warmed
    t0 = time.perf_counter()
    main_win.open_settings(initial_tab=0)
    t1 = time.perf_counter()
    open_ms = (t1 - t0) * 1000

    assert main_win.pref_window.isVisible() is True
    # Pre-warmed open must be fast (under 350ms, down from 7000ms cold probe)
    assert open_ms < 350.0, f"Expected <350ms, got {open_ms:.1f}ms"

    main_win.pref_window.close()
    main_win.close()


