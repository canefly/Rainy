"""The Settings profile card shows who is signed in (just the name), and the title bar has no author link."""
import sys

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel

from src.core.license_service import LicenseService
from src.ui.components.preferences_dialog import PreferencesWindow, UserProfileCard, initials_for


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


class FakeService(QObject):
    state_changed = Signal(object)

    def __init__(self, name):
        super().__init__()
        self.snapshot = type("Snap", (), {"editor_name": name})()
        self.controller = None


@pytest.fixture
def signed_in(qapp):
    """Pretend the license service is running for this editor; the real one is put back afterwards."""
    saved = LicenseService._instance
    holder = {}

    def sign_in(name):
        holder["svc"] = FakeService(name)
        LicenseService._instance = holder["svc"]
        return holder["svc"]

    yield sign_in
    LicenseService._instance = saved


# -- initials ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("Cane fly", "CF"), ("canefly", "C"), ("  mary   jane  watson ", "MJ"), ("*Cane* _fly_", "CF"),
    ("", "E"), ("   ", "E"), ("***", "E"), ("élodie dupont", "ÉD"), ("林 小", "林小"), ("42 things", "4T"),
])
def test_initials(name, expected):
    assert initials_for(name) == expected


# -- the card ---------------------------------------------------------------------------------------
def test_the_card_shows_the_signed_in_editor_and_no_email(qapp, signed_in):
    signed_in("Cane fly")
    card = UserProfileCard()
    assert card.name_lbl.text() == "Cane fly" and card.avatar._initials == "CF"
    assert not hasattr(card, "sub_lbl")                                   # the email line is gone
    texts = " ".join(lbl.text() for lbl in card.findChildren(QLabel))
    assert "@" not in texts and "outlook" not in texts.lower()


def test_nobody_signed_in_shows_a_neutral_editor_not_anyones_name(qapp):
    saved = LicenseService._instance
    LicenseService._instance = None
    try:
        card = UserProfileCard()
        assert card.name_lbl.text() == "Editor" and card.avatar._initials == "E"
    finally:
        LicenseService._instance = saved


def test_the_card_follows_the_license_state_live(qapp, signed_in):
    svc = signed_in("First Name")
    card = UserProfileCard()
    assert card.name_lbl.text() == "First Name"
    snap = type("Snap", (), {"editor_name": "Renamed Editor"})()
    svc.state_changed.emit(snap)
    assert card.name_lbl.text() == "Renamed Editor" and card.avatar._initials == "RE"
    svc.state_changed.emit(type("Snap", (), {"editor_name": ""})())      # e.g. locked / not registered yet
    assert card.name_lbl.text() == "Editor"


def test_a_name_with_markup_is_shown_literally(qapp, signed_in):
    signed_in("<b>Bold</b> <img src=x onerror=alert(1)>")
    card = UserProfileCard()
    assert card.name_lbl.text() == "<b>Bold</b> <img src=x onerror=alert(1)>"
    assert card.name_lbl.textFormat().name == "PlainText"


def test_the_name_sits_on_the_same_centre_line_as_the_circle(qapp, signed_in):
    signed_in("Canefly")
    win = PreferencesWindow(None, initial_tab=0)
    win.resize(1000, 700)
    win.show()
    qapp.processEvents()
    card = win.user_profile
    assert card.avatar.geometry().center().y() == card.name_lbl.geometry().center().y()
    assert card.avatar.geometry().right() < card.name_lbl.geometry().left()
    win.close()


def test_a_long_name_is_cut_with_an_ellipsis_and_keeps_the_full_name_in_the_tooltip(qapp, signed_in):
    long = "A very long editor name that keeps going and going and going"
    signed_in(long)
    win = PreferencesWindow(None, initial_tab=0)
    win.resize(1000, 700)
    win.show()
    qapp.processEvents()
    card = win.user_profile
    assert card.name_lbl.text() != long and card.name_lbl.text().endswith("…")
    assert card.name_lbl.toolTip() == long
    card.set_name("Short")
    assert card.name_lbl.text() == "Short" and card.name_lbl.toolTip() == ""
    win.close()


# -- the title bar ----------------------------------------------------------------------------------
def test_the_title_bar_has_no_author_link(qapp):
    from src.ui.components.titlebar import TitleBarBrandingWidget
    w = TitleBarBrandingWidget()
    assert not hasattr(w, "author_link")
    assert [lbl.text() for lbl in w.findChildren(QLabel)] == ["Rainy"]
