import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import struct
import unittest
from unittest.mock import MagicMock
from src.core.media_inspector import (
    inspect_image_dimensions,
    inspect_mp4_metadata,
    format_resolution,
    format_bytes_display,
    format_duration_display,
    parse_drive_virus_size,
    probe_google_drive_stream,
)

class TestMediaInspector(unittest.TestCase):

    def test_inspect_png_dimensions(self):
        # PNG signature + IHDR chunk (width=1920, height=1080)
        data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 1920, 1080) + b"\x08\x06\x00\x00\x00"
        dim = inspect_image_dimensions(data)
        self.assertEqual(dim, (1920, 1080))

    def test_inspect_gif_dimensions(self):
        # GIF89a + width=640, height=480 (little endian)
        data = b"GIF89a" + struct.pack("<HH", 640, 480)
        dim = inspect_image_dimensions(data)
        self.assertEqual(dim, (640, 480))

    def test_inspect_jpeg_dimensions(self):
        # JPEG SOI (FF D8) + APP0 marker + SOF0 marker (FF C0) with width=3872, height=2592
        app0 = b"\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        sof0 = b"\xFF\xC0" + struct.pack(">HBHHB", 17, 8, 2592, 3872, 3)
        data = b"\xFF\xD8" + app0 + sof0 + b"\xFF\xD9"
        dim = inspect_image_dimensions(data)
        self.assertEqual(dim, (3872, 2592))

    def test_inspect_mp4_metadata(self):
        # Build minimal MP4 container with moov -> mvhd (timescale=1000, duration=49200 -> 49.2s)
        # mvhd box: size(4), 'mvhd'(4), ver/flags(4), ctime(4), mtime(4), timescale(4), duration(4)
        mvhd_payload = b"\x00\x00\x00\x00" + b"\x00"*8 + struct.pack(">II", 1000, 49200) + b"\x00"*80
        mvhd_box = struct.pack(">I4s", len(mvhd_payload) + 8, b"mvhd") + mvhd_payload
        
        # tkhd box: width=1920, height=1080 (16.16 fixed point)
        # For version 0: 84 bytes before fixed width/height
        tkhd_payload = b"\x00\x00\x00\x00" + b"\x00"*80 + struct.pack(">II", 1920 << 16, 1080 << 16)
        tkhd_box = struct.pack(">I4s", len(tkhd_payload) + 8, b"tkhd") + tkhd_payload

        moov_payload = mvhd_box + tkhd_box
        moov_box = struct.pack(">I4s", len(moov_payload) + 8, b"moov") + moov_payload
        data = b"\x00\x00\x00\x18ftypisom" + moov_box

        dur, w, h = inspect_mp4_metadata(data)
        self.assertAlmostEqual(dur, 49.2, places=1)
        self.assertEqual(w, 1920)
        self.assertEqual(h, 1080)

    def test_format_resolution(self):
        # Photos return exact dimensions
        self.assertEqual(format_resolution(3872, 2592, is_video=False), "3872x2592")
        self.assertEqual(format_resolution(1920, 1080, is_video=False), "1920x1080")

        # Videos return standard labels
        self.assertEqual(format_resolution(3840, 2160, is_video=True), "4K")
        self.assertEqual(format_resolution(2560, 1440, is_video=True), "1440p")
        self.assertEqual(format_resolution(1920, 1080, is_video=True), "1080p")
        self.assertEqual(format_resolution(1280, 720, is_video=True), "720p")
        self.assertEqual(format_resolution(640, 480, is_video=True), "480p")

    def test_parse_drive_virus_size(self):
        bytes_val, disp = parse_drive_virus_size("11G")
        self.assertEqual(disp, "11 GB")
        self.assertEqual(bytes_val, 11 * 1024**3)

        bytes_val, disp = parse_drive_virus_size("878M")
        self.assertEqual(disp, "878 MB")
        self.assertEqual(bytes_val, 878 * 1024**2)

        bytes_val, disp = parse_drive_virus_size("1.5G")
        self.assertEqual(disp, "1.5 GB")

    def test_format_bytes_display(self):
        self.assertEqual(format_bytes_display(2291970), "2.19 MB")
        self.assertEqual(format_bytes_display(2098911), "2 MB")
        self.assertEqual(format_bytes_display(11811160064), "11 GB")

    def test_format_duration_display(self):
        self.assertEqual(format_duration_display(49.2), "49s")
        self.assertEqual(format_duration_display(862.0), "14m 22s")
        self.assertEqual(format_duration_display(3665.0), "1h 1m")

    def test_probe_google_drive_stream_mock_206(self):
        # Mock HTTP 206 response for JPEG image
        mock_resp = MagicMock()
        mock_resp.status = 206
        mock_resp.headers = {
            "Content-Range": "bytes 0-131071/2291970",
            "Content-Disposition": 'attachment; filename="Crime Scene Photos - House (States exhibit 4).jpg"',
            "Content-Type": "image/jpeg",
        }
        # Fake JPEG header with 3872x2592
        sof0 = b"\xFF\xC0" + struct.pack(">HBHHB", 17, 8, 2592, 3872, 3)
        mock_resp.read.return_value = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof0
        mock_resp.__enter__.return_value = mock_resp

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        size_bytes, size_disp, resolution, dur_sec, dur_disp, filename = probe_google_drive_stream(
            "dummy_id", mock_opener
        )
        self.assertEqual(size_bytes, 2291970)
        self.assertEqual(size_disp, "2.19 MB")
        self.assertEqual(resolution, "3872x2592")
        self.assertEqual(filename, "Crime Scene Photos - House (States exhibit 4).jpg")

    def test_probe_google_drive_stream_mock_virus_page(self):
        # Mock HTTP 200 response with virus warning HTML (>100MB video)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {
            "Content-Type": "text/html; charset=utf-8",
        }
        html = """
        <html><body>
        <div id="uc-text">
        <span class="uc-name-size"><a href="/open?id=123">12-34567 Sam Rivera 1st Interview (REDACTED).mp4</a> (11G)</span>
        </div></body></html>
        """
        mock_resp.read.return_value = html.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        size_bytes, size_disp, resolution, dur_sec, dur_disp, filename = probe_google_drive_stream(
            "dummy_id", mock_opener
        )
        self.assertEqual(size_disp, "11 GB")
        self.assertEqual(filename, "12-34567 Sam Rivera 1st Interview (REDACTED).mp4")
