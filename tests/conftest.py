import atexit
import os
import tempfile
import pytest

# Must happen at import time: conftest loads before test modules are collected,
# and src.core.database resolves DB_PATH (and creates `db`) when first imported.
# Setting this inside a fixture is too late and lets tests hit the live DB.
_TEMP_DIR = tempfile.TemporaryDirectory(prefix="knot_test_")
_TEST_DB = os.path.join(_TEMP_DIR.name, "isolated_test_knot.db")
os.environ["KNOT_DB_PATH"] = _TEST_DB
# The whole app home (logs, models, cache, updates, engines) is a temp folder too, so a test run
# never touches the real %LOCALAPPDATA%\com.canefly.knot.
os.environ["KNOT_HOME"] = os.path.join(_TEMP_DIR.name, "home")
# Same deal for photos extracted from scripts: keep them out of ~/Documents/Rainy.
os.environ["KNOT_PHOTOS_DIR"] = os.path.join(_TEMP_DIR.name, "Photos Temp")
# And never pick up (or download into) the real auto-censor model folder.
os.environ["KNOT_CENSOR_MODEL_DIR"] = os.path.join(_TEMP_DIR.name, "photo_censor_model")
os.environ["KNOT_NO_AUTO_DOWNLOAD"] = "1"   # no first-launch model download from tests
# MainWindow-based UI tests must not hit the license server or sit behind the gate screen.
# (License tests build their own controller and never go through this switch.)
os.environ["KNOT_SKIP_LICENSE"] = "1"
# ...and no MainWindow-based test may reach out to the update server either.
os.environ["KNOT_NO_UPDATE_CHECK"] = "1"
# VO loudness-envelope cache (speech-island detection) stays out of the real cache folder.
os.environ["KNOT_VO_ENVELOPE_DIR"] = os.path.join(_TEMP_DIR.name, "vo_envelopes")
os.environ["KNOT_CUT_PREVIEW_DIR"] = os.path.join(_TEMP_DIR.name, "cut_preview")
def _cleanup_temp_home():
    import logging
    logging.shutdown()                      # release knot.log, which lives in the temp app home
    import shutil
    shutil.rmtree(_TEMP_DIR.name, ignore_errors=True)


atexit.register(_cleanup_temp_home)


@pytest.fixture(autouse=True, scope="session")
def isolate_test_database():
    """Guarantee that tests never read or write to the user's live knot.db"""
    from src.core import database

    real_db = os.path.abspath(os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "com.canefly.knot", "knot.db"))
    resolved = os.path.abspath(database.DB_PATH)
    if resolved == real_db or os.path.abspath(_TEMP_DIR.name) not in resolved:
        pytest.exit(
            f"Refusing to run: database resolves to {resolved}, not the isolated temp DB. "
            "Something imported src.core.database before tests/conftest.py set KNOT_DB_PATH.",
            returncode=2,
        )
    yield
