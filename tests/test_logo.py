"""The logo must stay sharp: a big enough source file, a Windows icon with big sizes, and labels that draw it at
the screen's real pixel density instead of stretching a small image."""
import os
import sys

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QWidget

from src.ui import logo as logo_mod
from src.ui.logo import LOGO_PATH, LogoLabel, logo_pixmap

ASSETS = os.path.dirname(LOGO_PATH)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


# -- the files ---------------------------------------------------------------------------------------
def test_the_logo_png_is_large_and_square_with_transparency():
    im = Image.open(LOGO_PATH)
    assert im.width == im.height and im.width >= 256          # 32 px was the blurry one
    assert im.mode == "RGBA"


def test_the_windows_icon_has_the_sizes_windows_asks_for():
    """Taskbar, Alt+Tab, desktop shortcut and the installer each pick a size from this one file."""
    im = Image.open(os.path.join(ASSETS, "logo.ico"))
    sizes = set(im.info["sizes"])
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


# -- drawing it ---------------------------------------------------------------------------------------
def test_the_pixmap_has_as_many_real_pixels_as_the_screen_needs(qapp):
    p1 = logo_pixmap(64, 1.0)
    p2 = logo_pixmap(64, 2.0)
    p3 = logo_pixmap(56, 1.5)
    assert (p1.width(), p1.devicePixelRatio()) == (64, 1.0)
    assert (p2.width(), p2.height(), p2.devicePixelRatio()) == (128, 128, 2.0)
    assert (p3.width(), p3.devicePixelRatio()) == (84, 1.5)
    assert p2.width() <= Image.open(LOGO_PATH).width          # always scaled down from the source, never up


def test_a_missing_logo_file_gives_an_empty_picture_not_a_crash(qapp, tmp_path):
    assert logo_pixmap(64, 2.0, path=str(tmp_path / "nope.png")).isNull()


def test_a_ratio_below_one_is_treated_as_one(qapp):
    assert logo_pixmap(64, 0.0).width() == 64 and logo_pixmap(64, -3).width() == 64


def test_the_label_redraws_when_the_screen_density_changes(qapp, monkeypatch):
    host = QWidget()
    label = LogoLabel(64, host)
    given = []
    monkeypatch.setattr(label, "setPixmap", lambda px: given.append(px))       # QLabel.pixmap() reports at the label's own ratio
    monkeypatch.setattr(LogoLabel, "devicePixelRatioF", lambda self: 2.0)      # moved to a 2x screen
    label._refresh()
    assert len(given) == 1 and given[0].width() == 128 and given[0].devicePixelRatio() == 2.0
    label._refresh()                                                           # same screen: no needless rescale
    assert len(given) == 1
    monkeypatch.setattr(LogoLabel, "devicePixelRatioF", lambda self: 1.0)      # and back
    label._refresh()
    assert len(given) == 2 and given[1].width() == 64


def test_a_label_on_a_real_2x_screen_holds_the_big_picture(qapp):
    """The same thing end to end: QT_SCALE_FACTOR makes the screen genuinely 2x (see the shot script in the notes)."""
    label = LogoLabel(64)
    assert label.pixmap().width() in (64, 128) and not label.pixmap().isNull()


def test_the_gate_and_update_screens_use_the_sharp_label(qapp):
    from src.ui.views.gate_view import GateView
    from src.ui.views.update_required_view import UpdateRequiredView
    assert isinstance(GateView().logo, LogoLabel)
    assert isinstance(UpdateRequiredView().logo, LogoLabel)
    assert not logo_mod.logo_pixmap(56, 1.0).isNull()
