import sys
import pytest
from PySide6.QtWidgets import QApplication
from src.core.updater import parse_version, UpdateService, APP_VERSION

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app

def test_parse_version_comparisons():
    assert parse_version("1.2.0") == (1, 2, 0)
    assert parse_version("v1.2.0") == (1, 2, 0)
    assert parse_version("1.2.1") > parse_version("1.2.0")
    assert parse_version("2.0.0") > parse_version("1.9.9")
    assert parse_version("1.2.0") == parse_version("1.2")
    assert parse_version("v1.3.0-rc1") > parse_version("1.2.9")

def test_updater_service_singleton():
    s1 = UpdateService.instance()
    s2 = UpdateService.instance()
    assert s1 is s2
    assert s1.current_version == APP_VERSION


def test_live_version_endpoint_payload():
    from src.core.updater import UpdateCheckWorker, VERSION_ENDPOINT
    import urllib.request
    import json

    req = urllib.request.Request(
        VERSION_ENDPOINT,
        headers={"User-Agent": "ClickablesTools/2.0 Native"}
    )
    with urllib.request.urlopen(req, timeout=5) as res:
        assert res.status == 200
        data = json.loads(res.read().decode("utf-8"))
        assert "latest_version" in data
        assert "download_url" in data
        assert "changelog" in data
        assert isinstance(data["changelog"], list)


def test_about_window_updates_ui(qapp):
    from src.ui.components.about_dialog import AboutWindow
    w = AboutWindow()
    assert hasattr(w, "ver_badge")
    assert hasattr(w, "check_btn")
    assert hasattr(w, "download_btn")
    assert "v1.2.0" in w.ver_desc.text()
    assert w.download_btn.isHidden()
    w.close()

def test_titlebar_update_pill(qapp):
    from src.ui.components.titlebar import TitleBarStatsWidget
    stats = TitleBarStatsWidget()
    assert hasattr(stats, "update_pill")
    assert stats.update_pill.isHidden()
    stats.set_update_available("1.3.0")
    assert not stats.update_pill.isHidden()
    assert "v1.3.0" in stats.update_pill.text()
    stats.close()
