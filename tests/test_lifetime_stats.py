"""The Data Vault's lifetime numbers (parse time, total cuts, duration) actually count.

The test suite runs on its own throwaway database (see conftest.py), so resetting the stats here is safe."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from sample_script import sample_docx_path
from src.core import parser
from src.core.database import db


@pytest.fixture(autouse=True)
def clean_stats():
    db.reset_stats()
    yield
    db.reset_stats()


def _forget_script_so_it_looks_new():
    """Make the next parse see the script as never opened before."""
    db.set_setting("stats_counted_scripts", "[]")


def test_parsing_a_new_script_counts_its_cuts_and_time():
    _forget_script_so_it_looks_new()
    doc = parser.parse_docx_file(sample_docx_path())
    stats = db.get_stats()
    assert stats.total_aroll_count == len(doc.items) > 0
    assert stats.lifetime_seconds == stats.total_duration_logged > 0


def test_parsing_the_same_script_again_does_not_count_it_twice():
    _forget_script_so_it_looks_new()
    parser.parse_docx_file(sample_docx_path())
    once = db.get_stats()
    parser.parse_docx_file(sample_docx_path())
    twice = db.get_stats()
    assert (twice.total_aroll_count, twice.lifetime_seconds) == (once.total_aroll_count, once.lifetime_seconds)


def test_re_importing_an_edited_script_is_not_counted_again():
    _forget_script_so_it_looks_new()
    first = parser.parse_docx_file(sample_docx_path())
    once = db.get_stats()
    parser.parse_docx_file(sample_docx_path(), previous_doc=first)
    assert db.get_stats().total_aroll_count == once.total_aroll_count


def test_a_counter_failure_never_stops_a_script_opening(monkeypatch):
    _forget_script_so_it_looks_new()

    def boom(*a, **k):
        raise RuntimeError("stats broke")
    monkeypatch.setattr(db, "increment_stats", boom)
    doc = parser.parse_docx_file(sample_docx_path())
    assert len(doc.items) > 0
