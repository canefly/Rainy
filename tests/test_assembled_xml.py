import xml.etree.ElementTree as ET
from src.core.models import DocumentState, ScriptItem
from src.core.xml_exporter import generate_assembled_fcp_xml, build_default_assembly_segments_from_locks


def test_assembled_multi_track_fcp_xml_v1_a2_aroll_and_a1_vo():
    item1 = ScriptItem(
        id="aroll-1",
        row_index=1,
        raw_timestamp="20:52 - 21:07",
        start_timecode="00:20:52:00",
        start_seconds=1252.0,
        end_timecode="00:21:07:00",
        end_seconds=1267.0,
        duration_seconds=15.0,
        duration_display="15s",
        text="Sam interrogation clip",
        meta="S01E02",
        sync_id=2,
        links=["https://drive.google.com/file/d/clip1"],
        is_checked=True,
        vo_locked=True,
        vo_cut_after_sec=10.0,
        vo_resume_sec=11.0,
        vo_lead_in_text="Watch his reaction here.",
        vo_has_adlib=True,
        vo_adlib_note="Kept 2s lead-in adlib",
        vo_status="LOCKED",
    )

    doc = DocumentState(
        id="doc-xml-test",
        file_signature="sig-xml-test",
        name="Assembly_Test",
        file_path="D:/Project/Assembly_Test.docx",
        raw_html="",
        items=[item1],
        vo_file_path="D:/Project/Audio/VO_Master.wav",
        vo_segments=[
            {"id": 0, "start": 0.0, "end": 10.0, "text": "Intro and adlib."},
            {"id": 1, "start": 11.0, "end": 25.0, "text": "Post-clip narration."},
        ],
    )

    linked = {"https://drive.google.com/file/d/clip1": "D:/Project/Footage/Sam_Interview.mp4"}

    segments = build_default_assembly_segments_from_locks(doc, fps=24.0, linked_footage=linked)
    assert len(segments) == 3
    assert segments[0]["type"] == "NARRATION_PACK"
    assert segments[0]["source_in_sec"] == 0.0
    assert segments[0]["source_out_sec"] == 10.0
    assert segments[1]["type"] == "AROLL_CLIP"
    assert segments[2]["type"] == "NARRATION_PACK"
    assert segments[2]["source_in_sec"] == 11.0
    assert segments[2]["source_out_sec"] == 25.0

    xml_str = generate_assembled_fcp_xml(
        doc_state=doc,
        vo_file_path=doc.vo_file_path,
        assembly_segments=segments,
        linked_footage=linked,
        fps=24.0,
    )

    root = ET.fromstring(xml_str.split("<!DOCTYPE xmeml>\n", 1)[1])
    seq = root.find("sequence")
    assert seq is not None

    # Verify V1 (A-Roll Video)
    v_tracks = seq.findall("./media/video/track")
    assert len(v_tracks) == 1
    v1_clips = v_tracks[0].findall("clipitem")
    assert len(v1_clips) == 1
    # First VO pack is 10s = 240 frames -> A-Roll starts at frame 240 and lasts 15s (360 frames) -> ends at 600
    assert int(v1_clips[0].find("start").text) == 240
    assert int(v1_clips[0].find("end").text) == 600

    # Verify Audio Tracks: A1 = Voiceover Packs, A2 = A-Roll Native Audio
    a_tracks = seq.findall("./media/audio/track")
    assert len(a_tracks) == 2
    a1_vo_clips = a_tracks[0].findall("clipitem")
    a2_aroll_clips = a_tracks[1].findall("clipitem")

    assert len(a1_vo_clips) == 2
    assert int(a1_vo_clips[0].find("start").text) == 0
    assert int(a1_vo_clips[0].find("end").text) == 240
    # Second VO pack ripples after A-Roll (starts at frame 600)
    assert int(a1_vo_clips[1].find("start").text) == 600

    assert len(a2_aroll_clips) == 1
    assert int(a2_aroll_clips[0].find("start").text) == 240
    assert int(a2_aroll_clips[0].find("end").text) == 600

    # Verify that individual clip markers are NOT added by default
    assert v1_clips[0].find("marker") is None
    assert a2_aroll_clips[0].find("marker") is None


def test_assembled_xml_stream_aware_audio_only_and_silent_video():
    # item1 is pure audio (e.g. 911 call wav)
    item_audio = ScriptItem(
        id="item-audio-only",
        row_index=1,
        raw_timestamp="00:00 - 00:05",
        start_timecode="00:00:00:00",
        start_seconds=0.0,
        end_timecode="00:00:05:00",
        end_seconds=5.0,
        duration_seconds=5.0,
        duration_display="5s",
        text="911 Call Recording",
        meta="AudioOnly",
        links=["https://drive.google.com/audio1"],
        is_checked=True,
        vo_cut_after_sec=5.0,
        vo_status="LOCKED",
    )
    # item2 is silent image/video (e.g. photo jpg)
    item_photo = ScriptItem(
        id="item-photo-only",
        row_index=2,
        raw_timestamp="00:00 - 00:03",
        start_timecode="00:00:00:00",
        start_seconds=0.0,
        end_timecode="00:00:03:00",
        end_seconds=3.0,
        duration_seconds=3.0,
        duration_display="3s",
        text="Crime scene photo",
        meta="Photo",
        links=["https://drive.google.com/photo1"],
        is_checked=True,
        vo_cut_after_sec=8.0,
        vo_status="LOCKED",
    )

    doc = DocumentState(
        id="doc-stream-test",
        file_signature="sig-stream-test",
        name="Stream_Test",
        file_path="D:/Project/Stream_Test.docx",
        raw_html="",
        items=[item_audio, item_photo],
        vo_file_path="D:/Project/Audio/VO_Part1.wav",
        vo_segments=[
            {"id": 0, "start": 0.0, "end": 5.0, "text": "Intro VO"},
            {"id": 1, "start": 5.0, "end": 8.0, "text": "Mid VO"},
            {"id": 2, "start": 8.0, "end": 12.0, "text": "Outro VO"},
        ],
    )

    linked = {
        "https://drive.google.com/audio1": "D:/Project/Footage/911_Call_Audio.wav",
        "https://drive.google.com/photo1": "D:/Project/Footage/Evidence_Photo.jpg",
    }

    xml_str = generate_assembled_fcp_xml(
        doc_state=doc,
        vo_file_path=doc.vo_file_path,
        linked_footage=linked,
        fps=24.0,
    )

    root = ET.fromstring(xml_str.split("<!DOCTYPE xmeml>\n", 1)[1])
    seq = root.find("sequence")
    assert seq is not None

    v1_track = seq.find("./media/video/track")
    a_tracks = seq.findall("./media/audio/track")
    a1_vo_track = a_tracks[0]
    a2_aroll_track = a_tracks[1]

    v1_clips = v1_track.findall("clipitem")
    a2_clips = a2_aroll_track.findall("clipitem")

    # Pure audio item must NOT be placed on V1, but MUST be placed on A2!
    v1_names = [c.find("name").text for c in v1_clips]
    assert not any("911 Call Recording" in n for n in v1_names), "Audio-only clip should NOT be on V1"

    a2_names = [c.find("name").text for c in a2_clips]
    assert any("AudioOnly" in n for n in a2_names), "Audio-only clip MUST be on A2"

    # Photo item must be placed on V1, but must NOT be placed on A2!
    assert any("Photo" in n for n in v1_names), "Photo clip MUST be on V1"
    assert not any("Photo" in n for n in a2_names), "Photo clip should NOT be on A2"

    # Verify that the WAV file element on A2 has ONLY <media><audio> and NO <media><video>
    wav_clip = next(c for c in a2_clips if "AudioOnly" in c.find("name").text)
    wav_file = wav_clip.find("file")
    assert wav_file is not None
    assert wav_file.find("./media/audio") is not None
    assert wav_file.find("./media/video") is None, "WAV file must not declare video stream in XML"

    # Verify that the JPG file element on V1 has ONLY <media><video> and NO <media><audio>
    jpg_clip = next(c for c in v1_clips if "Photo" in c.find("name").text)
    jpg_file = jpg_clip.find("file")
    assert jpg_file is not None
    assert jpg_file.find("./media/video") is not None
    assert jpg_file.find("./media/audio") is None, "Image file must not declare audio stream in XML"

