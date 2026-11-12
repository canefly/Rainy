import os
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Optional, Dict, List, Any
from .models import DocumentState, ScriptItem
from .parser import normalize_link
from .timecode_engine import analyze_framerate, parse_timestamp_seconds, seconds_to_frames
from .database import db

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp"}


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


def _format_file_url(local_path: str) -> str:
    clean_p = os.path.abspath(local_path).replace("\\", "/")
    if not clean_p.startswith("/"):
        clean_p = "/" + clean_p
    encoded_p = urllib.parse.quote(clean_p, safe="/:")
    return f"file://localhost{encoded_p}"


def generate_fcp_xml(
    doc_state: DocumentState,
    footage_folder: str = "",
    fps: float = 23.976,
    linked_footage: Optional[Dict[str, str]] = None,
    include_clip_markers: bool = False,
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
    ntsc_str = "TRUE" if is_ntsc else "FALSE"

    # Sequence Metadata
    name_el = ET.SubElement(seq, "name")
    name_el.text = doc_state.name or "Rainy Timeline"
    
    rate_el = ET.SubElement(seq, "rate")
    timebase_el = ET.SubElement(rate_el, "timebase")
    timebase_el.text = str(nominal_base)
    ntsc_el = ET.SubElement(rate_el, "ntsc")
    ntsc_el.text = ntsc_str
    
    # Media: Video Track V1
    media = ET.SubElement(seq, "media")
    video = ET.SubElement(media, "video")
    
    format_el = ET.SubElement(video, "format")
    sample_char = ET.SubElement(format_el, "samplecharacteristics")
    v_rate_sc = ET.SubElement(sample_char, "rate")
    ET.SubElement(v_rate_sc, "timebase").text = str(nominal_base)
    ET.SubElement(v_rate_sc, "ntsc").text = ntsc_str
    w_el = ET.SubElement(sample_char, "width")
    w_el.text = "1920"
    h_el = ET.SubElement(sample_char, "height")
    h_el.text = "1080"
    
    v_track = ET.SubElement(video, "track")

    # Media: Audio Track A1
    audio = ET.SubElement(media, "audio")
    a_format = ET.SubElement(audio, "format")
    a_sc = ET.SubElement(a_format, "samplecharacteristics")
    ET.SubElement(a_sc, "depth").text = "16"
    ET.SubElement(a_sc, "samplerate").text = "48000"
    a_track = ET.SubElement(audio, "track")

    AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff"}
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp"}

    # Pre-scan matched files to establish safe duration metadata
    files_meta: Dict[str, Dict[str, Any]] = {}
    file_id_counter = 1

    for item in doc_state.items:
        # Skip single-point timestamps / stray dialogue times without an in-and-out range
        if getattr(item, "is_single", False) or not item.end_timecode:
            continue

        s_frame = parse_timecode_to_frames(item.start_timecode, exact_fps)
        e_frame = parse_timecode_to_frames(item.end_timecode, exact_fps)
        if e_frame <= s_frame:
            e_frame = s_frame + nominal_base * 2

        matched = None
        for lk in (item.links or []):
            norm_lk = normalize_link(lk)
            cand = linked_map.get(norm_lk) or linked_map.get(lk)
            if cand:
                matched = cand
                break
        if matched:
            norm_p = os.path.normpath(matched)
            ext = os.path.splitext(matched)[1].lower()
            is_pure_audio = ext in AUDIO_EXTS
            
            from .media_probe import probe_media_file
            probe_info = probe_media_file(matched)
            has_v = bool(probe_info.has_video) if probe_info else (not is_pure_audio)
            has_a = bool(probe_info.has_audio) if probe_info else is_pure_audio
            v_width = probe_info.width if (probe_info and probe_info.width) else 1920
            v_height = probe_info.height if (probe_info and probe_info.height) else 1080
            a_channels = probe_info.audio_channels if (probe_info and probe_info.audio_channels) else 2
            a_sr = probe_info.sample_rate if (probe_info and probe_info.sample_rate) else 48000
            
            needed_dur = max(e_frame + nominal_base * 10, nominal_base * 10)
            if norm_p not in files_meta:
                files_meta[norm_p] = {
                    "id": f"file-{file_id_counter}",
                    "name": os.path.basename(matched),
                    "pathurl": _format_file_url(matched),
                    "duration": needed_dur,
                    "is_pure_audio": is_pure_audio,
                    "has_video": has_v,
                    "has_audio": has_a,
                    "width": v_width,
                    "height": v_height,
                    "channels": a_channels,
                    "sample_rate": a_sr,
                }
                file_id_counter += 1
            else:
                if needed_dur > files_meta[norm_p]["duration"]:
                    files_meta[norm_p]["duration"] = needed_dur

    seen_file_ids = set()

    def _attach_file_element(parent_el: ET.Element, f_info: Dict[str, Any]):
        fid = f_info["id"]
        f_el = ET.SubElement(parent_el, "file", id=fid)
        if fid not in seen_file_ids:
            seen_file_ids.add(fid)
            ET.SubElement(f_el, "name").text = f_info["name"]
            ET.SubElement(f_el, "pathurl").text = f_info["pathurl"]
            f_rate = ET.SubElement(f_el, "rate")
            ET.SubElement(f_rate, "timebase").text = str(nominal_base)
            ET.SubElement(f_rate, "ntsc").text = ntsc_str
            ET.SubElement(f_el, "duration").text = str(f_info["duration"])
            m_el = ET.SubElement(f_el, "media")
            if f_info.get("has_video", True):
                vm = ET.SubElement(m_el, "video")
                vm_sc = ET.SubElement(vm, "samplecharacteristics")
                ET.SubElement(vm_sc, "width").text = str(f_info.get("width", 1920))
                ET.SubElement(vm_sc, "height").text = str(f_info.get("height", 1080))
            if f_info.get("has_audio", False):
                am = ET.SubElement(m_el, "audio")
                am_sc = ET.SubElement(am, "samplecharacteristics")
                ET.SubElement(am_sc, "depth").text = "16"
                ET.SubElement(am_sc, "samplerate").text = str(f_info.get("sample_rate", 48000))
                ET.SubElement(am, "channelcount").text = str(f_info.get("channels", 2))
    
    curr_track_frame = 0
    clip_counter = 0
    for item in doc_state.items:
        # Rule: Only export genuine media cuts with valid IN and OUT ranges
        if getattr(item, "is_single", False) or not item.end_timecode:
            continue

        clip_counter += 1
        start_frame = parse_timecode_to_frames(item.start_timecode, exact_fps)
        end_frame = parse_timecode_to_frames(item.end_timecode, exact_fps)
        
        # Guard against zero duration: out must strictly exceed in
        if end_frame <= start_frame:
            duration_frames = nominal_base * 2
            end_frame = start_frame + duration_frames
        else:
            duration_frames = end_frame - start_frame
        
        clip_start = curr_track_frame
        clip_end = curr_track_frame + duration_frames
        curr_track_frame = clip_end

        local_matched = None
        for lk in (item.links or []):
            norm_lk = normalize_link(lk)
            cand = linked_map.get(norm_lk) or linked_map.get(lk)
            if cand:
                local_matched = cand
                break

        f_info = files_meta.get(os.path.normpath(local_matched)) if local_matched else None
        is_pure_audio = f_info["is_pure_audio"] if f_info else False

        clip_id = f"clipitem-{clip_counter}"
        clip_name = f"[{'DONE' if item.is_checked else 'AROL'}] {item.text[:30]}".strip()

        if is_pure_audio:
            # Place audio assets on audio track
            a_item = ET.SubElement(a_track, "clipitem", id=clip_id)
            ET.SubElement(a_item, "name").text = clip_name
            ET.SubElement(a_item, "duration").text = str(duration_frames)
            c_rate = ET.SubElement(a_item, "rate")
            ET.SubElement(c_rate, "timebase").text = str(nominal_base)
            ET.SubElement(c_rate, "ntsc").text = ntsc_str
            ET.SubElement(a_item, "start").text = str(clip_start)
            ET.SubElement(a_item, "end").text = str(clip_end)
            ET.SubElement(a_item, "in").text = str(start_frame)
            ET.SubElement(a_item, "out").text = str(end_frame)
            if f_info:
                _attach_file_element(a_item, f_info)

            # Dialogue marker (disabled by default to avoid timeline clutter)
            if include_clip_markers:
                marker = ET.SubElement(a_item, "marker")
                m_comment = ET.SubElement(marker, "comment")
                m_comment.text = f"{item.text}\nLinks: {', '.join(item.links) if item.links else 'None'}"
                if local_matched:
                    m_comment.text += f"\nLocal Footage: {local_matched}"
                ET.SubElement(marker, "in").text = str(start_frame)
                ET.SubElement(marker, "out").text = str(end_frame)
        else:
            # Place video assets on video track
            clipitem = ET.SubElement(v_track, "clipitem", id=clip_id)
            c_name = ET.SubElement(clipitem, "name")
            c_name.text = clip_name
            
            c_duration = ET.SubElement(clipitem, "duration")
            c_duration.text = str(duration_frames)
            
            c_rate = ET.SubElement(clipitem, "rate")
            c_tb = ET.SubElement(c_rate, "timebase")
            c_tb.text = str(nominal_base)
            c_ntsc = ET.SubElement(c_rate, "ntsc")
            c_ntsc.text = ntsc_str
            
            c_start = ET.SubElement(clipitem, "start")
            c_start.text = str(clip_start)
            
            c_end = ET.SubElement(clipitem, "end")
            c_end.text = str(clip_end)
            
            c_in = ET.SubElement(clipitem, "in")
            c_in.text = str(start_frame)
            
            c_out = ET.SubElement(clipitem, "out")
            c_out.text = str(end_frame)
            
            if f_info:
                _attach_file_element(clipitem, f_info)

            # Dialogue marker (disabled by default to avoid timeline clutter)
            if include_clip_markers:
                marker = ET.SubElement(clipitem, "marker")
                m_comment = ET.SubElement(marker, "comment")
                m_comment.text = f"{item.text}\nLinks: {', '.join(item.links) if item.links else 'None'}"
                if local_matched:
                    m_comment.text += f"\nLocal Footage: {local_matched}"
                m_in = ET.SubElement(marker, "in")
                m_in.text = str(start_frame)
                m_out = ET.SubElement(marker, "out")
                m_out.text = str(end_frame)

            # Corresponding audio on A1 track if media is linked AND file actually has audio
            if f_info and f_info.get("has_audio"):
                a_item = ET.SubElement(a_track, "clipitem", id=f"{clip_id}-a1")
                ET.SubElement(a_item, "name").text = f"{clip_name} (Audio)"
                ET.SubElement(a_item, "duration").text = str(duration_frames)
                ac_rate = ET.SubElement(a_item, "rate")
                ET.SubElement(ac_rate, "timebase").text = str(nominal_base)
                ET.SubElement(ac_rate, "ntsc").text = ntsc_str
                ET.SubElement(a_item, "start").text = str(clip_start)
                ET.SubElement(a_item, "end").text = str(clip_end)
                ET.SubElement(a_item, "in").text = str(start_frame)
                ET.SubElement(a_item, "out").text = str(end_frame)
                _attach_file_element(a_item, f_info)

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
    include_clip_markers: bool = False,
) -> None:
    """Exports DocumentState to Premiere Pro FCP XML file with local footage links."""
    if fps is None:
        fps_str = db.get_setting("timeline_framerate", "23.976")
        try:
            fps = float(fps_str)
        except Exception:
            fps = 23.976

    xml_content = generate_fcp_xml(
        doc_state,
        footage_folder=footage_folder,
        fps=fps,
        linked_footage=linked_footage,
        include_clip_markers=include_clip_markers,
    )
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(xml_content)


def build_default_assembly_segments_from_locks(
    doc_state: DocumentState,
    fps: float = 23.976,
    linked_footage: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Constructs a linear list of NARRATION_PACK and AROLL_CLIP segments from the
    VO locks stored on doc_state.items and doc_state.vo_segments, accurately conforming
    and slicing narration across multi-part VO files.
    """
    linked_map = linked_footage or {}
    exact_fps, nominal_base, _, _ = analyze_framerate(fps)

    from .vo_engine import get_vo_manifest, slice_narration_pack_across_parts
    vo_manifest = get_vo_manifest(doc_state)
    files_manifest = vo_manifest.get("files", [])
    vo_total_sec = float(vo_manifest.get("total_duration_sec", 0.0))

    if vo_total_sec <= 0.0 and getattr(doc_state, "vo_segments", None):
        try:
            vo_total_sec = max(float(s.get("global_out", s.get("end", 0.0))) for s in doc_state.vo_segments)
        except Exception:
            vo_total_sec = 0.0

    locked_items = [
        it for it in doc_state.items
        if getattr(it, "vo_cut_after_sec", None) is not None
    ]
    locked_items.sort(key=lambda x: float(x.vo_cut_after_sec or 0.0))

    segments: List[Dict[str, Any]] = []
    cursor_sec = 0.0
    seg_idx = 1

    for item in locked_items:
        cut_sec = max(cursor_sec, float(item.vo_cut_after_sec or cursor_sec))
        if cut_sec - cursor_sec > 0.04:
            pack_slices = slice_narration_pack_across_parts(
                cursor_sec,
                cut_sec,
                files_manifest=files_manifest,
                has_adlib=bool(getattr(item, "vo_has_adlib", False)),
                adlib_note=getattr(item, "vo_adlib_note", ""),
                lead_in_text=getattr(item, "vo_lead_in_text", ""),
            )
            for ps in pack_slices:
                ps["index"] = seg_idx
                segments.append(ps)
                seg_idx += 1

        in_frame = parse_timecode_to_frames(item.start_timecode, exact_fps)
        out_frame = parse_timecode_to_frames(item.end_timecode, exact_fps) if item.end_timecode else (in_frame + nominal_base * 2)
        dur_frames = max(nominal_base, out_frame - in_frame)

        local_matched = ""
        for lk in (item.links or []):
            norm_lk = normalize_link(lk)
            cand = linked_map.get(norm_lk) or linked_map.get(lk)
            if cand:
                local_matched = cand
                break

        segments.append({
            "index": seg_idx,
            "type": "AROLL_CLIP",
            "script_item_id": item.id,
            "row_index": item.row_index,
            "meta": item.meta,
            "text": item.text,
            "smpte_in": item.start_timecode,
            "smpte_out": item.end_timecode or item.start_timecode,
            "in_frame": in_frame,
            "out_frame": out_frame,
            "duration_frames": dur_frames,
            "matched_footage_path": local_matched,
        })
        seg_idx += 1

        resume_sec = getattr(item, "vo_resume_sec", None)
        cursor_sec = float(resume_sec) if (resume_sec is not None and float(resume_sec) >= cut_sec) else cut_sec

    if vo_total_sec > cursor_sec + 0.05:
        pack_slices = slice_narration_pack_across_parts(
            cursor_sec,
            vo_total_sec,
            files_manifest=files_manifest,
            has_adlib=False,
            adlib_note="",
            lead_in_text="Outro Narration",
        )
        for ps in pack_slices:
            ps["index"] = seg_idx
            segments.append(ps)
            seg_idx += 1

    return segments


def generate_assembled_fcp_xml(
    doc_state: DocumentState,
    vo_file_path: str = "",
    assembly_segments: Optional[List[Dict[str, Any]]] = None,
    linked_footage: Optional[Dict[str, str]] = None,
    fps: float = 23.976,
    include_clip_markers: bool = False,
) -> str:
    """
    Generates a Multi-Track Final Cut Pro 7 XML (xmeml v4) rough-cut assembly for Premiere Pro:
      - V1 (Video Track 1): Bound A-Roll Video Clips at rippled timeline positions.
      - A1 (Audio Track 1): Contiguous Voiceover Narration Packs (sliced around A-Roll cuts across multi-part VO).
      - A2 (Audio Track 2): Native A-Roll Audio Clips aligned beneath V1 A-Roll Video.
    """
    linked_map = linked_footage or {}
    vo_path = vo_file_path or getattr(doc_state, "vo_file_path", "")
    exact_fps, nominal_base, _, _ = analyze_framerate(fps)
    is_ntsc = abs(exact_fps - 23.976) < 0.01 or abs(exact_fps - 29.97) < 0.01 or abs(exact_fps - 59.94) < 0.01
    ntsc_str = "TRUE" if is_ntsc else "FALSE"

    from .vo_engine import get_vo_manifest, slice_narration_pack_across_parts
    vo_manifest = get_vo_manifest(doc_state)
    files_manifest = vo_manifest.get("files", [])

    raw_segments = assembly_segments
    if not raw_segments:
        raw_segments = getattr(doc_state, "timeline_assembly", None) or build_default_assembly_segments_from_locks(
            doc_state, fps=exact_fps, linked_footage=linked_map
        )

    # Normalize narration segments so any multi-part audio is cleanly sliced and bound
    segments: List[Dict[str, Any]] = []
    seg_idx = 1
    for seg in (raw_segments or []):
        s_type = (seg.get("type") or "").upper()
        if s_type == "NARRATION_PACK":
            src_file = seg.get("source_file", "")
            # If no source_file or source_file missing from disk and we have multi-part manifest:
            if (not src_file or not os.path.isfile(src_file)) and files_manifest:
                s_in = float(seg.get("source_in_sec", 0.0))
                s_out = float(seg.get("source_out_sec", 0.0))
                slices = slice_narration_pack_across_parts(
                    s_in,
                    s_out,
                    files_manifest=files_manifest,
                    has_adlib=bool(seg.get("has_adlib", False)),
                    adlib_note=seg.get("adlib_note", ""),
                    lead_in_text=seg.get("lead_in_text", ""),
                )
                for sl in slices:
                    sl["index"] = seg_idx
                    segments.append(sl)
                    seg_idx += 1
            else:
                seg_copy = dict(seg)
                seg_copy["index"] = seg_idx
                segments.append(seg_copy)
                seg_idx += 1
        else:
            seg_copy = dict(seg)
            seg_copy["index"] = seg_idx
            segments.append(seg_copy)
            seg_idx += 1

    items_by_id = {it.id: it for it in doc_state.items}
    items_by_row = {it.row_index: it for it in doc_state.items}

    xmeml = ET.Element("xmeml", version="4")
    seq = ET.SubElement(xmeml, "sequence")

    name_el = ET.SubElement(seq, "name")
    name_el.text = f"{doc_state.name or 'Rainy'} — Pre-Cut Assembly"

    rate_el = ET.SubElement(seq, "rate")
    ET.SubElement(rate_el, "timebase").text = str(nominal_base)
    ET.SubElement(rate_el, "ntsc").text = ntsc_str

    media = ET.SubElement(seq, "media")

    # Video Section (Track V1 = A-Roll Video)
    video = ET.SubElement(media, "video")
    v_format = ET.SubElement(video, "format")
    v_sc = ET.SubElement(v_format, "samplecharacteristics")
    ET.SubElement(v_sc, "width").text = "1920"
    ET.SubElement(v_sc, "height").text = "1080"
    v1_track = ET.SubElement(video, "track")

    # Audio Section (Track A1 = Voiceover, Track A2 = A-Roll Native Audio)
    audio = ET.SubElement(media, "audio")
    a_format = ET.SubElement(audio, "format")
    a_sc = ET.SubElement(a_format, "samplecharacteristics")
    ET.SubElement(a_sc, "depth").text = "16"
    ET.SubElement(a_sc, "samplerate").text = "48000"
    a1_vo_track = ET.SubElement(audio, "track")
    a2_aroll_track = ET.SubElement(audio, "track")

    seen_file_ids = set()
    files_meta_cache: Dict[str, Dict[str, Any]] = {}

    def _get_assembled_file_info(fpath: str) -> Dict[str, Any]:
        norm_p = os.path.normpath(fpath)
        if norm_p in files_meta_cache:
            return files_meta_cache[norm_p]
        ext = os.path.splitext(norm_p)[1].lower()
        is_pure_audio = ext in AUDIO_EXTS
        is_image = ext in IMAGE_EXTS
        from .media_probe import probe_media_file
        probe_info = probe_media_file(norm_p) if os.path.isfile(norm_p) else None
        if probe_info:
            has_v = bool(probe_info.has_video)
            has_a = bool(probe_info.has_audio)
            if not has_v and not has_a:
                has_v = not is_pure_audio
                has_a = is_pure_audio or (not is_image)
            w = probe_info.width or 1920
            h = probe_info.height or 1080
            sr = probe_info.sample_rate or 48000
            ch = probe_info.audio_channels or 2
            dur_f = int(round(probe_info.duration_seconds * exact_fps)) if probe_info.duration_seconds > 0 else 0
        else:
            has_v = not is_pure_audio
            has_a = is_pure_audio or (not is_image)
            w, h = 1920, 1080
            sr, ch = 48000, 2
            dur_f = 0

        file_id = f"file-media-{abs(hash(norm_p)) % 1000000}"
        meta = {
            "id": file_id,
            "name": os.path.basename(fpath),
            "pathurl": _format_file_url(fpath),
            "has_video": has_v,
            "has_audio": has_a,
            "width": w,
            "height": h,
            "sample_rate": sr,
            "channels": ch,
            "duration_frames": dur_f,
        }
        files_meta_cache[norm_p] = meta
        return meta

    def _attach_assembled_file(parent_el: ET.Element, f_info: Dict[str, Any], needed_dur: int):
        fid = f_info["id"]
        f_el = ET.SubElement(parent_el, "file", id=fid)
        if fid not in seen_file_ids:
            seen_file_ids.add(fid)
            ET.SubElement(f_el, "name").text = f_info["name"]
            ET.SubElement(f_el, "pathurl").text = f_info["pathurl"]
            f_rate = ET.SubElement(f_el, "rate")
            ET.SubElement(f_rate, "timebase").text = str(nominal_base)
            ET.SubElement(f_rate, "ntsc").text = ntsc_str
            dur_val = f_info.get("duration_frames") or needed_dur
            ET.SubElement(f_el, "duration").text = str(max(dur_val, needed_dur))
            m_el = ET.SubElement(f_el, "media")
            if f_info.get("has_video", True):
                vm = ET.SubElement(m_el, "video")
                vm_sc = ET.SubElement(vm, "samplecharacteristics")
                ET.SubElement(vm_sc, "width").text = str(f_info.get("width", 1920))
                ET.SubElement(vm_sc, "height").text = str(f_info.get("height", 1080))
            if f_info.get("has_audio", False):
                am = ET.SubElement(m_el, "audio")
                am_sc = ET.SubElement(am, "samplecharacteristics")
                ET.SubElement(am_sc, "depth").text = "16"
                ET.SubElement(am_sc, "samplerate").text = str(f_info.get("sample_rate", 48000))
                ET.SubElement(am, "channelcount").text = str(f_info.get("channels", 2))

    curr_timeline_frame = 0
    vo_clip_counter = 0
    aroll_clip_counter = 0

    for seg in segments:
        seg_type = (seg.get("type") or "").upper()

        if seg_type == "NARRATION_PACK":
            src_in_sec = float(seg.get("source_in_sec", 0.0))
            src_out_sec = float(seg.get("source_out_sec", 0.0))
            in_frame = max(0, int(round(src_in_sec * exact_fps)))
            out_frame = max(in_frame + 1, int(round(src_out_sec * exact_fps)))
            dur_frames = out_frame - in_frame

            tl_start = curr_timeline_frame
            tl_end = curr_timeline_frame + dur_frames
            curr_timeline_frame = tl_end
            vo_clip_counter += 1

            a1_item = ET.SubElement(a1_vo_track, "clipitem", id=f"vo-clipitem-{vo_clip_counter}")
            part_suffix = f" (Part {seg['part_id']})" if seg.get("part_id") else ""
            label_suffix = " [AD-LIB]" if seg.get("has_adlib") else ""
            ET.SubElement(a1_item, "name").text = f"VO Pack {vo_clip_counter}{part_suffix}{label_suffix}"
            ET.SubElement(a1_item, "duration").text = str(dur_frames)

            c_rate = ET.SubElement(a1_item, "rate")
            ET.SubElement(c_rate, "timebase").text = str(nominal_base)
            ET.SubElement(c_rate, "ntsc").text = ntsc_str

            ET.SubElement(a1_item, "start").text = str(tl_start)
            ET.SubElement(a1_item, "end").text = str(tl_end)
            ET.SubElement(a1_item, "in").text = str(in_frame)
            ET.SubElement(a1_item, "out").text = str(out_frame)

            clip_vo = seg.get("source_file") or vo_path
            if clip_vo:
                vo_info = _get_assembled_file_info(clip_vo)
                vo_info["has_video"] = False
                vo_info["has_audio"] = True
                _attach_assembled_file(a1_item, vo_info, dur_frames + nominal_base * 10)

            if seg.get("has_adlib") or seg.get("adlib_note"):
                marker = ET.SubElement(a1_item, "marker")
                ET.SubElement(marker, "name").text = "VO Ad-lib / Modification"
                ET.SubElement(marker, "comment").text = str(seg.get("adlib_note") or seg.get("lead_in_text") or "Modified VO")
                ET.SubElement(marker, "in").text = str(in_frame)
                ET.SubElement(marker, "out").text = str(out_frame)

        elif seg_type == "AROLL_CLIP":
            item_obj = items_by_id.get(seg.get("script_item_id", "")) or items_by_row.get(seg.get("row_index", -1))

            if "in_frame" in seg and "out_frame" in seg:
                in_frame = int(seg["in_frame"])
                out_frame = int(seg["out_frame"])
            elif item_obj:
                in_frame = parse_timecode_to_frames(item_obj.start_timecode, exact_fps)
                out_frame = parse_timecode_to_frames(item_obj.end_timecode, exact_fps)
            else:
                in_frame = parse_timecode_to_frames(seg.get("smpte_in", "00:00:00:00"), exact_fps)
                out_frame = parse_timecode_to_frames(seg.get("smpte_out", "00:00:02:00"), exact_fps)

            if out_frame <= in_frame:
                dur_frames = nominal_base * 2
                out_frame = in_frame + dur_frames
            else:
                dur_frames = out_frame - in_frame
            tl_start = curr_timeline_frame
            tl_end = curr_timeline_frame + dur_frames
            curr_timeline_frame = tl_end
            aroll_clip_counter += 1

            local_matched = seg.get("matched_footage_path", "")
            if not local_matched and item_obj:
                for lk in (item_obj.links or []):
                    norm_lk = normalize_link(lk)
                    cand = linked_map.get(norm_lk) or linked_map.get(lk)
                    if cand:
                        local_matched = cand
                        break

            clip_title = seg.get("meta") or (item_obj.meta if item_obj else f"CLIP {aroll_clip_counter}")
            clip_text = seg.get("text") or (item_obj.text if item_obj else "")

            f_info = _get_assembled_file_info(local_matched) if local_matched else None
            has_v = f_info["has_video"] if f_info else True
            has_a = f_info["has_audio"] if f_info else False
            if not has_v and not has_a:
                has_v = True
            needed_dur = max(out_frame + nominal_base * 10, dur_frames)

            # 1. V1 Track ClipItem (A-Roll Video) - placed ONLY if asset has video
            v1_item = None
            v1_id = f"v1-aroll-{aroll_clip_counter}"
            if has_v:
                v1_item = ET.SubElement(v1_track, "clipitem", id=v1_id)
                ET.SubElement(v1_item, "name").text = f"[A-ROLL] {clip_title} {clip_text[:28]}".strip()
                ET.SubElement(v1_item, "duration").text = str(dur_frames)
                v_rate = ET.SubElement(v1_item, "rate")
                ET.SubElement(v_rate, "timebase").text = str(nominal_base)
                ET.SubElement(v_rate, "ntsc").text = ntsc_str
                ET.SubElement(v1_item, "start").text = str(tl_start)
                ET.SubElement(v1_item, "end").text = str(tl_end)
                ET.SubElement(v1_item, "in").text = str(in_frame)
                ET.SubElement(v1_item, "out").text = str(out_frame)

                if f_info:
                    _attach_assembled_file(v1_item, f_info, needed_dur)

                v_src = ET.SubElement(v1_item, "sourcetrack")
                ET.SubElement(v_src, "mediatype").text = "video"
                ET.SubElement(v_src, "trackindex").text = "1"

                if include_clip_markers:
                    v_marker = ET.SubElement(v1_item, "marker")
                    ET.SubElement(v_marker, "name").text = clip_title
                    ET.SubElement(v_marker, "comment").text = clip_text
                    ET.SubElement(v_marker, "in").text = str(in_frame)
                    ET.SubElement(v_marker, "out").text = str(out_frame)

            # 2. A2 Track ClipItem (A-Roll Native Audio) - placed ONLY if asset has native audio
            a2_item = None
            a2_id = f"a2-aroll-{aroll_clip_counter}"
            if has_a:
                a2_item = ET.SubElement(a2_aroll_track, "clipitem", id=a2_id)
                ET.SubElement(a2_item, "name").text = f"[A-ROLL AUDIO] {clip_title}"
                ET.SubElement(a2_item, "duration").text = str(dur_frames)
                a_rate = ET.SubElement(a2_item, "rate")
                ET.SubElement(a_rate, "timebase").text = str(nominal_base)
                ET.SubElement(a_rate, "ntsc").text = ntsc_str
                ET.SubElement(a2_item, "start").text = str(tl_start)
                ET.SubElement(a2_item, "end").text = str(tl_end)
                ET.SubElement(a2_item, "in").text = str(in_frame)
                ET.SubElement(a2_item, "out").text = str(out_frame)

                if f_info:
                    _attach_assembled_file(a2_item, f_info, needed_dur)

                a_src = ET.SubElement(a2_item, "sourcetrack")
                ET.SubElement(a_src, "mediatype").text = "audio"
                ET.SubElement(a_src, "trackindex").text = "1"

                if include_clip_markers and not v1_item:
                    a_marker = ET.SubElement(a2_item, "marker")
                    ET.SubElement(a_marker, "name").text = clip_title
                    ET.SubElement(a_marker, "comment").text = clip_text
                    ET.SubElement(a_marker, "in").text = str(in_frame)
                    ET.SubElement(a_marker, "out").text = str(out_frame)

            # 3. Synchronized Link Definition between V1 and A2
            # Explicitly links the video and audio clipitems so Premiere Pro treats them as connected clips
            if v1_item is not None and a2_item is not None:
                v1_clip_idx = len(v1_track.findall("clipitem"))
                a2_clip_idx = len(a2_aroll_track.findall("clipitem"))

                # Link definitions inside V1 clipitem
                v_link1 = ET.SubElement(v1_item, "link")
                ET.SubElement(v_link1, "linkclipref").text = v1_id
                ET.SubElement(v_link1, "mediatype").text = "video"
                ET.SubElement(v_link1, "trackindex").text = "1"
                ET.SubElement(v_link1, "clipindex").text = str(v1_clip_idx)

                v_link2 = ET.SubElement(v1_item, "link")
                ET.SubElement(v_link2, "linkclipref").text = a2_id
                ET.SubElement(v_link2, "mediatype").text = "audio"
                ET.SubElement(v_link2, "trackindex").text = "2"
                ET.SubElement(v_link2, "clipindex").text = str(a2_clip_idx)
                ET.SubElement(v_link2, "groupindex").text = "1"

                # Link definitions inside A2 clipitem
                a_link1 = ET.SubElement(a2_item, "link")
                ET.SubElement(a_link1, "linkclipref").text = v1_id
                ET.SubElement(a_link1, "mediatype").text = "video"
                ET.SubElement(a_link1, "trackindex").text = "1"
                ET.SubElement(a_link1, "clipindex").text = str(v1_clip_idx)

                a_link2 = ET.SubElement(a2_item, "link")
                ET.SubElement(a_link2, "linkclipref").text = a2_id
                ET.SubElement(a_link2, "mediatype").text = "audio"
                ET.SubElement(a_link2, "trackindex").text = "2"
                ET.SubElement(a_link2, "clipindex").text = str(a2_clip_idx)
                ET.SubElement(a_link2, "groupindex").text = "1"

    ET.SubElement(seq, "duration").text = str(curr_timeline_frame)
    xml_str = ET.tostring(xmeml, encoding="utf-8")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n{xml_str.decode("utf-8")}'


def export_assembled_fcp_xml_file(
    file_path: str,
    doc_state: DocumentState,
    vo_file_path: str = "",
    assembly_segments: Optional[List[Dict[str, Any]]] = None,
    linked_footage: Optional[Dict[str, str]] = None,
    fps: Optional[float] = None,
    include_clip_markers: bool = False,
) -> str:
    """Writes the multi-track (V1/A2 A-Roll + A1 Voiceover) FCP7 XML file to disk."""
    if fps is None:
        fps_str = db.get_setting("timeline_framerate", "23.976")
        try:
            fps = float(fps_str)
        except Exception:
            fps = 23.976

    xml_content = generate_assembled_fcp_xml(
        doc_state=doc_state,
        vo_file_path=vo_file_path,
        assembly_segments=assembly_segments,
        linked_footage=linked_footage,
        fps=fps,
        include_clip_markers=include_clip_markers,
    )
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    return os.path.abspath(file_path)
