import os
import re
import uuid
import hashlib
import colorsys
import html
from typing import Tuple, List, Dict, Optional, Any
from PySide6.QtCore import QRunnable, QObject, Signal, QUrl
from docx import Document
from docx.text.run import Run
from docx.shared import RGBColor, Pt
from docx.oxml import OxmlElement

from .models import DocumentState, ScriptItem, EditorNote
from .database import db

# Embedded script photos live beside Transcripts/ and Projects/ so files dragged into an
# NLE keep a stable path (the OS temp folder gets cleaned and the media goes offline).
PHOTOS_TEMP_DIR = os.environ.get("KNOT_PHOTOS_DIR") or os.path.join(
    os.path.expanduser("~"), "Documents", "Rainy", "Photos Temp"
)
SCRIPT_IMAGE_MAX_WIDTH = 360


def script_photos_dir(file_path: str) -> str:
    """Folder holding the photos extracted from one script: Photos Temp/<script name>/."""
    stem = os.path.splitext(os.path.basename(file_path))[0]
    safe = re.sub(r'[<>:"/\\|?*]', '_', stem).strip(" .") or "script"
    return os.path.join(PHOTOS_TEMP_DIR, safe)


def _save_script_photo(photos_dir: str, img_bytes: bytes, ext: str) -> str:
    """Writes one embedded photo under a content-derived name, so re-parsing the same
    script reuses the file instead of piling up copies or changing a path an NLE links to."""
    digest = hashlib.sha1(img_bytes).hexdigest()[:10]
    prefix = os.path.basename(photos_dir)
    img_path = os.path.join(photos_dir, f"{prefix}_{digest}.{ext}")
    if not (os.path.isfile(img_path) and os.path.getsize(img_path) == len(img_bytes)):
        os.makedirs(photos_dir, exist_ok=True)
        with open(img_path, 'wb') as f_img:
            f_img.write(img_bytes)
    return os.path.abspath(img_path)


def _image_display_width(img_path: str) -> int:
    """Width to render a script photo at (natural width, capped), or 0 if Qt can't decode it."""
    try:
        from PySide6.QtGui import QImageReader
        natural_w = QImageReader(img_path).size().width()
    except Exception:
        natural_w = -1
    if natural_w <= 0:
        return 0
    return min(natural_w, SCRIPT_IMAGE_MAX_WIDTH)


# Photo formats worth trying inline for footage-bound links (anything Qt can't decode is skipped).
_LINKED_PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg",
    ".heic", ".heif", ".avif",
}


def _linked_photo_paths(block: Dict[str, Any], linked_footage: Optional[Dict[str, str]]) -> List[str]:
    """Local photo files bound (via footage linking) to the links inside one script block."""
    if not linked_footage:
        return []
    paths: List[str] = []
    for s in block.get("spans", []):
        link = s.get("link")
        if not link or link == "#":
            continue
        local = linked_footage.get(normalize_link(link)) or linked_footage.get(link)
        if not local or local in paths:
            continue
        if os.path.splitext(local)[1].lower() in _LINKED_PHOTO_EXTENSIONS:
            paths.append(local)
    return paths


def _build_block_images_html(image_paths: List[str], has_text: bool) -> str:
    """Inline <img> tags for a block's photos. They stay inside the block's own <p> because
    ScriptViewer maps QTextBlocks to script blocks one-to-one."""
    tags = []
    seen = set()
    for img_path in image_paths or []:
        if not img_path or img_path in seen or not os.path.isfile(img_path):
            continue
        seen.add(img_path)
        width = _image_display_width(img_path)
        if width <= 0:
            continue
        src = QUrl.fromLocalFile(img_path).toString()
        tags.append(f"<img src='{html.escape(src, quote=True)}' width='{width}'/>")
    if not tags:
        return ""
    return ("<br/>" if has_text else "") + "<br/>".join(tags)


def compute_file_hash(file_path: str) -> str:
    """Computes SHA256 of file for stable document signature."""
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def normalize_link(link: str) -> str:
    if not link or link == '#':
        return link

    # YouTube specific normalization: extract 11-char video ID and format to clean youtu.be link
    youtube_patterns = [
        r'(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/watch\?.*v=([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/embed\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/v\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/shorts\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?youtu\.be\/([a-zA-Z0-9_-]{11})'
    ]

    yt_id = None
    for pattern in youtube_patterns:
        match = re.search(pattern, link)
        if match:
            yt_id = match.group(1)
            break

    if yt_id:
        return f"https://www.youtube.com/watch?v={yt_id}"

    # Remove fragments like #t=123
    clean = re.sub(r'#.*$', '', link)

    # Google Drive specific: remove /view, /edit, and any following query parameters
    if 'drive.google.com' in clean:
        clean = re.sub(r'/(?:view|edit|preview|copy|sharing).*$', '', clean)
        if '/file/d/' in clean:
            clean = re.sub(r'\?.*$', '', clean)
        clean = re.sub(r'[?&]\.[\d]+$', '', clean)

    # Remove common timestamp parameters for other links
    clean = re.sub(r'([?&])(?:t|time|start|timestamp|at)=\d+&?', r'\1', clean)
    clean = clean.rstrip('?&')
    return clean.strip()

def process_time_raw(raw: str) -> Tuple[str, int]:
    """Parses raw timestamp into HH:MM:SS:FF string and total seconds."""
    if not raw:
        return "00:00:00:00", 0
    parts = raw.replace('-', '').replace('–', '').replace('—', '').replace('(', '').replace(')', '').strip().split(':')
    if len(parts) == 2:
        m, s = parts
        h = "00"
        f = "00"
    elif len(parts) == 3:
        h, m, s = parts
        f = "00"
    elif len(parts) == 4:
        h, m, s, f = parts
    else:
        return "00:00:00:00", 0

    h = h.zfill(2)
    m = m.zfill(2)
    s = s.zfill(2)
    f = f.zfill(2)

    total_seconds = int(h) * 3600 + int(m) * 60 + int(s)
    return f"{h}:{m}:{s}:{f}", total_seconds

def format_smpte(seconds: float, fps: float = 23.976) -> str:
    """Formats floating-point seconds into SMPTE timecode string HH:MM:SS:FF."""
    if seconds <= 0:
        return "00:00:00:00"
    if fps <= 0 or fps > 120.0:
        fps = 23.976
    base_fps = max(1, int(round(fps)))
    total_frames = int(round(seconds * fps))
    ff = total_frames % base_fps
    total_sec = total_frames // base_fps
    ss = total_sec % 60
    total_min = total_sec // 60
    mm = total_min % 60
    hh = total_min // 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

def parse_time_single(raw: str) -> Tuple[str, float, bool]:
    """Lenient parser for standalone timestamp string (supports ; and . typos)."""
    cleaned = re.sub(r'[()—–\-]', '', raw).strip()
    has_typo = any(char in raw for char in [';', '.', ' '])
    parts = re.split(r'[:;.\s]+', cleaned)
    parts = [p.zfill(2) for p in parts if p.isdigit()]

    if len(parts) == 1:
        s = parts[0]
        h, m, f = "00", "00", "00"
    elif len(parts) == 2:
        m, s = parts
        h, f = "00", "00"
    elif len(parts) == 3:
        h, m, s = parts
        f = "00"
    elif len(parts) >= 4:
        h, m, s, f = parts[:4]
    else:
        return "00:00:00:00", 0.0, True

    h = h.zfill(2)
    m = m.zfill(2)
    s = s.zfill(2)
    f = f.zfill(2)
    total_sec = int(h) * 3600 + int(m) * 60 + int(s) + int(f) / 30.0
    return f"{h}:{m}:{s}:{f}", total_sec, has_typo

def process_time_span(raw: str) -> Tuple[str, float, str, float, float, str, bool, str]:
    """Lenient processor for start-end spans."""
    if not raw:
        return "00:00:00:00", 0.0, "00:00:00:00", 0.0, 0.0, "--", False, ""

    range_match = re.split(r'\s*(?:[-–—]|to)\s*', raw.strip())
    start_tc, start_sec, typo_start = parse_time_single(range_match[0])

    if len(range_match) > 1 and range_match[1].strip():
        end_tc, end_sec, typo_end = parse_time_single(range_match[1])
    else:
        end_tc, end_sec, typo_end = start_tc, start_sec, False

    dur_sec = max(0.0, end_sec - start_sec)
    if dur_sec >= 60:
        mins = int(dur_sec // 60)
        secs = int(dur_sec % 60)
        dur_display = f"{mins}m {secs}s" if secs > 0 else f"{mins}m"
    elif dur_sec > 0:
        dur_display = f"{int(dur_sec)}s"
    else:
        dur_display = "--"

    has_warning = typo_start or typo_end
    warning_reason = f"Normalized from '{raw}'" if has_warning else ""
    return start_tc, start_sec, end_tc, end_sec, dur_sec, dur_display, has_warning, warning_reason

def get_font_attr(run, paragraph, attr_name):
    """Retrieves font attribute from run or cascading paragraph styles."""
    if run.font:
        val = getattr(run.font, attr_name, None)
        if val is not None:
            return val

    current_style = paragraph.style
    while current_style:
        if current_style.font:
            val = getattr(current_style.font, attr_name, None)
            if val is not None:
                return val
        current_style = current_style.base_style
    return None

def generate_link_colors(links: List[str]) -> Dict[str, str]:
    """Generates distinct, evenly distributed HSL colors for unique links."""
    colors: Dict[str, str] = {}
    if not links:
        return colors
    count = len(links)
    for i, link in enumerate(links):
        hue = (i * (360.0 / count)) % 360.0
        r, g, b = colorsys.hls_to_rgb(hue / 360.0, 0.60, 0.75)
        colors[link] = f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
    return colors

def extract_page_geometry(doc: Document) -> Dict[str, float]:
    """Extracts page size and margin dimensions in points from the first DOCX section."""
    geom = {
        "width_pt": 612.0,
        "height_pt": 792.0,
        "margin_top_pt": 72.0,
        "margin_bottom_pt": 72.0,
        "margin_left_pt": 72.0,
        "margin_right_pt": 72.0,
    }
    try:
        if doc.sections:
            sec = doc.sections[0]
            if sec.page_width is not None:
                geom["width_pt"] = float(sec.page_width.pt)
            if sec.page_height is not None:
                geom["height_pt"] = float(sec.page_height.pt)
            if sec.top_margin is not None:
                geom["margin_top_pt"] = float(sec.top_margin.pt)
            if sec.bottom_margin is not None:
                geom["margin_bottom_pt"] = float(sec.bottom_margin.pt)
            if sec.left_margin is not None:
                geom["margin_left_pt"] = float(sec.left_margin.pt)
            if sec.right_margin is not None:
                geom["margin_right_pt"] = float(sec.right_margin.pt)
    except Exception:
        pass
    return geom


HIGHLIGHT_COLOR_MAP = {
    'yellow': '#fef08a', 'green': '#bbf7d0', 'cyan': '#a5f3fc',
    'magenta': '#fbcfe8', 'blue': '#bfdbfe', 'red': '#fecaca',
    'darkblue': '#1e40af', 'darkcyan': '#0e7490', 'darkgreen': '#15803d',
    'darkmagenta': '#86198f', 'darkred': '#991b1b', 'darkyellow': '#a16207',
    'darkgray': '#475569', 'lightgray': '#e2e8f0', 'black': '#0f172a'
}


def extract_run_bg_color(run, p) -> Optional[str]:
    """Extracts background fill color from run shading, highlight, or paragraph shading."""
    try:
        # 1. Run-level XML shading fill
        fills = run._r.xpath('.//w:shd/@w:fill')
        if fills:
            f = str(fills[0]).strip().lstrip('#')
            if len(f) == 6 and f.lower() not in ('auto', 'ffffff', '000000', 'none'):
                return f"#{f.lower()}"
        # 2. Run-level highlight color
        hl = run.font.highlight_color
        if hl is not None:
            hl_str = str(hl).split()[-1].lower() if hasattr(hl, '__str__') else ''
            if hl_str in HIGHLIGHT_COLOR_MAP:
                return HIGHLIGHT_COLOR_MAP[hl_str]
        hl_vals = run._r.xpath('.//w:highlight/@w:val')
        if hl_vals:
            v = str(hl_vals[0]).strip().lower()
            if v in HIGHLIGHT_COLOR_MAP:
                return HIGHLIGHT_COLOR_MAP[v]
        # 3. Paragraph-level XML shading fill fallback
        p_fills = p._p.xpath('./w:pPr/w:shd/@w:fill')
        if p_fills:
            pf = str(p_fills[0]).strip().lstrip('#')
            if len(pf) == 6 and pf.lower() not in ('auto', 'ffffff', '000000', 'none'):
                return f"#{pf.lower()}"
    except Exception:
        pass
    return None


def _adapt_bg_color_for_surface(hex_color: str, light_paper: bool) -> str:
    """Ensures background highlight colors remain readable and contrast well on both light and dark paper."""
    if not hex_color:
        return hex_color
    clean = hex_color.lstrip("#")
    if len(clean) == 6:
        try:
            r = int(clean[0:2], 16) / 255.0
            g = int(clean[2:4], 16) / 255.0
            b = int(clean[4:6], 16) / 255.0
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            if light_paper:
                # White paper: docx pastel highlights look authentic as-is
                return f"#{clean.lower()}"
            else:
                # Dark paper: darken pastel highlight so white body text remains crisp and readable
                if l > 0.50:
                    target_l = 0.22
                    target_s = min(0.75, s * 1.4 + 0.15)
                    nr, ng, nb = colorsys.hls_to_rgb(h, target_l, target_s)
                    return f"#{int(nr * 255):02x}{int(ng * 255):02x}{int(nb * 255):02x}"
        except Exception:
            pass
    return hex_color


def _adapt_color_for_surface(hex_color: str, light_paper: bool) -> str:
    """Ensures text colors remain readable on both Classic White Paper and Dark Paper/Surfaces."""
    if not hex_color:
        return hex_color
    clean = hex_color.lstrip("#")
    if len(clean) == 6:
        try:
            r = int(clean[0:2], 16) / 255.0
            g = int(clean[2:4], 16) / 255.0
            b = int(clean[4:6], 16) / 255.0
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            if light_paper and l > 0.55:
                # Darken bright/light colors (cyan, light blue, yellow) on white paper so text is crisp and readable
                new_l = max(0.18, 0.28 if s < 0.2 else 0.32)
                nr, ng, nb = colorsys.hls_to_rgb(h, new_l, min(1.0, s * 1.1))
                return f"#{int(nr * 255):02x}{int(ng * 255):02x}{int(nb * 255):02x}"
            elif not light_paper and l < 0.35:
                # Lighten pure black / very dark DOCX run colors so they are crisp on dark surfaces
                new_l = min(0.92, 0.82 if s < 0.2 else 0.72)
                nr, ng, nb = colorsys.hls_to_rgb(h, new_l, s)
                return f"#{int(nr * 255):02x}{int(ng * 255):02x}{int(nb * 255):02x}"
        except ValueError:
            pass
    return hex_color


def render_span_text(text: str, light_paper: bool = False, block_id: Optional[int] = None) -> str:
    """Highlights @editor triggers and timestamps in text with high-contrast readable colors."""
    editor_col = "#9a3412" if light_paper else "#facc15"  # Deep burnt amber on white paper vs vibrant yellow on dark
    ts_col = "#0369a1" if light_paper else "#38bdf8"      # Deep readable ocean blue on white paper vs sky cyan on dark

    # 1. Highlight @editor triggers (Yellow / Amber)
    text = re.sub(
        r'(@?editor\s*[:;]?)', 
        f"<span style='color: {editor_col}; font-weight: 700;'>\\1</span>", 
        text, 
        flags=re.IGNORECASE
    )

    # 2. Highlight timestamps (Blue Underlined)
    time_pattern = re.compile(
        r'(?:\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?\s*(?:[-–—]|to)\s*\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?)|(?:\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?)',
        re.IGNORECASE
    )

    def repl_time(m):
        raw = m.group(0)
        if not raw.strip() or ':' not in raw:
            return raw
        href = f"time:{raw}?sync={block_id}" if block_id is not None else f"time:{raw}"
        return f"<a href='{href}' style='color: {ts_col}; text-decoration: underline; font-weight: 700; cursor: pointer;' data-time='{raw}'>{raw}</a>"

    text = time_pattern.sub(repl_time, text)
    return text


def build_raw_html(
    blocks: List[Dict[str, Any]],
    link_colors: Dict[str, str],
    light_paper: bool = False,
    view_mode: str = "reading",
    vo_block_diffs: Optional[Dict[str, Dict[str, Any]]] = None,
    linked_footage: Optional[Dict[str, str]] = None,
) -> str:
    """Builds styled HTML for ScriptViewer preserving DOCX font colors, highlights, formatting, and headings."""
    html_parts = []
    diffs_map = vo_block_diffs or {}
    is_doc = (view_mode == "document")
    body_text_col = "#0f172a" if light_paper else "#f1f5f9"

    # In Document Mode: respect MS Word styling (classic dark heading on light paper, classic blue links)
    if is_doc:
        heading_col = "#0f172a" if light_paper else "#f1f5f9"
        doc_link_col = "#1155cc" if light_paper else "#60a5fa"
    else:
        heading_col = "#15803d" if light_paper else "#4ade80"
        doc_link_col = "#0284c7" if light_paper else "#38bdf8"

    for b in blocks:
        block_id = b["id"]
        is_header = b["isHeader"]
        header_level = b["headerLevel"] or 1

        span_htmls = []
        for s in b.get("spans", []):
            txt = s["text"]
            escaped_txt = html.escape(txt)
            styled_txt = render_span_text(escaped_txt, light_paper=light_paper, block_id=block_id)

            style_props = []
            if s.get("bg_color"):
                bg_col = _adapt_bg_color_for_surface(s["bg_color"], light_paper)
                style_props.append(f"background-color: {bg_col}; border-radius: 2px; padding: 1px 2px;")
            if s.get("color"):
                col = _adapt_color_for_surface(s["color"], light_paper)
                style_props.append(f"color: {col}")
            elif s.get("bg_color"):
                # Explicitly enforce text color inside highlighted runs so light/dark surfaces don't default to wrong contrast
                default_highlight_text_col = "#0f172a" if light_paper else "#f1f5f9"
                style_props.append(f"color: {default_highlight_text_col}")
            else:
                style_props.append(f"color: {body_text_col}")
            if s.get("bold"):
                style_props.append("font-weight: bold")
            if s.get("italic"):
                style_props.append("font-style: italic")
            if s.get("size"):
                pt_match = re.search(r'([\d.]+)pt', s["size"])
                if pt_match:
                    pt_val = float(pt_match.group(1))
                    if is_doc:
                        style_props.append(f"font-size: {pt_val:.1f}pt")
                    else:
                        em_val = round(pt_val / 11.0, 2)
                        style_props.append(f"font-size: {em_val}em")

            style_str = f" style='{'; '.join(style_props)}'" if style_props else ""

            if s.get("link"):
                norm = normalize_link(s["link"])
                if is_doc:
                    link_col = doc_link_col
                else:
                    raw_link_col = link_colors.get(norm, doc_link_col)
                    link_col = _adapt_color_for_surface(raw_link_col, light_paper)
                span_htmls.append(f"<a href='{s['link']}' style='color: {link_col}; text-decoration: underline; font-weight: bold;'>{styled_txt}</a>")
            else:
                if style_props:
                    span_htmls.append(f"<span{style_str}>{styled_txt}</span>")
                else:
                    span_htmls.append(styled_txt)

        inner_content = "".join(span_htmls)
        # Photos embedded in the .docx, then photos bound to this block's links via footage linking.
        block_photos = list(b.get("images") or []) + _linked_photo_paths(b, linked_footage)
        images_html = _build_block_images_html(block_photos, has_text=bool(inner_content.strip()))
        inner_content += images_html

        if not inner_content.strip():
            # Preserve blank paragraph lines for authentic DOCX layout
            p_style = "margin-top: 0px; margin-bottom: 7px; line-height: 1.15; min-height: 12.65pt;" if is_doc else "margin: 4px 0; min-height: 1em;"
            html_parts.append(f"<p id='block-{block_id}' style='{p_style}'><a name='block-{block_id}'></a>&nbsp;</p>")
            continue

        if is_header:
            if is_doc:
                # In Document Mode: authentic MS Word heading with before/after spacing
                html_parts.append(f"<p id='block-{block_id}' style='margin-top: 10px; margin-bottom: 7px; line-height: 1.15; font-weight: bold; color: {heading_col};'><a name='block-{block_id}'></a>{inner_content}</p>")
            else:
                if header_level == 1:
                    tag = f"<h1 id='block-{block_id}' style='margin: 18px 0 8px 0; font-size: 1.4em; font-weight: 800; color: {heading_col};'><a name='block-{block_id}'></a>{inner_content}</h1>"
                elif header_level == 2:
                    tag = f"<h2 id='block-{block_id}' style='margin: 14px 0 6px 0; font-size: 1.25em; font-weight: 700; color: {heading_col};'><a name='block-{block_id}'></a>{inner_content}</h2>"
                elif header_level == 3:
                    tag = f"<h3 id='block-{block_id}' style='margin: 12px 0 4px 0; font-size: 1.15em; font-weight: 700; color: {heading_col};'><a name='block-{block_id}'></a>{inner_content}</h3>"
                else:
                    tag = f"<h4 id='block-{block_id}' style='margin: 10px 0 4px 0; font-size: 1.05em; font-weight: 700; color: {heading_col};'><a name='block-{block_id}'></a>{inner_content}</h4>"
                html_parts.append(tag)
        else:
            diff_entry = diffs_map.get(str(block_id)) or diffs_map.get(block_id)
            vo_callout_html = ""
            p_extra_border = ""
            if diff_entry and isinstance(diff_entry, dict):
                m_type = (diff_entry.get("match_type") or "EXACT").upper()
                vo_txt = html.escape(str(diff_entry.get("vo_actual_text") or "").strip())
                s_sec = float(diff_entry.get("vo_start_sec", 0.0) or 0.0)
                e_sec = float(diff_entry.get("vo_end_sec", 0.0) or 0.0)
                tc_badge = f"{int(s_sec)//60:02d}:{s_sec%60:04.1f} - {int(e_sec)//60:02d}:{e_sec%60:04.1f}" if e_sec > 0 else "VO"

                if m_type in ("MODIFIED", "ADLIB", "RETAKE_CUT") and vo_txt:
                    badge_col = "#c084fc" if m_type == "ADLIB" else ("#f87171" if m_type == "RETAKE_CUT" else "#fbbf24")
                    badge_bg = "rgba(192, 132, 252, 0.14)" if m_type == "ADLIB" else ("rgba(248, 113, 113, 0.14)" if m_type == "RETAKE_CUT" else "rgba(251, 191, 36, 0.14)")
                    p_extra_border = f"border-left: 3px solid {badge_col}; padding-left: 6px;"
                    vo_callout_html = (
                        f"<br/><span style='background-color: {badge_bg}; color: {badge_col}; "
                        f"font-size: 0.88em; font-weight: 700; padding: 1px 6px; border-radius: 4px;'>"
                        f"[VO {m_type} {tc_badge}]: &ldquo;{vo_txt}&rdquo;</span>"
                    )
                elif m_type == "EXACT":
                    p_extra_border = "border-left: 3px solid #34d399; padding-left: 6px;"

            p_style = (
                f"margin-top: 0px; margin-bottom: 7px; line-height: 1.15; color: {body_text_col}; {p_extra_border}"
                if is_doc
                else f"margin: 4px 0; line-height: 1.4; color: {body_text_col}; {p_extra_border}"
            )
            if images_html:
                # Proportional line-height would scale with the photo's height and leave a huge
                # gap under it; use a fixed gap between lines for photo paragraphs instead.
                p_style = re.sub(
                    r'line-height: [\d.]+;', 'line-height: 6px; -qt-line-height-type: line-distance;', p_style, count=1
                )
            html_parts.append(f"<p id='block-{block_id}' style='{p_style}'><a name='block-{block_id}'></a>{inner_content}{vo_callout_html}</p>")

    return "".join(html_parts)

def _count_lifetime_stats_once(file_name: str, doc: DocumentState) -> None:
    """The Data Vault's fun lifetime numbers (parse time, total cuts, duration).

    A script is counted the first time it is opened, by its file name, so opening it again or editing and re-importing
    it never adds it twice. A counter must never stop a script from opening, so any failure here is swallowed."""
    try:
        import json
        seen = json.loads(db.get_setting("stats_counted_scripts", "[]") or "[]")
        key = (file_name or "").strip().lower()
        if not key or key in seen:
            return
        db.increment_stats(
            duration_seconds=int(sum(max(0.0, float(i.duration_seconds or 0)) for i in doc.items)),
            aroll_count=len(doc.items),
        )
        seen.append(key)
        db.set_setting("stats_counted_scripts", json.dumps(seen[-2000:]))
    except Exception:  # noqa: BLE001
        pass


def parse_docx_file(file_path: str, progress_callback=None, previous_doc: Optional[DocumentState] = None) -> DocumentState:
    """
    Parses a DOCX file using the authentic Python sidecar engine logic from the original React app.
    Extracts all run styling, font colors, headings, images, and correctly associates timestamps and links.
    """
    file_sig = compute_file_hash(file_path)
    file_name = os.path.basename(file_path)

    existing_doc = previous_doc or db.get_document_by_signature(file_sig)
    overrides = db.get_overrides(file_sig)

    if progress_callback:
        progress_callback(10, "Opening document...")

    doc = Document(file_path)
    page_geometry = extract_page_geometry(doc)
    photos_dir = script_photos_dir(file_path)

    lines = []
    total_paragraphs = len(doc.paragraphs)

    for i, p in enumerate(doc.paragraphs):
        if progress_callback and total_paragraphs > 0 and i % 15 == 0:
            pct = 15 + int((i / total_paragraphs) * 50)
            progress_callback(pct, f"Parsing paragraph {i + 1} of {total_paragraphs}...")

        text = p.text.strip()

        # Check for images before skipping empty paragraph
        has_images = False
        if p._p.xml:
            for rel in doc.part.rels.values():
                if "image" in rel.reltype and (f'"{rel.rId}"' in p._p.xml or f"'{rel.rId}'" in p._p.xml):
                    has_images = True
                    break

        if not text and not has_images:
            lines.append({
                "text": "",
                "spans": [],
                "hiddenLinks": [],
                "extractedImages": [],
                "origIndex": i,
                "isEditorLine": False,
                "isHeader": False,
                "headerLevel": None
            })
            continue

        spans = []
        runs_and_links = []
        for child in p._p:
            if child.tag.endswith('}r'):
                runs_and_links.append((Run(child, p), None))
            elif child.tag.endswith('}hyperlink'):
                rId = child.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                url = None
                if rId and rId in doc.part.rels:
                    url = doc.part.rels[rId].target_ref
                for sub_child in child:
                    if sub_child.tag.endswith('}r'):
                        runs_and_links.append((Run(sub_child, p), url))

        for run, link in runs_and_links:
            if not run.text:
                continue

            color_obj = get_font_attr(run, p, 'color')
            color = str(color_obj.rgb) if color_obj and hasattr(color_obj, 'rgb') and color_obj.rgb else None

            bold = get_font_attr(run, p, 'bold')
            if bold is None and run.bold:
                bold = run.bold

            italic = get_font_attr(run, p, 'italic')
            if italic is None and run.italic:
                italic = run.italic

            size_obj = get_font_attr(run, p, 'size')
            size = f"{size_obj.pt}pt" if size_obj and hasattr(size_obj, 'pt') and size_obj.pt is not None else None

            bg_color = extract_run_bg_color(run, p)

            spans.append({
                "text": run.text,
                "color": f"#{color}" if color else None,
                "bg_color": bg_color,
                "bold": bool(bold),
                "italic": bool(italic),
                "size": size,
                "link": link
            })

        # Merge adjacent spans with identical styling
        merged_spans = []
        for span in spans:
            if not merged_spans:
                merged_spans.append(span)
            else:
                last_span = merged_spans[-1]
                if (last_span["color"] == span["color"] and
                    last_span.get("bg_color") == span.get("bg_color") and
                    last_span["bold"] == span["bold"] and
                    last_span["italic"] == span["italic"] and
                    last_span["size"] == span["size"] and
                    last_span["link"] == span["link"]):
                    last_span["text"] += span["text"]
                else:
                    merged_spans.append(span)
        spans = merged_spans

        # Determine genuine document headings vs. accidental heading styles on body paragraphs
        is_header = False
        header_level = None

        if text and not bool(re.search(r'@\w+', text, re.IGNORECASE)):
            clean_text = text.strip()
            style_name = p.style.name if (p.style and p.style.name) else ""
            is_heading_style = style_name.startswith('Heading') or 'Title' in style_name

            # Check if line is dialogue/speaker line e.g. "D left:", "Sam:", "Jordan:"
            is_dialogue = bool(re.match(r'^[A-Za-z0-9\s/–—_-]{1,25}:', clean_text)) and not clean_text.upper().startswith(
                ('INTRO:', 'OUTRO:', 'CASE ', 'ACT ', 'HOOK:', 'BODY:', 'TITLE:')
            )
            is_url_line = clean_text.startswith(('http://', 'https://'))

            if not is_dialogue and not is_url_line:
                sizes = []
                for s in spans:
                    if s.get("size") and s["size"].endswith("pt"):
                        try:
                            sizes.append(float(s["size"].replace("pt", "")))
                        except ValueError:
                            pass
                max_sz = max(sizes) if sizes else None
                has_large_font = max_sz is not None and max_sz >= 14.0
                all_small_font = bool(sizes and all(sz <= 12.0 for sz in sizes))
                has_bold = any(s.get("bold") for s in spans if s.get("text", "").strip())
                all_bold = len(spans) > 0 and all(s.get("bold") for s in spans if s.get("text", "").strip())

                section_kw_match = bool(re.match(
                    r'^(\d+/\d+\.|\b(case|part|scene|act|chapter|level|lvl|tab)\b\s*\d*|intro:|outro:|hook:|body:|title:)',
                    clean_text, re.IGNORECASE
                ))

                if is_heading_style:
                    # In Google Docs, body text or URLs typed inside Heading 2 have explicit 11pt unbolded font
                    if (all_small_font and not has_bold and len(clean_text) > 40) or (
                        len(clean_text) > 85 and clean_text.endswith(('.', '?', '!', ';')) and not section_kw_match
                    ):
                        is_header = False
                    else:
                        is_header = True
                        m = re.search(r'\d+', style_name)
                        if m:
                            header_level = int(m.group())
                        elif 'Subtitle' in style_name:
                            header_level = 2
                        else:
                            header_level = 1
                elif section_kw_match:
                    is_header = True
                    header_level = 1 if len(clean_text) < 40 else 2
                elif len(clean_text) <= 80 and not clean_text.endswith(('.', '?', '!', ';')):
                    if has_large_font:
                        is_header = True
                        header_level = 1
                    elif all_bold and len(clean_text) < 40:
                        is_header = True
                        header_level = 2

        extracted_images = []
        hidden_links = []
        if p._p.xml:
            for rel in doc.part.rels.values():
                if "hyperlink" in rel.reltype and (f'"{rel.rId}"' in p._p.xml or f"'{rel.rId}'" in p._p.xml):
                    hidden_links.append(rel.target_ref)
                elif "image" in rel.reltype and (f'"{rel.rId}"' in p._p.xml or f"'{rel.rId}'" in p._p.xml):
                    try:
                        img_part = rel.target_part
                        img_bytes = img_part.blob
                        img_ext = img_part.content_type.split('/')[-1] if img_part.content_type else 'png'
                        if img_ext == 'jpeg':
                            img_ext = 'jpg'
                        extracted_images.append(_save_script_photo(photos_dir, img_bytes, img_ext))
                    except Exception:
                        pass

        lines.append({
            "text": text,
            "spans": spans,
            "hiddenLinks": hidden_links,
            "extractedImages": extracted_images,
            "origIndex": i,
            "isEditorLine": bool(re.search(r'@?editor', text, re.IGNORECASE)),
            "isHeader": is_header,
            "headerLevel": header_level
        })

    items: List[ScriptItem] = []
    links_set = set()
    notes: List[EditorNote] = []
    warnings: List[str] = []
    processed_line_indices = set()

    trigger_regex = re.compile(r'@?editor\s*[:;]?\s*.*(play|pay|lay|pla|ply|insert|inset|insrt|inst|show|use|using|clip|roll|cut|add)', re.IGNORECASE)
    # Strictly require colons in timestamp parts to avoid eating episode/season numbers like S10E20
    global_time_regex = re.compile(
        r'(?:\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?\s*(?:[-–—]|to)\s*\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?)|(?:\(?((?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?)\)?)',
        re.IGNORECASE
    )

    def process_match(match, pivot_i: int, lines_arr: List[Dict[str, Any]], meta: str, sync_id: int):
        g1, g2, g3 = match

        start_str = None
        end_str = None
        start_sec = 0
        end_sec = 0
        is_single = False
        raw_ts = ""

        if g1:
            start_str, start_sec = process_time_raw(g1)
            end_str, end_sec = process_time_raw(g2)
            raw_ts = f"{g1} - {g2}"
        elif g3:
            start_str, start_sec = process_time_raw(g3)
            is_single = True
            raw_ts = g3

        if not start_str:
            return

        duration = max(0, end_sec - start_sec) if not is_single and end_str else 0

        # Find line index matching sync_id
        t_idx = None
        for idx, line in enumerate(lines_arr):
            if line["origIndex"] == sync_id:
                t_idx = idx
                break
        pivot_idx = t_idx if t_idx is not None else pivot_i

        # Search offsets [0, -1, 1, 2, 3, 4] for associated link
        found_link = None
        for offset in [0, -1, 1, 2, 3, 4]:
            k = pivot_idx + offset
            if 0 <= k < len(lines_arr):
                check_line = lines_arr[k]
                if "spans" in check_line:
                    for s in check_line["spans"]:
                        if s.get("link"):
                            found_link = s["link"]
                            break
                if not found_link and 'http' in check_line["text"]:
                    url_match = re.search(r'(https?:\/\/[^\s]+)', check_line["text"])
                    if url_match:
                        found_link = url_match.group(1)
                if not found_link and check_line.get("hiddenLinks"):
                    found_link = check_line["hiddenLinks"][0]
                if found_link:
                    break

        norm_link = normalize_link(found_link) if found_link else None
        if norm_link:
            links_set.add(norm_link)

        row_idx = len(items) + 1
        item_id = str(uuid.uuid4())

        # Check existing database overrides
        if item_id in overrides:
            ov = overrides[item_id]
            start_str = ov.get("start_tc") or start_str
            end_str = ov.get("end_tc") or end_str

        # Duration display
        if duration >= 60:
            mins = duration // 60
            secs = duration % 60
            dur_disp = f"{mins}m {secs}s" if secs > 0 else f"{mins}m"
        elif duration > 0:
            dur_disp = f"{duration}s"
        else:
            dur_disp = "--"

        item_links = [norm_link] if norm_link else []
        checked_links = existing_doc.checked_links if existing_doc else {}
        is_checked = any(checked_links.get(l, False) for l in item_links)

        items.append(ScriptItem(
            id=item_id,
            row_index=row_idx,
            raw_timestamp=raw_ts,
            start_timecode=start_str,
            start_seconds=float(start_sec),
            end_timecode=end_str or "",
            end_seconds=float(end_sec),
            duration_seconds=float(duration),
            duration_display=dur_disp,
            text=lines_arr[pivot_idx]["text"],
            meta=meta,
            is_single=is_single,
            sync_id=sync_id,
            links=item_links,
            is_checked=is_checked
        ))

    for i, line_obj in enumerate(lines):
        text_line = line_obj["text"]

        if line_obj["isEditorLine"]:
            if trigger_regex.search(text_line):
                found_timestamp = False
                found_meta = "Clip"

                # Search metadata (S01E02) nearby
                for m in range(max(0, i - 3), min(i + 3, len(lines))):
                    meta_check = lines[m]["text"]
                    full_match = re.search(r'S(\d+)\s*E(\d+)|Season\s*(\d+)|Episode\s*(\d+)', meta_check, re.IGNORECASE)
                    if full_match:
                        s = full_match.group(1) or full_match.group(3)
                        e = full_match.group(2) or full_match.group(4)
                        if not s or not e:
                            s_m = re.search(r'S(\d+)|Season\s*(\d+)', meta_check, re.IGNORECASE)
                            e_m = re.search(r'E(\d+)|Episode\s*(\d+)', meta_check, re.IGNORECASE)
                            s = s_m.group(1) if s_m else None
                            e = e_m.group(1) if e_m else None
                        if s and e:
                            found_meta = f"S{str(s).zfill(2)}E{str(e).zfill(2)}"
                            break

                search_limit = min(i + 6, len(lines))
                for j in range(i, search_limit):
                    sub_line_obj = lines[j]
                    if j > i and trigger_regex.search(sub_line_obj["text"]):
                        break

                    matches = global_time_regex.findall(sub_line_obj["text"])
                    if matches:
                        found_timestamp = True
                        for match in matches:
                            processed_line_indices.add(sub_line_obj["origIndex"])
                            process_match(match, i, lines, found_meta, sub_line_obj["origIndex"])

                if not found_timestamp:
                    warnings.append("Found @editor tag but no timestamp. Moved to Editor's Notes.")
                    notes.append(EditorNote(
                        id=str(uuid.uuid4()),
                        block_id=line_obj["origIndex"],
                        text=text_line
                    ))
            else:
                context = (text_line[:60] + '...') if len(text_line) > 60 else text_line
                warnings.append(f"Ignored Unknown Command: \"{context}\". Moved to Editor's Notes.")
                notes.append(EditorNote(
                    id=str(uuid.uuid4()),
                    block_id=line_obj["origIndex"],
                    text=text_line
                ))

        if line_obj["origIndex"] not in processed_line_indices:
            matches = global_time_regex.findall(text_line)
            if matches:
                has_link_nearby = False
                for k in range(max(0, i - 1), min(i + 2, len(lines))):
                    if 'http' in lines[k]["text"] or lines[k]["hiddenLinks"]:
                        has_link_nearby = True
                        break

                if has_link_nearby:
                    for match in matches:
                        processed_line_indices.add(line_obj["origIndex"])
                        fallback_meta = "Clip"
                        for m in range(max(0, i - 2), min(i + 2, len(lines))):
                            meta_check = lines[m]["text"]
                            meta_match = re.search(r'S(\d+)\s*E(\d+)', meta_check, re.IGNORECASE)
                            if meta_match:
                                fallback_meta = f"S{meta_match.group(1).zfill(2)}E{meta_match.group(2).zfill(2)}"
                                break
                        process_match(match, i, lines, fallback_meta, line_obj["origIndex"])

        if line_obj["hiddenLinks"]:
            for link in line_obj["hiddenLinks"]:
                links_set.add(normalize_link(link))

    # Reconcile new items & notes with existing_doc so checked states and IDs survive re-parses
    if existing_doc:
        unmatched_prev_items = list(existing_doc.items)
        for new_item in items:
            matched_prev = None
            # 1. Exact (sync_id, raw_timestamp) match
            for prev in unmatched_prev_items:
                if prev.sync_id == new_item.sync_id and prev.raw_timestamp == new_item.raw_timestamp:
                    matched_prev = prev
                    break
            # 2. Same (start_timecode, end_timecode, meta)
            if not matched_prev:
                for prev in unmatched_prev_items:
                    if (prev.start_timecode == new_item.start_timecode and
                        prev.end_timecode == new_item.end_timecode and
                        prev.meta == new_item.meta):
                        matched_prev = prev
                        break
            # 3. Same sync_id (when timestamp on that line was edited)
            if not matched_prev:
                for prev in unmatched_prev_items:
                    if prev.sync_id == new_item.sync_id:
                        matched_prev = prev
                        break

            if matched_prev:
                unmatched_prev_items.remove(matched_prev)
                new_item.id = matched_prev.id
                new_item.is_checked = matched_prev.is_checked or new_item.is_checked
                if new_item.id in overrides:
                    ov = overrides[new_item.id]
                    new_item.start_timecode = ov.get("start_tc") or new_item.start_timecode
                    new_item.end_timecode = ov.get("end_tc") or new_item.end_timecode

        unmatched_prev_notes = list(existing_doc.notes)
        for new_note in notes:
            matched_n = None
            for prev_n in unmatched_prev_notes:
                if prev_n.block_id == new_note.block_id and prev_n.text == new_note.text:
                    matched_n = prev_n
                    break
            if not matched_n:
                for prev_n in unmatched_prev_notes:
                    if prev_n.text == new_note.text:
                        matched_n = prev_n
                        break
            if matched_n:
                unmatched_prev_notes.remove(matched_n)
                new_note.id = matched_n.id
                new_note.is_checked = matched_n.is_checked

    unique_links = list(links_set)
    link_colors = generate_link_colors(unique_links)

    # Build blocks
    blocks = []
    headings = []
    for line in lines:
        block = {
            "id": line["origIndex"],
            "text": line["text"],
            "spans": line["spans"],
            "images": line.get("extractedImages", []),
            "isEditorLine": line["isEditorLine"],
            "isHeader": line["isHeader"],
            "headerLevel": line["headerLevel"]
        }
        blocks.append(block)
        if line["isHeader"]:
            headings.append({
                "id": line["origIndex"],
                "text": line["text"],
                "level": line["headerLevel"] or 1
            })

    raw_html_content = build_raw_html(blocks, link_colors)

    if progress_callback:
        progress_callback(95, "Finishing up...")

    new_doc_state = DocumentState(
        id=existing_doc.id if existing_doc else str(uuid.uuid4()),
        file_signature=file_sig,
        name=file_name,
        file_path=file_path,
        raw_html=raw_html_content,
        items=items,
        notes=notes,
        custom_notes=existing_doc.custom_notes if existing_doc else [],
        headings=headings,
        links=unique_links,
        checked_links=existing_doc.checked_links if existing_doc else {},
        checked_notes=existing_doc.checked_notes if existing_doc else {},
        link_colors=link_colors,
        page_geometry=page_geometry,
        blocks=blocks
    )

    db.save_document(new_doc_state)
    if previous_doc is None:
        _count_lifetime_stats_once(file_name, new_doc_state)
    from .project_manager import project_manager
    proj_path = project_manager.get_auto_project_path(file_name)
    db.add_recent_file(proj_path if os.path.exists(proj_path) else file_path, file_name)

    if progress_callback:
        progress_callback(100, "Done!")

    return new_doc_state


def _apply_spans_to_paragraph(p, spans: List[Dict[str, Any]], is_header: bool = False, header_level: Optional[int] = None, doc=None):
    """Rebuilds text runs of a python-docx Paragraph from span dicts while preserving embedded drawings."""
    # Preserve any run containing actual <w:drawing> or <w:pict> child elements
    drawing_runs = []
    for child in list(p._p):
        if child.tag.endswith('}r'):
            has_drawing = any(sub.tag.endswith('}drawing') or sub.tag.endswith('}pict') for sub in child.iter())
            if has_drawing:
                drawing_runs.append(child)
            p._p.remove(child)
        elif child.tag.endswith('}hyperlink'):
            p._p.remove(child)

    if doc is not None:
        try:
            if is_header:
                lvl = header_level if header_level in (1, 2, 3, 4) else 1
                style_name = f"Heading {lvl}"
                if style_name in doc.styles:
                    p.style = doc.styles[style_name]
            else:
                if p.style and p.style.name and p.style.name.startswith("Heading"):
                    if "Normal" in doc.styles:
                        p.style = doc.styles["Normal"]
        except Exception:
            pass

    for s in spans:
        txt = s.get("text", "")
        if not txt:
            continue
        run = p.add_run(txt)
        if s.get("bold") is not None:
            run.bold = bool(s["bold"])
        if s.get("italic") is not None:
            run.italic = bool(s["italic"])
        col_str = s.get("color")
        if col_str and isinstance(col_str, str):
            clean_hex = col_str.lstrip("#")
            if len(clean_hex) == 6:
                try:
                    run.font.color.rgb = RGBColor.from_string(clean_hex.upper())
                except Exception:
                    pass
        sz_str = s.get("size")
        if sz_str and isinstance(sz_str, str) and sz_str.endswith("pt"):
            try:
                pt_val = float(sz_str.replace("pt", ""))
                run.font.size = Pt(pt_val)
            except Exception:
                pass
        bg_str = s.get("bg_color")
        if bg_str and isinstance(bg_str, str):
            clean_bg = bg_str.lstrip("#")
            if len(clean_bg) == 6:
                try:
                    rPr = run._r.get_or_add_rPr()
                    shd = OxmlElement('w:shd')
                    shd.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val', 'clear')
                    shd.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}color', 'auto')
                    shd.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}fill', clean_bg.upper())
                    rPr.append(shd)
                except Exception:
                    pass

    for dr in drawing_runs:
        p._p.append(dr)


def save_blocks_to_docx(
    file_path: str,
    edited_blocks: List[Dict[str, Any]],
    previous_doc: Optional[DocumentState] = None
) -> DocumentState:
    """
    Writes modified/inserted blocks directly back to the .docx file on disk,
    migrates the SQLite file signature in-place, and re-runs the parser to refresh DocumentState.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"DOCX file not found: {file_path}")

    old_sig = previous_doc.file_signature if previous_doc else compute_file_hash(file_path)
    doc = Document(file_path)
    orig_paragraphs = list(doc.paragraphs)

    # Index original blocks for change comparison so untouched paragraphs aren't modified
    orig_block_map: Dict[int, Dict[str, Any]] = {}
    if previous_doc and previous_doc.blocks:
        for b in previous_doc.blocks:
            if isinstance(b.get("id"), int):
                orig_block_map[b["id"]] = b

    seen_orig_ids = set()
    last_anchor_p = orig_paragraphs[-1] if orig_paragraphs else None

    for b in edited_blocks:
        block_id = b.get("id")
        spans = b.get("spans") or [{"text": b.get("text", ""), "bold": False, "italic": False, "color": None, "size": None, "link": None}]
        is_header = bool(b.get("isHeader", False))
        header_level = b.get("headerLevel")

        if isinstance(block_id, int) and 0 <= block_id < len(orig_paragraphs):
            seen_orig_ids.add(block_id)
            target_p = orig_paragraphs[block_id]
            last_anchor_p = target_p

            orig_b = orig_block_map.get(block_id)
            if orig_b:
                norm_orig_txt = re.sub(r'\s+', ' ', (orig_b.get("text") or "").strip())
                norm_new_txt = re.sub(r'\s+', ' ', (b.get("text") or "").strip())
                # Skip touching XML if text, header state, and span styles are identical
                if (norm_orig_txt == norm_new_txt and
                    bool(orig_b.get("isHeader")) == is_header and
                    orig_b.get("headerLevel") == header_level and
                    orig_b.get("spans") == spans):
                    continue

            _apply_spans_to_paragraph(target_p, spans, is_header=is_header, header_level=header_level, doc=doc)
        else:
            # Newly inserted paragraph in Direct Edit Mode
            new_p = doc.add_paragraph()
            _apply_spans_to_paragraph(new_p, spans, is_header=is_header, header_level=header_level, doc=doc)
            if last_anchor_p is not None and last_anchor_p._p is not new_p._p:
                last_anchor_p._p.addnext(new_p._p)
            last_anchor_p = new_p

    # If any original text block was completely deleted by the user in Edit Mode, clear its text runs
    if orig_block_map:
        for orig_id, orig_b in orig_block_map.items():
            if orig_id not in seen_orig_ids and 0 <= orig_id < len(orig_paragraphs):
                if not orig_b.get("images"):
                    _apply_spans_to_paragraph(orig_paragraphs[orig_id], [], is_header=False, doc=doc)

    doc.save(file_path)
    new_sig = compute_file_hash(file_path)
    db.migrate_document_signature(old_sig, new_sig)

    return parse_docx_file(file_path, previous_doc=previous_doc)


def _format_tc_for_script(tc_norm: str, reference_raw: str = "") -> str:
    """Formats a normalized HH:MM:SS:FF timecode to match script style (e.g., MM:SS if hours/frames are 00)."""
    parts = tc_norm.split(":")
    if len(parts) != 4:
        return tc_norm
    h, m, s, f = parts
    ref_colons = reference_raw.count(":")
    if f == "00" and h == "00" and ref_colons <= 1:
        return f"{m}:{s}"
    if f == "00" and ref_colons == 2:
        return f"{h}:{m}:{s}"
    return tc_norm


def update_timecode_in_docx(
    file_path: str,
    item: ScriptItem,
    new_start_tc: str,
    new_end_tc: Optional[str] = None,
    previous_doc: Optional[DocumentState] = None
) -> DocumentState:
    """
    Updates a ScriptItem's timestamp directly inside the .docx paragraph (sync_id),
    saves the .docx, migrates the signature, and refreshes DocumentState.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"DOCX file not found: {file_path}")

    old_sig = previous_doc.file_signature if previous_doc else compute_file_hash(file_path)
    doc = Document(file_path)
    paragraphs = list(doc.paragraphs)

    norm_start, _, _ = parse_time_single(new_start_tc)
    if new_end_tc is not None:
        norm_end, _, _ = parse_time_single(new_end_tc)
    else:
        norm_end = item.end_timecode

    # Determine replacement raw timestamp string
    raw_parts = re.split(r'\s*(?:[-–—]|to)\s*', item.raw_timestamp.strip()) if item.raw_timestamp else []
    start_ref = raw_parts[0] if raw_parts else ""
    end_ref = raw_parts[1] if len(raw_parts) > 1 else start_ref

    fmt_start = _format_tc_for_script(norm_start, start_ref)
    if not item.is_single and norm_end:
        fmt_end = _format_tc_for_script(norm_end, end_ref)
        replacement_raw = f"{fmt_start} - {fmt_end}"
    else:
        replacement_raw = fmt_start

    # Locate paragraph containing raw_timestamp around item.sync_id
    candidate_indices = []
    if 0 <= item.sync_id < len(paragraphs):
        candidate_indices.append(item.sync_id)
    for offset in (-1, 1, 2, -2, 3):
        idx = item.sync_id + offset
        if 0 <= idx < len(paragraphs) and idx not in candidate_indices:
            candidate_indices.append(idx)

    replaced = False
    for idx in candidate_indices:
        p = paragraphs[idx]
        if item.raw_timestamp and item.raw_timestamp in p.text:
            # Try in-run replacement first to preserve run styling
            for run in p.runs:
                if item.raw_timestamp in run.text:
                    run.text = run.text.replace(item.raw_timestamp, replacement_raw, 1)
                    replaced = True
                    break
            if not replaced:
                new_p_text = p.text.replace(item.raw_timestamp, replacement_raw, 1)
                if p.runs:
                    p.runs[0].text = new_p_text
                    for r in p.runs[1:]:
                        r.text = ""
                else:
                    p.add_run(new_p_text)
                replaced = True
            break

    # Persist override as well so explicit timecode choice is guaranteed
    db.save_override(old_sig, item.id, norm_start, norm_end or "")

    if replaced:
        doc.save(file_path)
        new_sig = compute_file_hash(file_path)
        db.migrate_document_signature(old_sig, new_sig)
        return parse_docx_file(file_path, previous_doc=previous_doc)

    return previous_doc or parse_docx_file(file_path)

class ParseWorkerSignals(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    error = Signal(str)

class DocxParseWorker(QRunnable):
    """Zero-UI-Freeze Background Worker for parsing DOCX files."""
    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.signals = ParseWorkerSignals()

    def run(self):
        try:
            doc_state = parse_docx_file(
                self.file_path, 
                progress_callback=self.signals.progress.emit
            )
            self.signals.finished.emit(doc_state)
        except Exception as e:
            self.signals.error.emit(str(e))
