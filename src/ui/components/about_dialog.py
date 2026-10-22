import os
import webbrowser
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
)
from qframelesswindow import FramelessWindow, StandardTitleBar
from qfluentwidgets import FluentIcon, IconWidget, PushButton, ToolButton, SmoothScrollArea
from ..theme import ThemeManager

class AboutWindow(FramelessWindow):
    """Independent desktop window for About & Updates matching OBS Studio / Premiere Pro architecture."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AboutWindow")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowTitle("About Clickables Tools")
        self.resize(480, 580)
        self.setMinimumSize(420, 500)

        # Window Icon
        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "logo.png"))
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        # TitleBar
        self.setTitleBar(StandardTitleBar(self))
        self.titleBar.setFixedHeight(40)
        self.titleBar.setObjectName("AboutHeader")

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 40, 0, 0)
        root_layout.setSpacing(0)

        # 2. Body Container with Smooth Kinetic ScrollArea
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        body = QWidget(scroll)
        body.setObjectName("AboutBody")
        b_layout = QVBoxLayout(body)
        b_layout.setContentsMargins(20, 16, 20, 16)
        b_layout.setSpacing(12)

        # 1. Maintained by Canefly Box
        box1 = QFrame(body)
        box1.setObjectName("PrefCard")
        b1_layout = QVBoxLayout(box1)
        b1_layout.setContentsMargins(16, 14, 16, 14)
        b1_layout.setSpacing(6)

        m_title = QLabel("MAINTAINED BY CANEFLY", box1)
        m_title.setObjectName("PrefSectionTitle")
        b1_layout.addWidget(m_title)

        m_desc = QLabel("This app is maintained by Canefly. To get new updates, join our Discord server:", box1)
        m_desc.setWordWrap(True)
        m_desc.setObjectName("PrefCaption")
        b1_layout.addWidget(m_desc)

        discord_btn = PushButton("discord.gg/Nv58zRGJma", box1)
        discord_btn.setIcon(FluentIcon.CHAT)
        discord_btn.clicked.connect(lambda: webbrowser.open("https://discord.gg/Nv58zRGJma"))
        b1_layout.addWidget(discord_btn)
        b_layout.addWidget(box1)

        # 2. Connect with Cane
        socials_title = QLabel("CONNECT WITH CANE", body)
        socials_title.setObjectName("PrefSectionTitle")
        b_layout.addWidget(socials_title)

        socials = [
            ("Instagram", "@yow_ph", "https://www.instagram.com/yow_ph/", "#ec4899"),
            ("TikTok", "@yow_ph", "https://www.tiktok.com/@yow_ph", "#06b6d4"),
            ("YouTube", "@yowph", "https://www.youtube.com/@yowph", "#ef4444"),
            ("Discord", "@yowph", "https://discord.com/users/758307200422248478", "#6366f1"),
        ]

        for platform, handle, url, color in socials:
            row = QFrame(body)
            row.setObjectName("PrefCard")
            r_layout = QHBoxLayout(row)
            r_layout.setContentsMargins(14, 10, 14, 10)

            p_lbl = QLabel(platform.upper(), row)
            p_lbl.setObjectName("PrefCaption")
            p_lbl.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 1px;")
            r_layout.addWidget(p_lbl)
            r_layout.addStretch(1)

            h_lbl = QLabel(handle, row)
            h_lbl.setStyleSheet(f"color: {color}; background: transparent; font-size: 11px; font-weight: 700;")
            r_layout.addWidget(h_lbl)

            row.mousePressEvent = lambda e, u=url: webbrowser.open(u)
            b_layout.addWidget(row)

        # 3. Legal Notice Box
        legal_box = QFrame(body)
        legal_box.setObjectName("PrefCard")
        l_layout = QVBoxLayout(legal_box)
        l_layout.setContentsMargins(16, 12, 16, 12)
        l_layout.setSpacing(4)

        l_title = QLabel("LEGAL NOTICE", legal_box)
        l_title.setObjectName("PrefSectionTitle")
        l_layout.addWidget(l_title)

        l_desc = QLabel(
            "This software is the exclusive property of Cane. Unauthorized reproduction, distribution, "
            "modification, or reverse engineering of this codebase, in whole or in part, is strictly prohibited by law. "
            "Violators will be subject to maximum civil liabilities, statutory damages, and immediate legal prosecution.",
            legal_box
        )
        l_desc.setWordWrap(True)
        l_desc.setObjectName("PrefCaption")
        l_layout.addWidget(l_desc)
        b_layout.addWidget(legal_box)

        # Copyright tag
        cr_lbl = QLabel("Clickables Tools v2.0 Native · All rights reserved", body)
        cr_lbl.setObjectName("PrefCaption")
        cr_lbl.setAlignment(Qt.AlignCenter)
        b_layout.addWidget(cr_lbl)

        scroll.setWidget(body)
        root_layout.addWidget(scroll, 1)

    def paintEvent(self, e):
        painter = QPainter(self)
        ThemeManager.paint_window_backdrop(self, painter)

    def moveEvent(self, e):
        super().moveEvent(e)
        if ThemeManager.orb_glow_intensity > 0:
            self.update()

    def showEvent(self, e):
        super().showEvent(e)
        ThemeManager.enable_window_backdrop(self)

AboutDialog = AboutWindow
