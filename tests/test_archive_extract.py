"""Pulling engine files out of downloaded archives: only what we ask for, flattened, with real content, never
outside the target folder, and never a wrong `tar`."""
import os
import subprocess
import sys
import zipfile

import pytest

from src.core import archive_extract as ax

NEEDED = ["ffmpeg.exe", "ffprobe.exe"]


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return str(path)


def listing(folder):
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


# -- zip ------------------------------------------------------------------------------------------
def test_zip_extracts_only_the_wanted_files_flattened(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"ffmpeg-7.1/bin/ffmpeg.exe": b"FF" * 100, "ffmpeg-7.1/bin/ffprobe.exe": b"FP" * 100,
                                      "ffmpeg-7.1/bin/ffplay.exe": b"PL", "ffmpeg-7.1/doc/readme.txt": b"hi", "ffmpeg-7.1/": b""})
    out = tmp_path / "bin"
    assert sorted(ax.extract_wanted(z, str(out), NEEDED)) == NEEDED
    assert listing(out) == NEEDED                                   # nothing else, no .part leftovers
    assert (out / "ffmpeg.exe").read_bytes() == b"FF" * 100


def test_a_missing_file_is_reported_as_missing(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"x/ffmpeg.exe": b"FF"})
    assert ax.extract_wanted(z, str(tmp_path / "bin"), NEEDED) == ["ffmpeg.exe"]


def test_an_empty_file_never_counts_as_extracted(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"x/ffmpeg.exe": b"", "x/ffprobe.exe": b"FP"})
    out = tmp_path / "bin"
    assert ax.extract_wanted(z, str(out), NEEDED) == ["ffprobe.exe"]
    assert listing(out) == ["ffprobe.exe"]


def test_names_match_case_insensitively_and_keep_the_wanted_spelling(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"x/FFMPEG.EXE": b"FF"})
    out = tmp_path / "bin"
    assert ax.extract_wanted(z, str(out), NEEDED) == ["ffmpeg.exe"] and listing(out) == ["ffmpeg.exe"]


def test_archive_paths_can_never_escape_the_target_folder(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"../../evil/ffmpeg.exe": b"EVIL", "..\..\ffprobe.exe": b"EVIL", "ok/ffmpeg.exe": b"FF"})
    out = tmp_path / "deep" / "bin"
    ax.extract_wanted(z, str(out), NEEDED)
    assert not (tmp_path / "evil").exists() and not (tmp_path / "ffprobe.exe").exists() and not (tmp_path.parent / "ffprobe.exe").exists()
    assert all(p.parent == out for p in out.iterdir())


def test_a_corrupt_archive_raises_instead_of_pretending(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"this is not a zip" * 20)
    with pytest.raises(zipfile.BadZipFile):
        ax.extract_wanted(str(bad), str(tmp_path / "bin"), NEEDED)


# -- 7z -------------------------------------------------------------------------------------------
def make_7z(path, members):
    py7zr = pytest.importorskip("py7zr")
    src = path.parent / "src_for_7z"
    for name, data in members.items():
        f = src / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    with py7zr.SevenZipFile(path, "w") as z:
        z.writeall(str(src), arcname="")
    return str(path)


def test_7z_extracts_only_the_wanted_files_and_leaves_no_scratch_folder(tmp_path):
    a = make_7z(tmp_path / "a.7z", {"ffmpeg-7.1/bin/ffmpeg.exe": b"FF" * 50, "ffmpeg-7.1/bin/ffprobe.exe": b"FP" * 50,
                                    "ffmpeg-7.1/include/big.h": b"H" * 1000, "ffmpeg-7.1/doc/x.txt": b"d"})
    out = tmp_path / "bin"
    assert sorted(ax.extract_wanted(a, str(out), NEEDED)) == NEEDED
    assert listing(out) == NEEDED                                   # no headers, no docs, no _extract_ folder
    assert (out / "ffprobe.exe").read_bytes() == b"FP" * 50


def test_7z_keeps_going_with_py7zr_when_there_is_no_system_bsdtar(tmp_path, monkeypatch):
    monkeypatch.setattr(ax, "find_bsdtar", lambda: None)
    a = make_7z(tmp_path / "a.7z", {"d/libmpv-2.dll": b"DLL" * 100, "d/other.txt": b"x"})
    out = tmp_path / "bin"
    assert ax.extract_wanted(a, str(out), ["libmpv-2.dll"]) == ["libmpv-2.dll"] and listing(out) == ["libmpv-2.dll"]


def test_a_7z_with_nothing_we_want_returns_nothing_and_cleans_up(tmp_path):
    a = make_7z(tmp_path / "a.7z", {"d/readme.txt": b"hello"})
    out = tmp_path / "bin"
    assert ax.extract_wanted(a, str(out), NEEDED) == [] and listing(out) == []


# -- which tar -------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="the Windows system tar")
def test_the_windows_system_tar_is_found_and_is_bsdtar():
    exe = ax.find_bsdtar()
    assert exe and exe.lower().endswith("tar.exe")
    assert "bsdtar" in subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.lower()


def test_a_gnu_tar_is_never_chosen(tmp_path, monkeypatch):
    """Git for Windows puts a GNU tar on PATH. It cannot read 7z, so it must not be picked."""
    fake = tmp_path / ("tar.cmd" if os.name == "nt" else "tar")
    fake.write_text("@echo tar (GNU tar) 1.35\r\n" if os.name == "nt" else "#!/bin/sh\necho 'tar (GNU tar) 1.35'\n")
    if os.name != "nt":
        fake.chmod(0o755)
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("windir", raising=False)
    monkeypatch.setattr(ax.shutil, "which", lambda name: str(fake) if name == "tar" else None)
    assert ax.find_bsdtar() is None
