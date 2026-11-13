"""3-slice live resize: a stretched bake must be pixel-identical to a real rebake."""
import sys
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtCore import Qt

from src.core.models import ScriptItem


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(params=[True, False], ids=["dark", "light"])
def theme(request, qapp):
    from src.ui.theme import ThemeManager
    orig_variant, orig_dark = ThemeManager.current_variant, ThemeManager.is_dark
    ThemeManager.apply_theme(orig_variant, dark=request.param)
    yield request.param
    ThemeManager.apply_theme(orig_variant, dark=orig_dark)


def _items():
    def item(i, link, single=False):
        s = 60 + i * 5
        return ScriptItem(
            id=f"i{i}", row_index=i + 1, raw_timestamp="x",
            start_timecode=f"00:01:{s % 60:02d}:00", start_seconds=s,
            end_timecode="" if single else f"00:01:{(s + 2) % 60:02d}:00",
            end_seconds=s + 2, duration_seconds=0 if single else 2, duration_display="2s", text="x",
            links=[f"https://drive.google.com/file/d/{i}"] if link else [],
        )
    return [item(0, True), item(1, False), item(2, True, single=True), item(3, False, single=True)]


@pytest.fixture
def timeline(qapp, theme):
    from src.ui.components.timeline_list import TimelineList
    tl = TimelineList()
    tl.resize(760, 400)
    tl.show()
    tl.load_items(_items())
    qapp.processEvents()
    yield tl
    tl.close()
    tl.deleteLater()


def _stretched_image(card, w, h, dpr):
    img = QImage(int(round(w * dpr)), int(round(h * dpr)), QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.transparent)
    p = QPainter(img)
    card.draw_stretched(p, 0, 0, w)
    p.end()
    return img


def _real_image(card, w, h, dpr):
    card.invalidate_cache()
    pix = card.get_cached_surface(w, h, dpr, bypass_hold=True)
    return pix.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)


def _diff_columns(a: QImage, b: QImage):
    return sorted({x for x in range(a.width()) for y in range(a.height()) if a.pixel(x, y) != b.pixel(x, y)})


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5])
@pytest.mark.parametrize("base_w,target_w", [(640, 600), (640, 720), (600, 760), (700, 590), (641, 600)])
def test_stretch_matches_real_rebake_wide(timeline, dpr, base_w, target_w):
    h = timeline.card_height
    for card in timeline.card_list:
        card.invalidate_cache()
        card.get_cached_surface(base_w, h, dpr)
        assert card.can_stretch_to(target_w), f"card {card.item.id} should stretch {base_w}->{target_w}"
        right_start = card._slice_info[2]
        stretched = _stretched_image(card, target_w, h, dpr)
        real = _real_image(card, target_w, h, dpr)
        assert stretched.size() == real.size()
        if ((target_w - base_w) * dpr).is_integer():
            # Width change is a whole number of physical pixels: must be pixel-identical
            assert stretched == real, f"card {card.item.id} differs at dpr={dpr} {base_w}->{target_w}"
        else:
            # Fractional scaling + half-pixel width change: the right cluster sits at a
            # different sub-pixel phase in a real bake. Everything left of it (left cluster,
            # margins, middle gap, borders) must still be pixel-identical.
            right_start_px = int((right_start + (target_w - base_w)) * dpr) - 2
            cols = _diff_columns(stretched, real)
            assert not cols or cols[0] >= right_start_px, (
                f"card {card.item.id} differs left of the right cluster at col {cols[0]} (dpr={dpr})")


@pytest.mark.parametrize("dpr", [1.0, 1.5])
def test_stretch_matches_real_rebake_compact(timeline, dpr):
    h = timeline.card_height
    for card in timeline.card_list:
        card.set_compact_mode(True)
        card.get_cached_surface(400, h, dpr)
        for target_w in (360, 440):
            assert card.can_stretch_to(target_w)
            stretched = _stretched_image(card, target_w, h, dpr)
            real = _real_image(card, target_w, h, dpr)
            assert stretched == real, f"compact card {card.item.id} differs at {target_w}"
            card.get_cached_surface(400, h, dpr)


def test_domain_breakpoint_forces_rebake(timeline):
    h = timeline.card_height
    with_domain = timeline.card_widgets["i0"]
    no_domain = timeline.card_widgets["i1"]
    for card in (with_domain, no_domain):
        card.invalidate_cache()
        card.get_cached_surface(600, h, 1.0)
    # Crossing 560px hides the domain chip, so a stretch would be wrong
    assert not with_domain.can_stretch_to(540)
    assert no_domain.can_stretch_to(540)


def test_too_narrow_forces_rebake(timeline):
    card = timeline.card_widgets["i0"]
    card.invalidate_cache()
    card.get_cached_surface(640, timeline.card_height, 1.0)
    assert not card.can_stretch_to(200)


def test_compact_switch_forces_rebake(timeline):
    card = timeline.card_widgets["i1"]
    card.invalidate_cache()
    card.get_cached_surface(640, timeline.card_height, 1.0)
    card.is_compact = True  # mode flipped without a rebake yet
    assert not card.can_stretch_to(600)


def test_held_canvas_paint_stretches_without_rebaking(timeline, qapp):
    canvas = timeline.container
    timeline.set_hold_reflow(True)
    try:
        # Bake everything at the current width
        canvas.render(QPixmap(canvas.size()))
        baked = {c.item.id: c._cached_surface for c in timeline.filtered_cards}
        # Drag narrower (stays above the domain breakpoint). Resize the canvas directly
        # without pumping the event loop, so unrelated async work (e.g. link metadata
        # landing and invalidating a card) can't interfere with what we're measuring.
        canvas.setFixedSize(canvas.width() - 60, canvas.height())
        canvas.render(QPixmap(canvas.size()))
        for c in timeline.filtered_cards:
            assert c._cached_surface is baked[c.item.id], f"card {c.item.id} was rebaked mid-drag"
    finally:
        timeline.set_hold_reflow(False)
