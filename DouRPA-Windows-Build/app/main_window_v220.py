from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
)

from app.main_window import StatusDot
from app.main_window_v219 import MainWindow as V219MainWindow


class MainWindow(V219MainWindow):
    """V2.2.0 visual polish only:
    - remove the sidebar bottom info card;
    - make top-right three blocks equal size;
    - make Start Run full-surface press feedback.
    """

    TOP_BLOCK_W = 172
    TOP_BLOCK_H = 48

    def _sidebar(self):
        panel = QFrame()
        panel.setObjectName("Sidebar")
        panel.setFixedWidth(270)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(20, 23, 18, 20)
        lay.setSpacing(8)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(11)

        icon = QLabel()
        icon.setObjectName("BrandIcon")
        icon.setFixedSize(50, 50)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(QApplication.windowIcon().pixmap(44, 44))

        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)

        brand = QLabel("DouRPA Pro")
        brand.setObjectName("BrandName")
        sub = QLabel("让电商运营更简单")
        sub.setObjectName("BrandSub")

        brand_text.addWidget(brand)
        brand_text.addWidget(sub)
        brand_row.addWidget(icon)
        brand_row.addLayout(brand_text, 1)

        lay.addLayout(brand_row)
        lay.addSpacing(20)

        self.nav = []
        items = [
            ("⌂", "工作台", 0),
            ("▱", "裂变任务", 1),
            ("▢", "店铺管理", 2),
            ("◇", "源商品模板", 3),
            ("≡", "运行日志", 4),
            ("⚙", "系统设置", 5),
        ]
        for glyph, text, idx in items:
            nav = QPushButton(f"  {glyph}     {text}")
            nav.setObjectName("NavButton")
            nav.setProperty("active", idx == 0)
            nav.setCursor(Qt.PointingHandCursor)
            nav.setFixedHeight(50)
            nav.clicked.connect(lambda _=False, i=idx: self.switch_page(i))
            lay.addWidget(nav)
            self.nav.append(nav)

        # Remove the entire lower information card shown in the screenshot.
        lay.addStretch(1)

        version = QLabel("DouRPA Pro  ·  v2.4.0")
        version.setObjectName("SidebarFooterPlain")
        version.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lay.addWidget(version)

        return panel

    def _topbar(self):
        top = QFrame()
        top.setObjectName("Topbar")
        top.setFixedHeight(92)

        row = QHBoxLayout(top)
        row.setContentsMargins(4, 0, 0, 0)
        row.setSpacing(10)

        self._titles = [
            ("工作台", "高效 · 稳定 · 智能  让重复的工作交给 DouRPA Pro"),
            ("裂变任务", "批量管理相似品发布任务"),
            ("店铺管理", "独立店铺 Profile 与登录状态"),
            ("源商品模板", "管理源商品定位与复用模板"),
            ("运行日志", "查看自动化节点、错误与恢复信息"),
            ("系统设置", "控制发布安全停点与页面配置"),
        ]

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.page_title = QLabel(self._titles[0][0])
        self.page_title.setObjectName("PageTitle")
        self.page_sub = QLabel(self._titles[0][1])
        self.page_sub.setObjectName("PageSub")
        title_box.addWidget(self.page_title)
        title_box.addWidget(self.page_sub)

        row.addLayout(title_box)
        row.addStretch(1)

        # Block 1/3: browser status
        connection = QFrame()
        connection.setObjectName("TopEqualBlock")
        connection.setFixedSize(self.TOP_BLOCK_W, self.TOP_BLOCK_H)
        cl = QHBoxLayout(connection)
        cl.setContentsMargins(13, 0, 12, 0)
        cl.setSpacing(7)
        self.connection_dot = StatusDot("#20C67A", 8)
        self.engine_label = QLabel("浏览器未连接  ⌄")
        self.engine_label.setObjectName("EngineStatus")
        self.engine_label.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.connection_dot)
        cl.addWidget(self.engine_label, 1)
        row.addWidget(connection)

        # Block 2/3: store selector
        self.quick_store_combo = QComboBox()
        self.quick_store_combo.setObjectName("TopEqualCombo")
        self.quick_store_combo.setFixedSize(self.TOP_BLOCK_W, self.TOP_BLOCK_H)
        self.quick_store_combo.currentIndexChanged.connect(self._quick_store_changed)
        row.addWidget(self.quick_store_combo)

        # Block 3/3: start-run split button.
        self.start_run_split = QFrame()
        self.start_run_split.setObjectName("TopRunSplit")
        self.start_run_split.setFixedSize(self.TOP_BLOCK_W, self.TOP_BLOCK_H)

        split = QHBoxLayout(self.start_run_split)
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        # The gradient is on the QPushButton itself, so the whole clickable surface
        # receives hover/pressed feedback instead of only the text looking active.
        self.start_run_button = QPushButton("▶  开始运行")
        self.start_run_button.setObjectName("TopRunMain")
        self.start_run_button.setCursor(Qt.PointingHandCursor)
        self.start_run_button.setFixedHeight(self.TOP_BLOCK_H)
        self.start_run_button.clicked.connect(self.open_store_browser)

        self.start_run_arrow = QToolButton()
        self.start_run_arrow.setObjectName("TopRunArrow")
        self.start_run_arrow.setText("⌄")
        self.start_run_arrow.setCursor(Qt.PointingHandCursor)
        self.start_run_arrow.setPopupMode(QToolButton.InstantPopup)
        self.start_run_arrow.setFixedSize(42, self.TOP_BLOCK_H)

        menu = QMenu(self.start_run_arrow)
        menu.setObjectName("StartRunMenu")
        self.end_run_action = QAction("结束运行", menu)
        self.end_run_action.triggered.connect(self.end_current_run)
        menu.addAction(self.end_run_action)
        self.start_run_arrow.setMenu(menu)

        split.addWidget(self.start_run_button, 1)
        split.addWidget(self.start_run_arrow)
        row.addWidget(self.start_run_split)
        return top
