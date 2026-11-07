import re
from typing import Optional, Tuple, Dict, Any


# Standard broadcast / cinema nominal framerates
STANDARD_RATES = {
    23.976: 24,
    24.0: 24,
    25.0: 25,
    29.97: 30,
    30.0: 30,
    50.0: 50,
    59.94: 60,
    60.0: 60,
}


def parse_timestamp_seconds(timestamp: str) -> float:
    """
    Parses any script or timecode string into elapsed real-world seconds (PTS).
    
    Supported formats:
      - "MM:SS" (e.g. "11:35" -> 695.0s, "15:30.500" -> 930.5s)
      - "HH:MM:SS" (e.g. "01:15:30" -> 4530.0s)
      - "HH:MM:SS:FF" or "HH:MM:SS;FF" (standard SMPTE timecode)
      - "SS" or float/int seconds string (e.g. "695.0")
    """
    if not timestamp or not isinstance(timestamp, str):
        return 0.0

    s = timestamp.strip()
    if not s:
        return 0.0

    # Replace drop-frame semicolon with colon for uniform parsing
    s_clean = s.replace(";", ":")
    parts = s_clean.split(":")

    try:
        if len(parts) == 1:
            return max(0.0, float(parts[0]))
        elif len(parts) == 2:
            # MM:SS or MM:SS.mmm
            m = int(parts[0])
            sec = float(parts[1])
            return max(0.0, m * 60.0 + sec)
        elif len(parts) == 3:
            # HH:MM:SS or HH:MM:SS.mmm
            h = int(parts[0])
            m = int(parts[1])
            sec = float(parts[2])
            return max(0.0, h * 3600.0 + m * 60.0 + sec)
        elif len(parts) == 4:
            # HH:MM:SS:FF (assume default 24fps frame duration for raw float conversion if unspecified)
            h = int(parts[0])
            m = int(parts[1])
            sec = int(parts[2])
            frames = float(parts[3])
            return max(0.0, h * 3600.0 + m * 60.0 + sec + (frames / 24.0))
    except (ValueError, TypeError):
        pass

    # Regex fallback for embedded numbers like "15m 30s"
    m_match = re.search(r"(\d+)\s*m(?:in)?(?:\s*(\d+(?:\.\d+)?)\s*s)?", s, re.I)
    if m_match:
        mins = int(m_match.group(1))
        secs = float(m_match.group(2) or 0)
        return mins * 60.0 + secs

    return 0.0


def analyze_framerate(r_fps: float, avg_fps: float = 0.0) -> Tuple[float, int, bool, bool]:
    """
    Analyzes probed frame rate streams to determine nominal FPS and detect VFR.
    
    Returns:
        (exact_fps, nominal_base_fps, is_vfr, is_standard)
    """
    raw_fps = r_fps if r_fps > 0 else avg_fps
    if raw_fps <= 0:
        return (23.976, 24, False, True)

    is_vfr = False
    if r_fps > 0 and avg_fps > 0:
        if abs(r_fps - avg_fps) > 0.08:
            is_vfr = True

    # Check for known standard NTSC / ATSC / PAL rates: find closest match
    closest_rate = min(STANDARD_RATES.keys(), key=lambda r: abs(raw_fps - r))
    if abs(raw_fps - closest_rate) <= 0.04:
        return (closest_rate, STANDARD_RATES[closest_rate], is_vfr, True)

    # Integer framerates (e.g. 15 fps security cams, 12 fps animations)
    rounded = round(raw_fps)
    if abs(raw_fps - rounded) <= 0.04 and rounded > 0:
        return (float(rounded), rounded, is_vfr, True)

    # Non-standard / wild VFR rate (e.g. 28.47 fps phone video)
    nom = max(1, round(raw_fps))
    return (raw_fps, nom, True, False)


def smpte_to_frames(timecode: str, nominal_fps: int = 24) -> int:
    """Parses HH:MM:SS:FF or HH:MM:SS;FF into total integer frames."""
    if not timecode:
        return 0
    clean = timecode.replace(";", ":").strip()
    parts = clean.split(":")
    if len(parts) != 4:
        return 0
    try:
        h, m, s, f = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        return h * 3600 * nominal_fps + m * 60 * nominal_fps + s * nominal_fps + f
    except (ValueError, TypeError):
        return 0


def seconds_to_frames(seconds: float, exact_fps: float, nominal_fps: int) -> int:
    """Converts elapsed seconds to integer frame count based on exact FPS timing."""
    if seconds <= 0:
        return 0

    if abs(exact_fps - 23.976) < 0.01:
        # NTSC 23.976 (24000 / 1001)
        rate = 24000.0 / 1001.0
        return int(round(seconds * rate))
    elif abs(exact_fps - 29.97) < 0.01:
        # NTSC 29.970 (30000 / 1001)
        rate = 30000.0 / 1001.0
        return int(round(seconds * rate))
    elif abs(exact_fps - 59.94) < 0.01:
        # NTSC 59.940 (60000 / 1001)
        rate = 60000.0 / 1001.0
        return int(round(seconds * rate))
    else:
        return int(round(seconds * exact_fps))


def frames_to_smpte(total_frames: int, nominal_fps: int, drop_frame: bool = False) -> str:
    """
    Formats an integer frame count into a standard Premiere Pro SMPTE string.
    
    If drop_frame is True and nominal_fps == 30:
        Implements SMPTE 12M drop-frame timecode (drops frames :00 and :01 every minute except %10).
    Otherwise:
        Standard Non-Drop Frame (NDF) with colons HH:MM:SS:FF.
    """
    total_frames = max(0, int(total_frames))
    fps = max(1, int(nominal_fps))

    if drop_frame and fps == 30:
        # SMPTE 12M Drop Frame calculation for 29.97 fps (30 nominal)
        # Drops frames :00 and :01 every minute except every 10th minute (0, 10, 20...)
        frames_per_10m = 17982 # 1800 + 9 * 1798 frames
        d = total_frames // frames_per_10m
        m = total_frames % frames_per_10m

        if m >= 1800:
            dropped_in_block = 2 * ((m - 1800) // 1798 + 1)
        else:
            dropped_in_block = 0

        calc_frames = total_frames + 18 * d + dropped_in_block

        ff = calc_frames % 30
        ss = (calc_frames // 30) % 60
        mm = (calc_frames // 1800) % 60
        hh = calc_frames // 108000
        return f"{hh:02}:{mm:02}:{ss:02};{ff:02}"

    # Non-Drop Frame (Universal NLE Default)
    ff = total_frames % fps
    total_sec = total_frames // fps
    ss = total_sec % 60
    mm = (total_sec // 60) % 60
    hh = total_sec // 3600
    return f"{hh:02}:{mm:02}:{ss:02}:{ff:02}"


def conform_timestamp_for_clip(
    script_timestamp: str,
    probed_fps: float = 0.0,
    container_start_time: float = 0.0,
    embedded_timecode: str = "",
    is_vfr: bool = False,
    fallback_fps: float = 23.976,
    drop_frame: bool = False
) -> Tuple[str, Dict[str, Any]]:
    """
    Converts a scriptwriter's Google Drive elapsed timestamp (PTS) into an exact
    Premiere Pro SMPTE timecode (HH:MM:SS:FF) conformed to the linked footage.
    
    Compensates for:
      1. Container audio/video multiplex delays (start_time offset, e.g. +4.32s)
      2. Native file FPS vs sequence timeline FPS
      3. Embedded camera/broadcast start timecode tracks (e.g. 01:00:00:00)
    
    Returns:
        (smpte_timecode, metadata_dict)
    """
    raw_sec = parse_timestamp_seconds(script_timestamp)

    # 1. Determine active framerate
    target_fps = probed_fps if probed_fps > 0 else fallback_fps
    exact_fps, nominal_base, vfr_flag, is_standard = analyze_framerate(target_fps)
    if is_vfr:
        vfr_flag = True

    # 2. Factor in container start_time delay (e.g. audio pre-roll of 4.320s)
    # If the file has a non-zero start_time, the video in Premiere is shifted by that amount.
    compensated_sec = max(0.0, raw_sec + max(0.0, float(container_start_time)))

    # 3. Calculate frame count at target framerate
    frames = seconds_to_frames(compensated_sec, exact_fps, nominal_base)

    # 4. Factor in embedded clip timecode offset if present
    if embedded_timecode:
        emb_frames = smpte_to_frames(embedded_timecode, nominal_base)
        frames += emb_frames

    # 5. Format to SMPTE timecode
    smpte = frames_to_smpte(frames, nominal_base, drop_frame=drop_frame)

    meta = {
        "script_timestamp": script_timestamp,
        "raw_seconds": round(raw_sec, 3),
        "compensated_seconds": round(compensated_sec, 3),
        "start_time_offset": round(container_start_time, 3),
        "exact_fps": exact_fps,
        "nominal_fps": nominal_base,
        "is_vfr": vfr_flag,
        "is_standard": is_standard,
        "embedded_timecode": embedded_timecode,
        "total_frames": frames,
        "smpte": smpte
    }

    return smpte, meta
