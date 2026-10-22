import os
import re
import uuid
import hashlib
import tempfile
import colorsys
import html
from typing import Tuple, List, Dict, Optional, Any
from PySide6.QtCore import QRunnable, QObject, Signal
from docx import Document
from docx.text.run import Run

from .models import DocumentState, ScriptItem, EditorNote
from .database import db

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

def render_span_text(text: str) -> str:
    """Highlights @editor triggers and timestamps in text."""
    # 1. Highlight @editor triggers (Yellow)
    text = re.sub(
        r'(@?editor\s*[:;]?)', 
        r"<span style='color: #facc15; font-weight: 700;'>\1</span>", 
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
        return f"<a href='time:{raw}' style='color: #38bdf8; text-decoration: underline; font-weight: 700; cursor: pointer;' data-time='{raw}'>{raw}</a>"

    text = time_pattern.sub(repl_time, text)
    return text

def build_raw_html(blocks: List[Dict[str, Any]], link_colors: Dict[str, str]) -> str:
    """Builds styled HTML for ScriptViewer preserving DOCX font colors, formatting, and headings."""
    html_parts = []
    for b in blocks:
        block_id = b["id"]
        is_header = b["isHeader"]
        header_level = b["headerLevel"] or 1

        span_htmls = []
        for s in b["spans"]:
            txt = s["text"]
            escaped_txt = html.escape(txt)
            styled_txt = render_span_text(escaped_txt)

            style_props = []
            if s.get("color"):
                style_props.append(f"color: {s['color']}")
            if s.get("bold"):
                style_props.append("font-weight: bold")
            if s.get("italic"):
                style_props.append("font-style: italic")
            if s.get("size"):
                pt_match = re.search(r'([\d.]+)pt', s["size"])
                if pt_match:
                    em_val = round(float(pt_match.group(1)) / 11.0, 2)
                    style_props.append(f"font-size: {em_val}em")

            style_str = f" style='{'; '.join(style_props)}'" if style_props else ""

            if s.get("link"):
                norm = normalize_link(s["link"])
                link_col = link_colors.get(norm, "#38bdf8")
                span_htmls.append(f"<a href='{s['link']}' style='color: {link_col}; text-decoration: underline; font-weight: bold;'>{styled_txt}</a>")
            else:
                if style_props:
                    span_htmls.append(f"<span{style_str}>{styled_txt}</span>")
                else:
                    span_htmls.append(styled_txt)

        inner_content = "".join(span_htmls)

        if is_header:
            if header_level == 1:
                tag = f"<h1 id='block-{block_id}' style='margin: 18px 0 8px 0; font-size: 1.5em; font-weight: 800; color: #4ade80;'><a name='block-{block_id}'></a>{inner_content}</h1>"
            elif header_level == 2:
                tag = f"<h2 id='block-{block_id}' style='margin: 14px 0 6px 0; font-size: 1.3em; font-weight: 700; color: #4ade80;'><a name='block-{block_id}'></a>{inner_content}</h2>"
            elif header_level == 3:
                tag = f"<h3 id='block-{block_id}' style='margin: 12px 0 4px 0; font-size: 1.15em; font-weight: 700; color: #4ade80;'><a name='block-{block_id}'></a>{inner_content}</h3>"
            else:
                tag = f"<h4 id='block-{block_id}' style='margin: 10px 0 4px 0; font-size: 1.05em; font-weight: 700; color: #4ade80;'><a name='block-{block_id}'></a>{inner_content}</h4>"
            html_parts.append(tag)
        else:
            html_parts.append(f"<p id='block-{block_id}' style='margin: 4px 0; line-height: 1.5;'><a name='block-{block_id}'></a>{inner_content}</p>")

    return "".join(html_parts)

def parse_docx_file(file_path: str, progress_callback=None) -> DocumentState:
    """
    Parses a DOCX file using the authentic Python sidecar engine logic from the original React app.
    Extracts all run styling, font colors, headings, images, and correctly associates timestamps and links.
    """
    file_sig = compute_file_hash(file_path)
    file_name = os.path.basename(file_path)

    existing_doc = db.get_document_by_signature(file_sig)
    overrides = db.get_overrides(file_sig)

    if progress_callback:
        progress_callback(10, "Opening document...")

    doc = Document(file_path)
    temp_img_dir = os.path.join(tempfile.gettempdir(), "clickables_images", file_sig[:12])
    os.makedirs(temp_img_dir, exist_ok=True)

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
            continue

        is_header = False
        header_level = None
        if p.style and p.style.name:
            style_name = p.style.name
            if style_name.startswith('Heading'):
                is_header = True
                match = re.search(r'\d+', style_name)
                if match:
                    header_level = int(match.group())
            elif 'Title' in style_name:
                is_header = True
                header_level = 1 if 'Subtitle' not in style_name else 2

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

            spans.append({
                "text": run.text,
                "color": f"#{color}" if color else None,
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
                    last_span["bold"] == span["bold"] and
                    last_span["italic"] == span["italic"] and
                    last_span["size"] == span["size"] and
                    last_span["link"] == span["link"]):
                    last_span["text"] += span["text"]
                else:
                    merged_spans.append(span)
        spans = merged_spans

        # Heuristic auto-detection for document headings if not marked by Word styles
        if not is_header and text and not bool(re.search(r'@?editor', text, re.IGNORECASE)):
            clean_text = text.strip()
            if len(clean_text) <= 80 and not clean_text.endswith(('.', '?', '!', ';')):
                section_kw_match = re.match(r'^(lvl|level|chapter|section|part|tab|scene|act|outro|intro)\s*(\d*)', clean_text, re.IGNORECASE)
                all_bold = len(spans) > 0 and all(s.get("bold") for s in spans if s.get("text", "").strip())
                large_font = False
                for s in spans:
                    sz = s.get("size")
                    if sz and sz.endswith("pt"):
                        try:
                            if float(sz.replace("pt", "")) >= 13.0:
                                large_font = True
                                break
                        except ValueError:
                            pass

                if section_kw_match or all_bold or large_font:
                    is_header = True
                    if re.search(r'\b(lvl\s*2|level\s*2|sub[- ]?section)\b', clean_text, re.IGNORECASE):
                        header_level = 2
                    elif re.search(r'\b(lvl\s*3|level\s*3)\b', clean_text, re.IGNORECASE):
                        header_level = 3
                    elif section_kw_match:
                        num = section_kw_match.group(2)
                        header_level = int(num) if num and int(num) in [1, 2, 3, 4] else 1
                    elif large_font:
                        header_level = 1
                    else:
                        header_level = 1 if len(clean_text) < 40 else 2

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
                        img_filename = f"{uuid.uuid4().hex[:8]}.{img_ext}"
                        img_path = os.path.join(temp_img_dir, img_filename)
                        with open(img_path, 'wb') as f_img:
                            f_img.write(img_bytes)
                        extracted_images.append(os.path.abspath(img_path))
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
        link_colors=link_colors
    )

    db.save_document(new_doc_state)
    db.add_recent_file(file_path, file_name)

    if progress_callback:
        progress_callback(100, "Done!")

    return new_doc_state

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
