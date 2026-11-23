"""The small "?" on the Welcome screen: first-time help for someone who has just installed Rainy."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src.core.license_core import LicenseSnapshot, LicenseState
from src.ui.views import gate_view as gv


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def gate(qapp):
    g = gv.GateView()
    g.show()
    yield g
    g.close()


def snap(state):
    return LicenseSnapshot(state)


def test_the_help_button_shows_on_the_welcome_and_waiting_screens(gate):
    for state in (LicenseState.NEEDS_REGISTRATION, LicenseState.PENDING):
        gate.show_snapshot(snap(state))
        assert gate.help_btn.isVisibleTo(gate), state


@pytest.mark.parametrize("state", [LicenseState.CHECKING, LicenseState.REVOKED, LicenseState.EXPIRED,
                                   LicenseState.UNREACHABLE, LicenseState.CLOCK_SKEW, LicenseState.ERROR])
def test_other_screens_have_no_help_button(gate, state):
    gate.show_snapshot(snap(state))
    assert not gate.help_btn.isVisibleTo(gate)


def test_the_help_text_starts_hidden_and_the_button_toggles_it(gate):
    gate.show_snapshot(snap(LicenseState.NEEDS_REGISTRATION))
    assert not gate.help_text.isVisibleTo(gate)
    gate.help_btn.click()
    assert gate.help_text.isVisibleTo(gate)
    gate.help_btn.click()
    assert not gate.help_text.isVisibleTo(gate)


def test_the_help_text_explains_the_first_steps(gate):
    text = gate.help_text.text()
    for must in ("Request access", "approve", "unlocks by itself", "helper programs", "Message Cane"):
        assert must in text


def test_moving_to_another_screen_closes_an_open_help(gate):
    gate.show_snapshot(snap(LicenseState.NEEDS_REGISTRATION))
    gate.help_btn.click()
    assert gate.help_text.isVisibleTo(gate)
    gate.show_snapshot(snap(LicenseState.REVOKED))
    assert not gate.help_text.isVisibleTo(gate)
    gate.show_snapshot(snap(LicenseState.NEEDS_REGISTRATION))
    assert not gate.help_text.isVisibleTo(gate)               # and it stays closed when you come back


def test_the_help_text_is_plain_text_and_follows_the_theme(gate):
    from src.ui.theme import ThemeManager
    assert gate.help_text.textFormat() == gv.Qt.PlainText
    gate.apply_theme()
    assert ThemeManager.get_palette()["surface_hover"] in gate.help_text.styleSheet()


def test_the_card_grows_to_fit_the_help_and_shrinks_back(gate, qapp):
    gate.show_snapshot(snap(LicenseState.NEEDS_REGISTRATION))
    qapp.processEvents()
    closed = gate.card.height()
    gate.toggle_help()
    qapp.processEvents()
    assert gate.card.height() > closed
    assert gate.help_text.height() >= gate.help_text.heightForWidth(gate.help_text.width())      # nothing cut off
    gate.toggle_help()
    qapp.processEvents()
    assert gate.card.height() == closed
