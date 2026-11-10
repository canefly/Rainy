import pytest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication
from src.ui.components.script_viewer import ScriptTextBrowser

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

def test_script_viewer_track_click_no_teleport_on_wheel(qapp):
    browser = ScriptTextBrowser()
    browser.resize(800, 600)
    # Populate with enough paragraphs to have a large scroll range
    html_lines = ["<p style='height: 40px;'>Line " + str(i) + "</p>" for i in range(200)]
    browser.setHtml("".join(html_lines))
    browser.show()
    qapp.processEvents()

    vbar = browser.delegate.vScrollBar
    assert vbar.maximum() > 500

    # Simulate clicking far down on the scrollbar track (e.g., at y=500)
    click_y = browser.height() - 50
    press_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(6, click_y),
        QPointF(6, click_y),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    vbar.mousePressEvent(press_ev)

    release_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(6, click_y),
        QPointF(6, click_y),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    vbar.mouseReleaseEvent(release_ev)

    # The internal value of vbar must now reflect the click target, NOT remain at 0
    internal_val = getattr(vbar, "_SmoothScrollBar__value", 0)
    assert internal_val > 100, f"Expected internal_val > 100 after track click, got {internal_val}"

    # Now simulate mouse wheel down (delta_y = -120)
    wheel_ev = QWheelEvent(
        QPointF(100, 100),
        QPointF(100, 100),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False
    )
    browser.delegate.eventFilter(browser.viewport(), wheel_ev)

    # Internal val must NOT teleport to 0!
    val_after_wheel = getattr(vbar, "_SmoothScrollBar__value", 0)
    assert val_after_wheel >= internal_val, f"Teleported to top! val_after_wheel={val_after_wheel}, expected >= {internal_val}"

    browser.close()
