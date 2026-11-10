import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint
from qfluentwidgets import RoundMenu, ComboBox, Action, FluentIcon
from qfluentwidgets.components.widgets.menu import MenuAnimationType
from src.ui.theme import disable_menu_animations


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    disable_menu_animations()
    return app


def test_round_menu_instant_exec(qapp):
    menu = RoundMenu()
    act = Action(FluentIcon.DOCUMENT, "Test Item")
    menu.addAction(act)

    menu.exec(QPoint(150, 200), aniType=MenuAnimationType.DROP_DOWN)
    assert menu.isVisible()
    # Confirm no property animation is running
    assert not menu.aniManager.ani.state() == menu.aniManager.ani.State.Running
    assert menu.mask().isEmpty()
    menu.close()


def test_round_menu_pull_up_instant_exec(qapp):
    menu = RoundMenu()
    act = Action(FluentIcon.DOCUMENT, "Pull Up Item")
    menu.addAction(act)

    menu.exec(QPoint(150, 600), aniType=MenuAnimationType.PULL_UP)
    assert menu.isVisible()
    assert not menu.aniManager.ani.state() == menu.aniManager.ani.State.Running
    assert menu.mask().isEmpty()
    menu.close()


def test_combo_box_instant_popup(qapp):
    combo = ComboBox()
    combo.addItems(["Option A", "Option B", "Option C"])
    combo.resize(140, 32)
    combo.show()

    combo._showComboMenu()
    drop_menu = combo.dropMenu
    assert drop_menu.isVisible()
    assert not drop_menu.aniManager.ani.state() == drop_menu.aniManager.ani.State.Running
    assert drop_menu.mask().isEmpty()
    drop_menu.close()
    combo.close()
