import os
import tempfile
import pytest

@pytest.fixture(autouse=True, scope="session")
def isolate_test_database():
    """Guarantee that tests never read or write to user's live ~/.knot/knot.db"""
    temp_dir = tempfile.TemporaryDirectory()
    test_db = os.path.join(temp_dir.name, "isolated_test_knot.db")
    os.environ["KNOT_DB_PATH"] = test_db
    yield
    if "KNOT_DB_PATH" in os.environ:
        del os.environ["KNOT_DB_PATH"]
    try:
        temp_dir.cleanup()
    except Exception:
        pass
