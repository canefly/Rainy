"""The in-app user guide window.

A separate window like Settings and About (not a modal: it can stay open beside Rainy while you work). Topics on the
left in three parts, the page on the right, a search box, links between pages, Back and Forward. The text comes from
core/guide.py (pages bundled in assets/guide). Links that point outside the guide are announced with
`app_link_requested`; the main window decides what to open, so this window knows nothing about Settings or footage.
"""
import os
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import (QColor, QIcon, QPainter, QPalette, QTextBlockFormat, QTextCharFormat, QTextCursor,
                           QTextDocument)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QTextBrowser, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)
from qfluentwidgets import FluentIcon, SearchLineEdit, ToolButton
from qframelesswindow import FramelessWindow, StandardTitleBar

from ...core import guide
from ..theme import ThemeManager

CONTACT_LINE = "Stuck? Message Cane (@yow_ph on Instagram or TikTok)."
NOT_FOUND = "This page could not be found."
NO_MATCH = "No pages match that search."
PAGE_ROLE = Qt.UserRole


def _find_item(tree: QTreeWidget, page_id: str) -> Optional[QTreeWidgetItem]:
    """The tree item for a page id, or None (it may be filtered out by a search)."""
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        if top.data(0, PAGE_ROLE) == page_id:
            return top
        for j in range(top.childCount()):
            child = top.child(j)
            if child.data(0, PAGE_ROLE) == page_id:
                return child
    return None


class GuideWindow(FramelessWindow):
    app_link_requested = Signal(str)      # e.g. "settings/ai-models" or "footage"

    def __init__(self, parent=None, pages: Optional[List[guide.Page]] = None):
        super().__init__(parent)
        self.setObjectName("GuideWindow")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("Rainy User Guide")
        self.resize(920, 640)
        self.setMinimumSize(640, 440)
        logo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "logo.png"))
        if os.path.exists(logo):
            self.setWindowIcon(QIcon(logo))
        self.setTitleBar(StandardTitleBar(self))
        self.titleBar.setFixedHeight(40)
        self.titleBar.setObjectName("GuideHeader")

        self.pages: List[guide.Page] = pages if pages is not None else guide.load_pages()
        self._history: List[str] = []
        self._pos = -1
        self._current: Optional[str] = None
        self._syncing = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 40, 0, 0)
        root.setSpacing(0)

        # -- left: search + topics -------------------------------------------------------
        left = QFrame(self)
        left.setObjectName("GuideSidebar")
        left.setFixedWidth(270)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(14, 12, 8, 14)
        lv.setSpacing(10)
        self.search = SearchLineEdit(self.titleBar)          # lives in the title bar, centred (see _place_search)
        self.search.setPlaceholderText("Search the guide")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(30)
        self.search.textChanged.connect(self._on_search)
        self.tree = QTreeWidget(left)
        self.tree.setObjectName("GuideTopics")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(10)
        self.tree.setRootIsDecorated(False)
        self.tree.setIconSize(QSize(0, 0))
        self.tree.setUniformRowHeights(False)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.currentItemChanged.connect(self._on_tree_changed)
        lv.addWidget(self.tree, 1)
        root.addWidget(left)

        # -- right: nav row + page + contact line ----------------------------------------
        right = QFrame(self)
        right.setObjectName("GuideContent")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(18, 12, 22, 12)
        rv.setSpacing(8)
        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(4)
        self.back_btn = ToolButton(FluentIcon.LEFT_ARROW, right)
        self.back_btn.setToolTip("Back")
        self.back_btn.clicked.connect(self.back)
        self.forward_btn = ToolButton(FluentIcon.RIGHT_ARROW, right)
        self.forward_btn.setToolTip("Forward")
        self.forward_btn.clicked.connect(self.forward)
        nav.addWidget(self.back_btn)
        nav.addWidget(self.forward_btn)
        nav.addStretch(1)
        rv.addLayout(nav)
        self.browser = QTextBrowser(right)
        self.browser.setObjectName("GuidePage")
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setFrameShape(QFrame.NoFrame)
        self.browser.anchorClicked.connect(self._on_anchor)
        rv.addWidget(self.browser, 1)
        self.contact = QLabel(CONTACT_LINE, right)
        self.contact.setObjectName("GuideContact")
        self.contact.setTextFormat(Qt.PlainText)
        rv.addWidget(self.contact)
        root.addWidget(right, 1)

        self._fill_tree()
        self.apply_theme()
        if self.pages:
            self.show_page(self.pages[0].id)
        else:
            self._render_text(NOT_FOUND)
        self._sync_nav()

    # -- topics ----------------------------------------------------------------------------
    def _fill_tree(self) -> None:
        self._syncing = True
        self.tree.clear()
        for number, title, items in guide.parts(self.pages):
            head = QTreeWidgetItem(self.tree, [f"Part {number}: {title}"])
            head.setFlags(Qt.ItemIsEnabled)                      # a heading: not selectable
            f = head.font(0)
            f.setBold(True)
            head.setFont(0, f)
            for p in items:
                child = QTreeWidgetItem(head, [p.title])
                child.setData(0, PAGE_ROLE, p.id)
            head.setExpanded(True)
        self._syncing = False
        self._select_in_tree(self._current)

    def _fill_results(self, hits: List[guide.Page]) -> None:
        self._syncing = True
        self.tree.clear()
        if not hits:
            note = QTreeWidgetItem(self.tree, [NO_MATCH])
            note.setFlags(Qt.NoItemFlags)
        for p in hits:
            item = QTreeWidgetItem(self.tree, [p.title])
            item.setData(0, PAGE_ROLE, p.id)
        self._syncing = False
        self._select_in_tree(self._current)

    def _on_search(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self._fill_tree()
        else:
            self._fill_results(guide.search(self.pages, text))

    def _select_in_tree(self, page_id: Optional[str]) -> None:
        self._syncing = True
        try:
            self.tree.setCurrentItem(None)
            if page_id is None:
                return
            item = _find_item(self.tree, page_id)
            if item is not None:
                self.tree.setCurrentItem(item)
        finally:
            self._syncing = False

    def _on_tree_changed(self, current, _previous) -> None:
        if self._syncing or current is None:
            return
        page_id = current.data(0, PAGE_ROLE)
        if page_id:
            self.show_page(str(page_id))

    # -- pages -----------------------------------------------------------------------------
    def show_page(self, page_id: str, record: bool = True) -> bool:
        """Show one page. A page that does not exist shows a plain message and returns False; it never raises."""
        page = guide.find(self.pages, page_id)
        if page is None:
            self._render_text(NOT_FOUND)
            return False
        self._current = page.id
        self._render_markdown(f"# {page.title}\n\n{page.body}")
        self._to_top()
        if record:
            del self._history[self._pos + 1:]
            if not self._history or self._history[-1] != page.id:
                self._history.append(page.id)
            self._pos = len(self._history) - 1
        self._select_in_tree(page.id)
        self._sync_nav()
        return True

    def _to_top(self) -> None:
        """Replacing the text leaves the cursor, and so the view, at the end of the page: put it back at the top."""
        self.browser.moveCursor(QTextCursor.Start)
        self.browser.verticalScrollBar().setValue(0)

    def _render_markdown(self, md: str) -> None:
        doc = self.browser.document()
        doc.setMarkdown(md, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        self._color_links(doc)
        self._loosen(doc)

    LINE_HEIGHT_PERCENT = 150      # a little air between lines
    PARAGRAPH_GAP = 10             # space after each paragraph
    HEADING_GAP_ABOVE = 16
    TABLE_CELL_PADDING = 8

    def _loosen(self, doc: QTextDocument) -> None:
        """Markdown comes out cramped: tall lines, a gap after every paragraph and heading, roomy table cells."""
        block = doc.begin()
        cur = QTextCursor(doc)
        while block.isValid():
            fmt = block.blockFormat()
            heading = fmt.headingLevel() > 0
            fmt.setLineHeight(float(self.LINE_HEIGHT_PERCENT), QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            fmt.setTopMargin(self.HEADING_GAP_ABOVE if heading and block != doc.begin() else fmt.topMargin())
            fmt.setBottomMargin(self.PARAGRAPH_GAP if not heading else self.PARAGRAPH_GAP - 2)
            cur.setPosition(block.position())
            cur.setBlockFormat(fmt)
            block = block.next()
        done = set()                                   # a table is reached from every cell, so style each only once
        block = doc.begin()
        while block.isValid():
            cur.setPosition(block.position())
            table = cur.currentTable()
            if table is not None and table.firstPosition() not in done:
                done.add(table.firstPosition())
                tf = table.format()
                tf.setCellPadding(self.TABLE_CELL_PADDING)
                tf.setTopMargin(4)
                tf.setBottomMargin(self.PARAGRAPH_GAP + 4)
                table.setFormat(tf)
            block = block.next()

    def _color_links(self, doc: QTextDocument) -> None:
        """Markdown links come out in Qt's default dark blue, unreadable on a dark theme: use the theme's accent."""
        ranges = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isAnchor():
                    ranges.append((frag.position(), frag.position() + frag.length()))
                it += 1
            block = block.next()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(ThemeManager.get_palette()["accent"]))
        fmt.setFontUnderline(True)
        for start, end in ranges:
            cur = QTextCursor(doc)
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.KeepAnchor)
            cur.mergeCharFormat(fmt)

    def _render_text(self, text: str) -> None:
        self.browser.document().setPlainText(text)

    def current_page_id(self) -> Optional[str]:
        return self._current

    # -- links and history -----------------------------------------------------------------
    def _on_anchor(self, url: QUrl) -> None:
        target = url.toString()
        kind, _, name = target.partition(":")
        if kind == "guide":
            self.show_page(name)
        elif kind == "app" and name in guide.APP_TARGETS:
            self.app_link_requested.emit(name)
        # anything else (a web address, a typo) is ignored on purpose: the guide never opens a browser

    def back(self) -> None:
        if self._pos > 0:
            self._pos -= 1
            self.show_page(self._history[self._pos], record=False)

    def forward(self) -> None:
        if self._pos < len(self._history) - 1:
            self._pos += 1
            self.show_page(self._history[self._pos], record=False)

    def _sync_nav(self) -> None:
        self.back_btn.setEnabled(self._pos > 0)
        self.forward_btn.setEnabled(self._pos < len(self._history) - 1)

    # -- look ------------------------------------------------------------------------------
    def apply_theme(self) -> None:
        pal = ThemeManager.get_palette()
        qp = self.browser.palette()
        qp.setColor(QPalette.Base, QColor(pal["surface"]))
        qp.setColor(QPalette.Text, QColor(pal["text"]))
        qp.setColor(QPalette.Link, QColor(pal["accent"]))
        qp.setColor(QPalette.LinkVisited, QColor(pal["accent"]))
        self.browser.setPalette(qp)
        self.setStyleSheet(f"""
            QFrame#GuideSidebar {{ background: transparent; border-right: 1px solid {pal['border']}; }}
            QFrame#GuideContent {{ background: transparent; }}
            QTreeWidget#GuideTopics {{ background: transparent; border: none; outline: none;
                color: {pal['text']}; font-size: 13px; }}
            QTreeWidget#GuideTopics::item {{ padding: 7px 8px; margin: 1px 0px; border-radius: 6px; }}
            QTreeWidget#GuideTopics::item:selected {{ background: {pal['surface_hover']}; color: {pal['text']}; }}
            QTreeWidget#GuideTopics::item:hover {{ background: {pal['surface_hover']}; }}
            QTextBrowser#GuidePage {{ background: {pal['surface']}; color: {pal['text']};
                border: 1px solid {pal['border']}; border-radius: 8px; padding: 10px; font-size: 14px; }}
            QLabel#GuideContact {{ color: {pal['muted']}; font-size: 12px; background: transparent; }}
        """)
        self._apply_caption_buttons(pal)
        if self._current:
            self.show_page(self._current, record=False)

    def _apply_caption_buttons(self, pal) -> None:
        """Minimise / maximise / close in the theme's text colour (white on dark), like the Settings window.
        qfluentwidgets re-applies its own style on polish, so these use a widget-level stylesheet, which wins."""
        tb = self.titleBar
        base = QColor("#ffffff") if pal["is_dark"] else QColor(pal["text"])      # white on dark

        def rgba(c: QColor) -> str:
            return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"

        hover = QColor(base.red(), base.green(), base.blue(), 28)
        press = QColor(base.red(), base.green(), base.blue(), 46)
        for btn in (tb.minBtn, tb.maxBtn):
            btn.setStyleSheet(
                f"qproperty-normalColor: {rgba(base)}; qproperty-hoverColor: {rgba(base)}; "
                f"qproperty-pressedColor: {rgba(base)}; qproperty-normalBackgroundColor: rgba(0, 0, 0, 0); "
                f"qproperty-hoverBackgroundColor: {rgba(hover)}; qproperty-pressedBackgroundColor: {rgba(press)};")
            btn.update()
        tb.closeBtn.setStyleSheet(
            f"qproperty-normalColor: {rgba(base)}; qproperty-hoverColor: rgba(255, 255, 255, 255); "
            f"qproperty-pressedColor: rgba(255, 255, 255, 255); qproperty-normalBackgroundColor: rgba(0, 0, 0, 0); "
            f"qproperty-hoverBackgroundColor: rgba(232, 17, 35, 255); "
            f"qproperty-pressedBackgroundColor: rgba(241, 112, 122, 255);")
        tb.closeBtn.update()

    def _place_search(self) -> None:
        """Centre the search box in the title bar, leaving room for the window buttons on the right."""
        width = max(180, min(380, self.width() - 2 * 160))
        self.search.setFixedWidth(width)
        self.search.move((self.width() - width) // 2, (self.titleBar.height() - self.search.height()) // 2)
        self.search.raise_()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_search()

    def paintEvent(self, e) -> None:
        painter = QPainter(self)
        ThemeManager.paint_window_backdrop(self, painter)

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        if ThemeManager.orb_glow_intensity > 0:
            self.update()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._place_search()
        ThemeManager.enable_window_backdrop(self)
