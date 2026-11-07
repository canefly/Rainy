import html as html_lib
import re
import struct
import urllib.request
from typing import Optional, Tuple

def format_bytes_display(size_bytes: Optional[int]) -> str:
    """Formats exact byte integer into human-readable string (e.g. 1548482012 -> 1.44 GB)."""
    if not size_bytes or size_bytes <= 0:
        return ""
    b = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0 or unit == "TB":
            if unit in ("B", "KB"):
                return f"{int(b)} {unit}"
            val_str = f"{b:.2f}".rstrip("0").rstrip(".")
            return f"{val_str} {unit}"
        b /= 1024.0
    return ""


def format_duration_display(duration_sec: Optional[float]) -> str:
    """Formats seconds into compact timecode string (e.g. 862.0 -> 14m 22s)."""
    if duration_sec is None or duration_sec <= 0:
        return ""
    mins = int(duration_sec // 60)
    secs = int(duration_sec % 60)
    hrs = mins // 60
    rem_mins = mins % 60
    if hrs > 0:
        return f"{hrs}h {rem_mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def inspect_image_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """
    Extracts (width, height) in pixels from the binary header of an image buffer.
    Supports JPEG, PNG, GIF, BMP, and WebP without downloading the full image.
    """
    if not data or len(data) < 10:
        return None

    # 1. PNG (\x89PNG\r\n\x1a\n)
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        if data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return w, h

    # 2. GIF (GIF87a / GIF89a)
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return w, h

    # 3. BMP (BM)
    if data.startswith(b"BM") and len(data) >= 26:
        header_size = struct.unpack("<I", data[14:18])[0]
        if header_size >= 40 and len(data) >= 14 + header_size:
            w, h = struct.unpack("<ii", data[18:26])
            return abs(w), abs(h)

    # 4. WebP (RIFF....WEBP)
    if data.startswith(b"RIFF") and len(data) >= 30 and data[8:12] == b"WEBP":
        vp8 = data[12:16]
        if vp8 == b"VP8 " and len(data) >= 30:
            if data[23:26] == b"\x9d\x01\x2a":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
        elif vp8 == b"VP8L" and len(data) >= 25:
            b0, b1, b2, b3 = data[21:25]
            w = 1 + (((b1 & 0x3F) << 8) | b0)
            h = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return w, h
        elif vp8 == b"VP8X" and len(data) >= 30:
            w = 1 + (data[24] | (data[25] << 8) | (data[26] << 16))
            h = 1 + (data[27] | (data[28] << 8) | (data[29] << 16))
            return w, h

    # 5. JPEG (\xFF\xD8)
    if data.startswith(b"\xFF\xD8"):
        idx = 2
        data_len = len(data)
        while idx < data_len - 9:
            if data[idx] == 0xFF:
                marker = data[idx + 1]
                # SOF markers: Baseline, Extended, Progressive, Lossless, Differential, etc.
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    try:
                        length, precision, height, width, comp = struct.unpack(">HBHHB", data[idx+2:idx+10])
                        if width > 0 and height > 0:
                            return width, height
                    except Exception:
                        break
                    idx += 10
                elif marker in (0xD8, 0xD9):  # SOI, EOI
                    idx += 2
                elif marker == 0x00:  # Byte stuffing
                    idx += 1
                else:
                    if idx + 4 > data_len:
                        break
                    seg_len = struct.unpack(">H", data[idx+2:idx+4])[0]
                    idx += 2 + seg_len
            else:
                idx += 1

    return None


def inspect_mp4_metadata(data: bytes) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    """
    Scans the initial bytes of an MP4/MOV container for 'moov' -> 'mvhd' (duration)
    and 'tkhd' (video track dimensions).
    """
    if not data or len(data) < 32:
        return None, None, None

    dur_sec: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None

    # Parse timescale and duration from mvhd box
    mvhd_idx = data.find(b"mvhd")
    if mvhd_idx != -1 and mvhd_idx >= 4:
        version = data[mvhd_idx + 4]
        if version == 0 and len(data) >= mvhd_idx + 24:
            ts, dur = struct.unpack(">II", data[mvhd_idx+16:mvhd_idx+24])
            if ts > 0:
                dur_sec = dur / ts
        elif version == 1 and len(data) >= mvhd_idx + 36:
            ts, dur = struct.unpack(">IQ", data[mvhd_idx+24:mvhd_idx+36])
            if ts > 0:
                dur_sec = dur / ts

    # Parse max track width and height from tkhd boxes
    tkhd_idx = 0
    max_w, max_h = 0, 0
    while True:
        tkhd_idx = data.find(b"tkhd", tkhd_idx + 1)
        if tkhd_idx == -1:
            break
        version = data[tkhd_idx + 4]
        offset = tkhd_idx + (88 if version == 0 else 100)
        if offset + 8 <= len(data):
            w_fixed, h_fixed = struct.unpack(">II", data[offset:offset+8])
            w, h = w_fixed >> 16, h_fixed >> 16
            if w > max_w:
                max_w = w
            if h > max_h:
                max_h = h

    if max_w > 0 and max_h > 0:
        width, height = max_w, max_h

    return dur_sec, width, height


def format_resolution(width: int, height: int, is_video: bool = False) -> str:
    """Formats width and height into standard broadcast or photo resolution labels."""
    if width <= 0 or height <= 0:
        return ""
    if not is_video:
        return f"{width}x{height}"

    h = height
    w = width
    if (w, h) in ((3840, 2160), (4096, 2160)) or h >= 2160:
        return "4K"
    elif (w, h) in ((2560, 1440),) or h >= 1440:
        return "1440p"
    elif (w, h) in ((1920, 1080), (1080, 1920)) or (h == 1080 and w >= 1920):
        return "1080p"
    elif (w, h) in ((1280, 720), (720, 1280)) or (h == 720 and w >= 1280):
        return "720p"
    elif h == 480 and w >= 640:
        return "480p"
    return f"{width}x{height}"


def parse_drive_virus_size(raw_size_str: str) -> Tuple[Optional[int], str]:
    """
    Parses Google Drive's virus scan warning size string like '11G', '878M', '1.5G', '450K'.
    Returns (approx_bytes, clean_display_string).
    """
    if not raw_size_str:
        return None, ""
    raw = raw_size_str.strip().upper()
    m = re.match(r"^([\d.]+)\s*([KMGT]?B?)$", raw)
    if not m:
        return None, raw_size_str
    try:
        num = float(m.group(1))
    except Exception:
        return None, raw_size_str

    unit = m.group(2)
    if "G" in unit:
        bytes_val = int(num * 1024**3)
        disp = f"{num:.1f} GB" if (num % 1 != 0) else f"{int(num)} GB"
        return bytes_val, disp
    elif "M" in unit:
        bytes_val = int(num * 1024**2)
        disp = f"{num:.1f} MB" if (num % 1 != 0) else f"{int(num)} MB"
        return bytes_val, disp
    elif "K" in unit:
        bytes_val = int(num * 1024)
        disp = f"{int(num)} KB"
        return bytes_val, disp
    elif "T" in unit:
        bytes_val = int(num * 1024**4)
        disp = f"{num:.1f} TB" if (num % 1 != 0) else f"{int(num)} TB"
        return bytes_val, disp
    else:
        return int(num), f"{int(num)} B"


def probe_google_drive_stream(
    drive_id: str,
    opener: urllib.request.OpenerDirector,
    timeout: float = 6.0,
    range_bytes: int = 131072,
) -> Tuple[Optional[int], str, str, Optional[float], str, Optional[str]]:
    """
    Performs a lightweight 128KB HTTP Range probe to Google Drive's download endpoint:
    https://drive.google.com/uc?export=download&id={drive_id}

    Returns:
    (size_bytes, size_display, resolution, duration_seconds, duration_display, resolved_filename)
    """
    url = f"https://drive.google.com/uc?export=download&id={drive_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Range": f"bytes=0-{range_bytes - 1}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
    )

    try:
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200) or 200
            cr = resp.headers.get("Content-Range") or ""
            cd = resp.headers.get("Content-Disposition") or ""
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read()

            resolved_name = None
            m_cd = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.IGNORECASE)
            if m_cd:
                resolved_name = m_cd.group(1).strip()

            size_bytes: Optional[int] = None
            size_disp = ""
            resolution = ""
            dur_sec: Optional[float] = None
            dur_disp = ""

            # Case A: Range request successful (Files <= 100MB)
            if status == 206 and cr:
                m_cr = re.search(r"/(\d+)$", cr)
                if m_cr:
                    size_bytes = int(m_cr.group(1))
                    size_disp = format_bytes_display(size_bytes)

                # 1. Image Dimensions
                img_dim = inspect_image_dimensions(raw)
                if img_dim:
                    resolution = format_resolution(img_dim[0], img_dim[1], is_video=False)
                else:
                    # 2. Video / Audio metadata
                    d, w, h = inspect_mp4_metadata(raw)
                    if d:
                        dur_sec = d
                        dur_disp = format_duration_display(dur_sec)
                    if w and h:
                        resolution = format_resolution(w, h, is_video=True)

                return size_bytes, size_disp, resolution, dur_sec, dur_disp, resolved_name

            # Case B: Large file virus scan warning page (Files > 100MB)
            elif "text/html" in ctype:
                html = raw.decode("utf-8", errors="replace")
                m_warn = re.search(r"uc-name-size.*?>([^<]+)</a>\s*\(([^)]+)\)", html)
                if m_warn:
                    resolved_name = html_lib.unescape(m_warn.group(1).strip())
                    raw_sz = m_warn.group(2).strip()
                    size_bytes, size_disp = parse_drive_virus_size(raw_sz)

                    # Check if filename hints at resolution
                    if re.search(r"\b(?:4k|2160p)\b", resolved_name, re.IGNORECASE):
                        resolution = "4K"
                    elif re.search(r"\b1440p\b", resolved_name, re.IGNORECASE):
                        resolution = "1440p"
                    elif re.search(r"\b(?:1080p|fhd)\b", resolved_name, re.IGNORECASE):
                        resolution = "1080p"
                    elif re.search(r"\b(?:720p|hd)\b", resolved_name, re.IGNORECASE):
                        resolution = "720p"

                    return size_bytes, size_disp, resolution, dur_sec, dur_disp, resolved_name

    except Exception:
        pass

    return None, "", "", None, "", None
