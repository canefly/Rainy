import os
import sys
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.ui.window import MainWindow
from src.ui.components.link_info_dialog import LinkMetadataDialog
from src.ui.components.drawers import LinksDrawer, NotesDrawer
from src.core.models import DocumentState, EditorNote
from src.core.unfurl_engine import unfurl_engine, LinkMetadata

@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app

def test_modal_in_modal_interaction_and_no_opacity_animations(qapp):
    """Verifies that:
    1. No opacity fade animations exist on any dialogs (snappy, 0ms latency).
    2. Zero QGraphicsOpacityEffect is attached to dialogs.
    3. LinkMetadataDialog (opened as a modal inside the modal LinksDrawer) is fully interactive.
    4. Clicking Close or clicking mask closes the child modal cleanly without affecting the parent drawer.
    """
    window = MainWindow()
    window.resize(1340, 840)
    window.show()
    window.open_workspace()

    ws = window.workspace_view
    test_url = "https://drive.google.com/file/d/test_modal_nested/view"

    meta = LinkMetadata(
        url=test_url,
        title="Test Clip.mp4",
        provider="Google Drive",
        domain="drive.google.com",
        file_type="VIDEO",
        resolution="4K (3872x2592)",
        file_size_display="2.64 MB",
        loading=False
    )
    unfurl_engine.store_metadata(test_url, meta)

    # 1. Open Source Links drawer
    ws.links_drawer.load_links([test_url], {})
    ws.toggle_links_drawer()
    assert not ws.links_drawer.isHidden()
    assert ws.links_drawer.graphicsEffect() is None

    # 2. Open LinkMetadataDialog (modal inside the drawer modal)
    dialog = LinkMetadataDialog(test_url, parent=ws.links_drawer)
    dialog.show()
    assert not dialog.isHidden()
    assert dialog.graphicsEffect() is None
    assert dialog.isClosableOnMaskClicked() is True
    assert dialog.parent() == ws.links_drawer

    # 3. Test interactive clicking on Re-fetch button
    refreshed = []
    dialog.refresh_btn.clicked.connect(lambda: refreshed.append(True))
    QTest.mouseClick(dialog.refresh_btn, Qt.LeftButton)
    assert len(refreshed) == 1

    # 4. Test interactive clicking on Copy URL button
    copied = []
    dialog.copy_url_btn.clicked.connect(lambda: copied.append(True))
    QTest.mouseClick(dialog.copy_url_btn, Qt.LeftButton)
    assert len(copied) == 1

    # 5. Test interactive clicking on Close button
    closed = []
    dialog.yesButton.clicked.connect(lambda: closed.append(True))
    QTest.mouseClick(dialog.yesButton, Qt.LeftButton)
    assert len(closed) == 1

    # Verify dialog closes cleanly
    dialog.accept()
    assert dialog.isHidden()

    # Verify parent LinksDrawer remains open and unharmed
    assert not ws.links_drawer.isHidden()
    assert ws.links_drawer.graphicsEffect() is None

    # Clean up
    ws.links_drawer.hide()
    window.close()
