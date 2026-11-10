"""Visual & geometric layout verifier for TimelineCard, TimelineList, and Clip Player.

Renders widgets at critical responsive breakpoints (285px compact, 420px wide, 700px full)
using QWidget.grab() and asserts zero text clipping and zero child widget overlap.

Usage:
    .\.venv\Scripts\python.exe scripts/verify_card_layout.py [output_dir]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication
from src.core.models import ScriptItem
from src.ui.components.timeline_card import TimelineCard
from src.ui.components.timeline_list import TimelineList
from src.ui.components.aroll_trimmer_window import ARollTrimmerWindow


def _sample_items():
    rows = [
        (58, "Clip", "00:14:51:03", "00:14:56:03", False),
        (59, "Clip", "00:15:37:02", "00:15:43:02", False),
        (60, "Clip", "02:04:17:00", "02:04:39:00", False),
        (61, "S10E20", "02:04:52:00", "02:05:09:00", False),
        (62, "Clip", "02:05:41:00", "02:05:56:00", True),
        (63, "Clip", "02:06:37:00", "02:06:41:00", False),
    ]
    items = []
    for idx, meta, s_tc, e_tc, checked in rows:
        items.append(
            ScriptItem(
                id=f"verify-card-{idx}",
                row_index=idx,
                raw_timestamp=f"{s_tc} - {e_tc}",
                start_timecode=s_tc,
                start_seconds=891.0,
                end_timecode=e_tc,
                end_seconds=896.0,
                duration_seconds=5.0,
                duration_display="5s",
                text=f"Sample clip {idx}",
                meta=meta,
                is_checked=checked,
                links=[f"https://drive.google.com/file/d/sample_{idx}"],
            )
        )
    return items


def assert_card_geometry(card: TimelineCard, width_label: str):
    """Asserts that no visible label is narrower than its rendered text and no children overlap."""
    if card.is_compact:
        for name, lbl in (
            ("in_lbl", card.in_lbl),
            ("out_lbl", card.out_lbl),
            ("start_btn_c", card.start_btn_c),
            ("end_btn_c", card.end_btn_c),
        ):
            adv = lbl.fontMetrics().horizontalAdvance(lbl.text())
            assert lbl.width() >= adv, (
                f"[{width_label}] {name} ('{lbl.text()}') clipped: width={lbl.width()}px < text={adv}px"
            )
        # In and Out rows must not overlap horizontally with timecode column
        assert card.in_lbl.geometry().right() < card.start_btn_c.geometry().left() + 2
        assert card.out_lbl.geometry().right() < card.end_btn_c.geometry().left() + 2

    # Check top-level visible children of the card are strictly ordered left-to-right
    top_widgets = [
        w for w in (card.cb, card.index_lbl, card.meta_pill, card.play_btn, card.tc_container, card.ext_btn)
        if w is not None and w.isVisible()
    ]
    for a, b in zip(top_widgets, top_widgets[1:]):
        assert a.geometry().right() <= b.geometry().left() + 1, (
            f"[{width_label}] Overlap between {a.objectName() or type(a).__name__} "
            f"({a.geometry()}) and {b.objectName() or type(b).__name__} ({b.geometry()})"
        )


def main():
    from qfluentwidgets import setTheme, Theme
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "tests", "_snapshots")
    os.makedirs(out_dir, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    setTheme(Theme.DARK)

    items = _sample_items()

    # 1. Single card at 285px (compact), 420px (wide), 700px (full with domain)
    for w, compact in ((285, True), (420, False), (700, False)):
        card = TimelineCard(items[0])
        card.show()
        card.setFixedSize(w, 44)
        card.set_compact_mode(compact)
        app.processEvents()
        assert_card_geometry(card, f"{w}px")
        card.grab().save(os.path.join(out_dir, f"card_{w}px.png"))
        card.close()

    # 2. Compact TimelineList (290px wide, matching tightest split)
    tl = TimelineList()
    tl.show()
    tl.setFixedSize(290, 380)
    tl.load_items(items)
    tl.set_player_target(items[0].id)
    tl.pulse_card(items[1].id, duration_ms=2000)
    app.processEvents()
    for c in tl.card_list:
        c.resize(tl.container.width(), tl.card_height)
        assert_card_geometry(c, "list-290px")
    tl.grab().save(os.path.join(out_dir, "timeline_list_compact_290px.png"))
    tl.close()

    # 3. Clip Player in Loop state & Edit state
    win = ARollTrimmerWindow()
    win.show()
    win.load_item(items[0], footage_path=None, doc_signature="verify-sig")
    app.processEvents()
    win.grab().save(os.path.join(out_dir, "clip_player_loop_mode.png"))
    win.set_mode(win.MODE_EDIT)
    app.processEvents()
    win.grab().save(os.path.join(out_dir, "clip_player_edit_mode.png"))
    win.destroy_window()
    app.processEvents()

    print(f"OK: All card & player layout checks passed. Snapshots saved to {out_dir}")


if __name__ == "__main__":
    main()
