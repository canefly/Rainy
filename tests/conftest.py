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
atexit.register(lambda: _TEMP_DIR.cleanup())


@pytest.fixture(autouse=True, scope="session")
def isolate_test_database():
    """Guarantee that tests never read or write to user's live ~/.knot/knot.db"""
    from src.core import database

    real_db = os.path.abspath(os.path.expanduser("~/.knot/knot.db"))
    resolved = os.path.abspath(database.DB_PATH)
    if resolved == real_db or os.path.abspath(_TEMP_DIR.name) not in resolved:
        pytest.exit(
            f"Refusing to run: database resolves to {resolved}, not the isolated temp DB. "
            "Something imported src.core.database before tests/conftest.py set KNOT_DB_PATH.",
            returncode=2,
        )
    yield
