import uuid
import time
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

@dataclass
class LinkItem:
    url: str
    domain: str
    title: str = ""
    thumbnail_url: Optional[str] = None
    is_checked: bool = False

@dataclass
class EditorNote:
    id: str
    block_id: int
    text: str
    comment: Optional[str] = None
    is_checked: bool = False
    timestamp: float = field(default_factory=time.time)

@dataclass
class VOSegment:
    id: int
    start: float
    end: float
    text: str


@dataclass
class BlockVODiff:
    block_id: int
    vo_segment_ids: List[int] = field(default_factory=list)
    vo_start_sec: float = 0.0
    vo_end_sec: float = 0.0
    vo_actual_text: str = ""
    match_type: str = "EXACT"  # EXACT | MODIFIED | ADLIB | RETAKE_CUT
    adlib_note: str = ""


@dataclass
class ScriptItem:
    id: str
    row_index: int
    raw_timestamp: str
    start_timecode: str           # Normalized HH:MM:SS:FF
    start_seconds: float
    end_timecode: str             # Normalized HH:MM:SS:FF
    end_seconds: float
    duration_seconds: float
    duration_display: str         # e.g. "2s", "44s", "1m 15s"
    text: str
    speaker: str = "Host"
    meta: str = ""
    is_single: bool = False
    sync_id: int = 0
    links: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    is_checked: bool = False
    has_warning: bool = False
    warning_reason: str = ""
    # Voiceover (VO) AI Assembly Lock state
    vo_locked: bool = False
    vo_cut_after_sec: Optional[float] = None
    vo_resume_sec: Optional[float] = None
    vo_lead_in_text: str = ""
    vo_has_adlib: bool = False
    vo_adlib_note: str = ""
    vo_status: str = "PENDING"    # PENDING | LOCKED | NEEDS_REVIEW

    def __post_init__(self):
        if not self.meta:
            self.meta = f"CLIP {self.row_index}"
        if not self.end_timecode:
            self.is_single = True

@dataclass
class GamificationStats:
    lifetime_seconds: int = 0
    total_aroll_count: int = 0
    total_duration_logged: int = 0
    last_used_date: str = ""

@dataclass
class DocumentState:
    id: str
    file_signature: str
    name: str
    file_path: str
    raw_html: str
    items: List[ScriptItem] = field(default_factory=list)
    notes: List[EditorNote] = field(default_factory=list)
    custom_notes: List[EditorNote] = field(default_factory=list)
    headings: List[Dict[str, Any]] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    checked_links: Dict[str, bool] = field(default_factory=dict)
    checked_notes: Dict[str, bool] = field(default_factory=dict)
    link_colors: Dict[str, str] = field(default_factory=dict)
    page_geometry: Dict[str, float] = field(default_factory=lambda: {
        "width_pt": 612.0,
        "height_pt": 792.0,
        "margin_top_pt": 72.0,
        "margin_bottom_pt": 72.0,
        "margin_left_pt": 72.0,
        "margin_right_pt": 72.0,
    })
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    vo_file_path: str = ""
    vo_files: List[str] = field(default_factory=list)
    vo_transcript_path: str = ""
    vo_segments: List[Dict[str, Any]] = field(default_factory=list)
    vo_block_diffs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    timeline_assembly: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    is_deleted: bool = False
