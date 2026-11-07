import os
import xml.etree.ElementTree as ET
from typing import Optional, Dict
from .models import DocumentState, ScriptItem
from .parser import normalize_link
from .timecode_engine import analyze_framerate, parse_timestamp_seconds, seconds_to_frames
from .database import db


def parse_timecode_to_frames(tc_str: str, fps: float = 23.976) -> int:
    """Parses timecode HH:MM:SS:FF or MM:SS to total frames at specified fps."""
    if not tc_str:
        return 0
    clean = str(tc_str).replace(";", ":").strip()
    parts = clean.split(":")
    nominal = round(fps)
    try:
        if len(parts) == 4:
            h, m, s, f = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            return ((h * 3600) + (m * 60) + s) * nominal + f
        elif len(parts) == 3:
            m, s, f = int(parts[0]), int(parts[1]), int(parts[2])
            return ((m * 60) + s) * nominal + f
        elif len(parts) == 2:
            m = int(parts[0])
            s = float(parts[1])
            return int(round((m * 60.0 + s) * fps))
        else:
            return int(round(float(parts[0]) * fps))
    except Exception:
        return 0


def generate_fcp_xml(
    doc_state: DocumentState,
    footage_folder: str = "",
    fps: float = 23.976,
    linked_footage: Optional[Dict[str, str]] = None,
) -> str:
    """Generates Final Cut Pro 7 XML (xmeml v4) compatible with Adobe Premiere Pro import.
    
    Creates a timeline sequence with clipitems corresponding to the parsed script items,
    along with dialogue markers and metadata annotations.
    """
    linked_map = linked_footage or {}
    xmeml = ET.Element("xmeml", version="4")
    seq = ET.SubElement(xmeml, "sequence")
    
    exact_fps, nominal_base, _, _ = analyze_framerate(fps)
    is_ntsc = abs(exact_fps - 23.976) < 0.01 or abs(exact_fps - 29.97) < 0.01 or abs(exact_fps - 59.94) < 0.01

    # Sequence Metadata
    name_el = ET.SubElement(seq, "name")
    name_el.text = doc_state.name or "Rainy Timeline"
    
    rate_el = ET.SubElement(seq, "rate")
    timebase_el = ET.SubElement(rate_el, "timebase")
    timebase_el.text = str(nominal_base)
    ntsc_el = ET.SubElement(rate_el, "ntsc")
    ntsc_el.text = "TRUE" if is_ntsc else "FALSE"
    
    # Media & Video Track
    media = ET.SubElement(seq, "media")
    video = ET.SubElement(media, "video")
    
    format_el = ET.SubElement(video, "format")
    sample_char = ET.SubElement(format_el, "samplecharacteristics")
    w_el = ET.SubElement(sample_char, "width")
    w_el.text = "1920"
    h_el = ET.SubElement(sample_char, "height")
    h_el.text = "1080"
    
    track = ET.SubElement(video, "track")
    
    curr_track_frame = 0
    for idx, item in enumerate(doc_state.items):
        start_frame = parse_timecode_to_frames(item.start_timecode, fps)
        end_frame = parse_timecode_to_frames(item.end_timecode, fps)
        duration_frames = max(nominal_base, end_frame - start_frame)
        
        clip_start = curr_track_frame
        clip_end = curr_track_frame + duration_frames
        curr_track_frame = clip_end
        
        clipitem = ET.SubElement(track, "clipitem", id=f"clipitem-{idx + 1}")
        c_name = ET.SubElement(clipitem, "name")
        c_name.text = f"[{'DONE' if item.is_checked else 'AROL'}] {item.text[:30]}"
        
        c_duration = ET.SubElement(clipitem, "duration")
        c_duration.text = str(duration_frames)
        
        c_rate = ET.SubElement(clipitem, "rate")
        c_tb = ET.SubElement(c_rate, "timebase")
        c_tb.text = str(nominal_base)
        c_ntsc = ET.SubElement(c_rate, "ntsc")
        c_ntsc.text = "TRUE" if is_ntsc else "FALSE"
        
        c_start = ET.SubElement(clipitem, "start")
        c_start.text = str(clip_start)
        
        c_end = ET.SubElement(clipitem, "end")
        c_end.text = str(clip_end)
        
        c_in = ET.SubElement(clipitem, "in")
        c_in.text = str(start_frame)
        
        c_out = ET.SubElement(clipitem, "out")
        c_out.text = str(end_frame)
        
        # Physical media linking in FCP XML
        local_matched = None
        for lk in (item.links or []):
            norm_lk = normalize_link(lk)
            cand = linked_map.get(norm_lk) or linked_map.get(lk)
            if cand:
                local_matched = cand
                break

        if local_matched:
            file_el = ET.SubElement(clipitem, "file", id=f"file-{idx + 1}")
            f_name = ET.SubElement(file_el, "name")
            f_name.text = os.path.basename(local_matched)
            f_path = ET.SubElement(file_el, "pathurl")
            # FCP 7 XML URI format: file://localhost/D:/Folder/Clip.mp4
            clean_p = local_matched.replace("\\", "/")
            if not clean_p.startswith("/"):
                clean_p = "/" + clean_p
            f_path.text = f"file://localhost{clean_p}"
            f_rate = ET.SubElement(file_el, "rate")
            f_tb = ET.SubElement(f_rate, "timebase")
            f_tb.text = str(nominal_base)
            f_ntsc = ET.SubElement(f_rate, "ntsc")
            f_ntsc.text = "TRUE" if is_ntsc else "FALSE"
            f_dur = ET.SubElement(file_el, "duration")
            f_dur.text = str(duration_frames)

        # Dialogue marker
        marker = ET.SubElement(clipitem, "marker")
        m_comment = ET.SubElement(marker, "comment")
        m_comment.text = f"{item.text}\nLinks: {', '.join(item.links) if item.links else 'None'}"
        if local_matched:
            m_comment.text += f"\nLocal Footage: {local_matched}"
        m_in = ET.SubElement(marker, "in")
        m_in.text = str(start_frame)
        m_out = ET.SubElement(marker, "out")
        m_out.text = str(end_frame)

    # Sequence duration
    seq_dur = ET.SubElement(seq, "duration")
    seq_dur.text = str(curr_track_frame)
    
    xml_str = ET.tostring(xmeml, encoding="utf-8")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n{xml_str.decode("utf-8")}'


def export_fcp_xml_file(
    file_path: str,
    doc_state: DocumentState,
    footage_folder: str = "",
    linked_footage: Optional[Dict[str, str]] = None,
    fps: Optional[float] = None,
) -> None:
    """Exports DocumentState to Premiere Pro FCP XML file with local footage links."""
    if fps is None:
        fps_str = db.get_setting("timeline_framerate", "23.976")
        try:
            fps = float(fps_str)
        except Exception:
            fps = 23.976

    xml_content = generate_fcp_xml(doc_state, footage_folder=footage_folder, fps=fps, linked_footage=linked_footage)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(xml_content)
