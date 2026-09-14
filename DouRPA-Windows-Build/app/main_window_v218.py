from __future__ import annotations

import random
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.main_window import StatusDot, button
from app.main_window_v212 import fixed_status_pill
from app.main_window_v215 import CancellableBrowserWorker
from app.main_window_v216 import MainWindow as V216MainWindow


class ConfigurableCancellableBrowserWorker(CancellableBrowserWorker):
    """Keep all existing RPA behavior, but make post-publish delay configurable."""

    def __init__(self, *args, delay_min=10, delay_max=15, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_min_seconds = int(delay_min)
        self.delay_max_seconds = int(delay_max)

    def set_publish_delay_range(self, minimum: int, maximum: int):
        minimum = max(0, int(minimum))
        maximum = max(minimum, int(maximum))
        self.delay_min_seconds = minimum
        self.delay_max_seconds = maximum

    def _schedule_next_publish_delay(self, code: str):
        minimum = max(0, int(self.delay_min_seconds))
        maximum = max(minimum, int(self.delay_max_seconds))
        seconds = (
            float(minimum)
            if minimum == maximum
            else random.uniform(float(minimum), float(maximum))
        )
        self._next_publish_not_before = time.monotonic() + seconds

        if minimum == maximum:
            desc = f"{minimum} 秒"
        else:
            desc = f"{minimum}~{maximum} 秒，本次 {seconds:.1f} 秒"
        self.info.emit(f"{code} 已发布成功。发布间隔：{desc}。")


class ReferenceMetricCard(QFrame):
    def __init__(self, icon_text: str, title: str, value: str, hint: str, accent: str):
        super().__init__()
        self.setObjectName("ReferenceMetricCard")
        self.setProperty("accent", accent)
        self.setFixedHeight(102)

        row = QHBoxLayout(self)
        row.setContentsMargins(17, 13, 16, 13)
        row.setSpacing(12)

        self.icon = QLabel(icon_text)
        self.icon.setObjectName("ReferenceMetricIcon")
        self.icon.setProperty("accent", accent)
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setFixedSize(48, 48)
        f = QFont("Microsoft YaHei UI", 18)
        f.setWeight(QFont.DemiBold)
        self.icon.setFont(f)
        row.addWidget(self.icon)

        text = QVBoxLayout()
        text.setSpacing(0)

        self.title = QLabel(title)
        self.title.setObjectName("MetricTitle")
        self.value = QLabel(value)
        self.value.setObjectName("MetricValue")
        self.hint = QLabel(hint)
        self.hint.setObjectName("MetricHint")

        text.addWidget(self.title)
        text.addWidget(self.value)
        text.addWidget(self.hint)
        row.addLayout(text, 1)

        self.bars = QLabel("▂▅▃▆▇")
        self.bars.setObjectName("ReferenceMiniBars")
        self.bars.setProperty("accent", accent)
        self.bars.setAlignment(Qt.AlignBottom | Qt.AlignRight)
        self.bars.setFixedWidth(58)
        row.addWidget(self.bars)


class RobotOrb(QWidget):
    """Small vector robot so the current-run card matches the reference without an asset."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(118, 118)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        glow = QRadialGradient(59, 59, 58)
        glow.setColorAt(0.0, QColor(255, 255, 255, 245))
        glow.setColorAt(0.42, QColor(188, 229, 255, 210))
        glow.setColorAt(0.70, QColor(117, 165, 255, 115))
        glow.setColorAt(1.0, QColor(124, 96, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(1, 1, 116, 116)

        p.setBrush(QColor(247, 251, 255, 250))
        p.setPen(QColor(255, 255, 255, 230))
        p.drawRoundedRect(30, 33, 58, 52, 20, 20)

        p.setBrush(QColor(24, 39, 69, 250))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(36, 47, 46, 25, 12, 12)

        p.setBrush(QColor(53, 175, 255))
        p.drawEllipse(46, 55, 7, 7)
        p.drawEllipse(66, 55, 7, 7)

        p.setBrush(QColor(247, 251, 255))
        p.drawRoundedRect(55, 24, 8, 13, 4, 4)

        p.setBrush(QColor(126, 164, 255, 210))
        p.drawEllipse(57, 19, 4, 4)

        p.end()


class MainWindow(V216MainWindow):
    """V2.1.8 — workbench rebuilt to the supplied reference composition.

    Dashboard presentation changes heavily, but RPA/task/store/database behavior
    continues to come from V2.1.5/V2.1.6.
    """

    def __init__(self, root: Path):
        self._run_started_monotonic = None
        self._last_run_seconds = 0.0
        self._settings = QSettings("LocalOps", "DouRPA")
        super().__init__(root)

        self.resize(1672, 941)
        self.setMinimumSize(1400, 820)

        # Bind the new dashboard controls to the original application behavior.
        if hasattr(self, "safe_mode"):
            self.auto_publish_checkbox.blockSignals(True)
            self.auto_publish_checkbox.setChecked(not self.safe_mode.isChecked())
            self.auto_publish_checkbox.blockSignals(False)

            self.auto_publish_checkbox.toggled.connect(
                lambda checked: self.safe_mode.setChecked(not checked)
            )
            self.safe_mode.toggled.connect(self._sync_auto_publish_from_safe_mode)

        try:
            self.store_table.itemSelectionChanged.connect(
                self._sync_quick_store_from_table
            )
        except Exception:
            pass

        self._runtime_timer = QTimer(self)
        self._runtime_timer.setInterval(1000)
        self._runtime_timer.timeout.connect(self._sync_reference_runtime)
        self._runtime_timer.start()

        self._install_reference_shadows()
        self._sync_delay_controls()
        self.refresh_all()

    # ---------- reference sidebar ----------
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

        lay.addStretch(1)

        liquid = QFrame()
        liquid.setObjectName("ReferenceSidebarLiquid")
        liquid.setFixedHeight(300)
        ll = QVBoxLayout(liquid)
        ll.setContentsMargins(22, 20, 18, 18)

        ll.addStretch(1)

        slogan1 = QLabel("AI 助力")
        slogan1.setObjectName("SidebarSlogan")
        slogan2 = QLabel("放大生意增长")
        slogan2.setObjectName("SidebarSloganSub")

        ll.addWidget(slogan1)
        ll.addWidget(slogan2)
        ll.addStretch(1)

        version = QLabel("DouRPA Pro\nv2.3.8  |  专业版")
        version.setObjectName("SidebarVersion")
        ll.addWidget(version)

        lay.addWidget(liquid)
        return panel

    # ---------- reference top bar ----------
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

        connection = QFrame()
        connection.setObjectName("ConnectionPill")
        cl = QHBoxLayout(connection)
        cl.setContentsMargins(14, 8, 13, 8)
        cl.setSpacing(7)

        self.connection_dot = StatusDot("#20C67A", 8)
        self.engine_label = QLabel("浏览器未连接 ⌄")
        self.engine_label.setObjectName("EngineStatus")

        cl.addWidget(self.connection_dot)
        cl.addWidget(self.engine_label)
        row.addWidget(connection)

        self.quick_store_combo = QComboBox()
        self.quick_store_combo.setObjectName("QuickStoreCombo")
        self.quick_store_combo.setFixedSize(168, 44)
        self.quick_store_combo.currentIndexChanged.connect(self._quick_store_changed)
        row.addWidget(self.quick_store_combo)

        # Exact split-button behavior requested:
        # left = original "打开店铺", right arrow menu = original "结束运行".
        self.start_run_split = QFrame()
        self.start_run_split.setObjectName("StartRunSplit")
        self.start_run_split.setFixedSize(184, 48)

        split = QHBoxLayout(self.start_run_split)
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        self.start_run_button = QPushButton("▶  开始运行")
        self.start_run_button.setObjectName("StartRunMainButton")
        self.start_run_button.setCursor(Qt.PointingHandCursor)
        self.start_run_button.clicked.connect(self.open_store_browser)

        self.start_run_arrow = QToolButton()
        self.start_run_arrow.setObjectName("StartRunArrowButton")
        self.start_run_arrow.setText("⌄")
        self.start_run_arrow.setCursor(Qt.PointingHandCursor)
        self.start_run_arrow.setPopupMode(QToolButton.InstantPopup)

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

    # ---------- reference workbench ----------
    def _dashboard_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # 1) Exactly four horizontal summary cards.
        metrics = QHBoxLayout()
        metrics.setSpacing(12)

        self.m_total = ReferenceMetricCard(
            "▤", "今日发布商品", "0", "今日任务", "blue"
        )
        self.m_pending = ReferenceMetricCard(
            "✓", "成功发布", "0", "今日成功", "purple"
        )
        self.m_success = ReferenceMetricCard(
            "×", "发布失败", "0", "今日失败", "red"
        )
        self.m_failed = ReferenceMetricCard(
            "◷", "运行时长", "0.0 小时", "当前运行", "orange"
        )

        for card in (
            self.m_total,
            self.m_pending,
            self.m_success,
            self.m_failed,
        ):
            metrics.addWidget(card, 1)
        lay.addLayout(metrics)

        # 2) Main left/right row.
        middle = QHBoxLayout()
        middle.setSpacing(14)

        batch = QFrame()
        batch.setObjectName("Card")
        batch.setFixedHeight(332)

        bl = QVBoxLayout(batch)
        bl.setContentsMargins(18, 15, 18, 16)
        bl.setSpacing(10)

        title_row = QHBoxLayout()
        title_left = QVBoxLayout()
        title_left.setSpacing(1)

        batch_title = QLabel("批量发布")
        batch_title.setObjectName("SectionTitle")
        batch_sub = QLabel("选择任务，设置发布参数，一键批量发布到店铺")
        batch_sub.setObjectName("SectionSub")

        title_left.addWidget(batch_title)
        title_left.addWidget(batch_sub)
        title_row.addLayout(title_left)
        title_row.addStretch(1)
        bl.addLayout(title_row)

        # Reference process row.
        process = QHBoxLayout()
        process.setSpacing(5)

        for i, text in enumerate(("选择商品", "设置参数", "选择店铺", "开始发布"), start=1):
            item = QFrame()
            item.setObjectName("ReferenceFlowItem")
            item.setProperty("active", i == 1)
            item.setFixedHeight(42)

            il = QHBoxLayout(item)
            il.setContentsMargins(10, 5, 10, 5)
            il.setSpacing(8)

            number = QLabel(str(i))
            number.setObjectName("ReferenceFlowNumber")
            number.setProperty("active", i == 1)
            number.setAlignment(Qt.AlignCenter)
            number.setFixedSize(28, 28)

            label = QLabel(text)
            label.setObjectName("ReferenceFlowText")
            label.setProperty("active", i == 1)

            il.addWidget(number)
            il.addWidget(label)
            il.addStretch(1)

            process.addWidget(item, 1)

            if i != 4:
                arrow = QLabel("›")
                arrow.setObjectName("ReferenceFlowArrow")
                arrow.setAlignment(Qt.AlignCenter)
                arrow.setFixedWidth(12)
                process.addWidget(arrow)

        bl.addLayout(process)

        # Reference dashed selector area. It maps to the existing task page.
        self.task_selector_button = QPushButton()
        self.task_selector_button.setObjectName("ReferenceTaskSelector")
        self.task_selector_button.setCursor(Qt.PointingHandCursor)
        self.task_selector_button.setFixedHeight(92)
        self.task_selector_button.clicked.connect(lambda: self.switch_page(1))
        bl.addWidget(self.task_selector_button)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)

        # Publish quantity: display only, auto-calculated from tasks.
        qty_box = QVBoxLayout()
        qty_box.setSpacing(4)
        qty_label = QLabel("发布数量")
        qty_label.setObjectName("ReferenceControlLabel")
        self.publish_quantity_display = QLineEdit("0")
        self.publish_quantity_display.setObjectName("ReadOnlyQuantity")
        self.publish_quantity_display.setReadOnly(True)
        self.publish_quantity_display.setAlignment(Qt.AlignCenter)
        self.publish_quantity_display.setFixedSize(132, 40)
        qty_box.addWidget(qty_label)
        qty_box.addWidget(self.publish_quantity_display)
        bottom.addLayout(qty_box)

        # Publish interval: custom minimum~maximum range, replacing fixed 10~15.
        interval_box = QVBoxLayout()
        interval_box.setSpacing(4)
        interval_label = QLabel("发布间隔 (秒)")
        interval_label.setObjectName("ReferenceControlLabel")

        range_frame = QFrame()
        range_frame.setObjectName("DelayRangeFrame")
        range_frame.setFixedSize(176, 40)
        rf = QHBoxLayout(range_frame)
        rf.setContentsMargins(4, 2, 4, 2)
        rf.setSpacing(2)

        self.delay_min_spin = QSpinBox()
        self.delay_min_spin.setObjectName("DelayRangeSpin")
        self.delay_min_spin.setRange(0, 300)
        self.delay_min_spin.setAlignment(Qt.AlignCenter)
        self.delay_min_spin.setFixedWidth(72)

        dash = QLabel("—")
        dash.setAlignment(Qt.AlignCenter)
        dash.setObjectName("DelayRangeDash")
        dash.setFixedWidth(18)

        self.delay_max_spin = QSpinBox()
        self.delay_max_spin.setObjectName("DelayRangeSpin")
        self.delay_max_spin.setRange(0, 300)
        self.delay_max_spin.setAlignment(Qt.AlignCenter)
        self.delay_max_spin.setFixedWidth(72)

        rf.addWidget(self.delay_min_spin)
        rf.addWidget(dash)
        rf.addWidget(self.delay_max_spin)

        interval_box.addWidget(interval_label)
        interval_box.addWidget(range_frame)
        bottom.addLayout(interval_box)

        # Reference checkboxes. Both are wired to real behavior.
        checks = QVBoxLayout()
        checks.setSpacing(5)

        self.auto_duplicate_checkbox = QCheckBox("自动处理重复")
        self.auto_duplicate_checkbox.setChecked(
            self._settings.value("auto_handle_duplicates", True, type=bool)
        )
        self.auto_duplicate_checkbox.toggled.connect(
            lambda checked: self._settings.setValue(
                "auto_handle_duplicates", bool(checked)
            )
        )

        self.auto_publish_checkbox = QCheckBox("发布后自动上架")
        self.auto_publish_checkbox.setChecked(True)

        checks.addStretch(1)
        checks.addWidget(self.auto_duplicate_checkbox)
        checks.addWidget(self.auto_publish_checkbox)
        checks.addStretch(1)

        bottom.addLayout(checks)
        bottom.addStretch(1)

        self.dashboard_publish_button = QPushButton("▶  开始发布")
        self.dashboard_publish_button.setObjectName("ReferencePublishButton")
        self.dashboard_publish_button.setCursor(Qt.PointingHandCursor)
        self.dashboard_publish_button.setFixedSize(218, 58)
        self.dashboard_publish_button.clicked.connect(self.run_pending_tasks)
        bottom.addWidget(self.dashboard_publish_button, 0, Qt.AlignBottom)

        bl.addLayout(bottom)
        middle.addWidget(batch, 2)

        # Right side "当前运行" card, without controls not present in the reference.
        run = QFrame()
        run.setObjectName("Card")
        run.setFixedHeight(332)

        rl = QVBoxLayout(run)
        rl.setContentsMargins(18, 15, 18, 15)
        rl.setSpacing(5)

        run_head = QHBoxLayout()
        green = StatusDot("#21C875", 8)
        run_title = QLabel("当前运行")
        run_title.setObjectName("SectionTitle")
        detail = QPushButton("查看详情  →")
        detail.setObjectName("TextLinkButton")
        detail.setCursor(Qt.PointingHandCursor)
        detail.clicked.connect(lambda: self.switch_page(1))

        run_head.addWidget(green)
        run_head.addWidget(run_title)
        run_head.addStretch(1)
        run_head.addWidget(detail)
        rl.addLayout(run_head)

        self.robot_orb = RobotOrb()
        rl.addWidget(self.robot_orb, 0, Qt.AlignHCenter)

        self.run_status = QLabel("浏览器已关闭")
        self.run_status.setObjectName("RunStatusBig")
        self.run_status.setAlignment(Qt.AlignCenter)

        self.run_desc = QLabel("等待开始运行")
        self.run_desc.setObjectName("RunDescription")
        self.run_desc.setAlignment(Qt.AlignCenter)
        self.run_desc.setWordWrap(True)

        rl.addWidget(self.run_status)
        rl.addWidget(self.run_desc)

        info = QFrame()
        info.setObjectName("RunInfoPanel")
        il = QVBoxLayout(info)
        il.setContentsMargins(13, 10, 13, 10)
        il.setSpacing(5)

        task_line = QHBoxLayout()
        task_line.addWidget(QLabel("当前任务"))
        self.current_task_value = QLabel("-")
        self.current_task_value.setObjectName("RunInfoValue")
        task_line.addStretch(1)
        task_line.addWidget(self.current_task_value)
        il.addLayout(task_line)

        progress_line = QHBoxLayout()
        progress_line.addWidget(QLabel("处理进度"))
        self.current_progress_text = QLabel("0%")
        self.current_progress_text.setObjectName("RunInfoValue")
        progress_line.addStretch(1)
        progress_line.addWidget(self.current_progress_text)
        il.addLayout(progress_line)

        self.run_progress = QProgressBar()
        self.run_progress.setValue(0)
        il.addWidget(self.run_progress)

        runtime_line = QHBoxLayout()
        runtime_line.addWidget(QLabel("运行时长"))
        self.current_runtime_value = QLabel("00:00:00")
        self.current_runtime_value.setObjectName("RunInfoValue")
        runtime_line.addStretch(1)
        runtime_line.addWidget(self.current_runtime_value)
        il.addLayout(runtime_line)

        rl.addWidget(info)
        middle.addWidget(run, 1)

        lay.addLayout(middle)

        # 3) Bottom recent-tasks table with the same reference column structure.
        recent = QFrame()
        recent.setObjectName("Card")

        recent_l = QVBoxLayout(recent)
        recent_l.setContentsMargins(16, 12, 16, 14)
        recent_l.setSpacing(7)

        rh = QHBoxLayout()
        clock = QLabel("◷")
        clock.setObjectName("RecentClock")
        recent_title = QLabel("最近任务")
        recent_title.setObjectName("SectionTitle")
        more = QPushButton("查看更多  →")
        more.setObjectName("TextLinkButton")
        more.clicked.connect(lambda: self.switch_page(1))
        more.setCursor(Qt.PointingHandCursor)

        rh.addWidget(clock)
        rh.addWidget(recent_title)
        rh.addStretch(1)
        rh.addWidget(more)
        recent_l.addLayout(rh)

        self.dashboard_table = self._table(
            [
                "任务名称",
                "任务类型",
                "目标店铺",
                "总数量",
                "成功",
                "失败",
                "状态",
                "开始时间",
                "操作",
            ]
        )
        self.dashboard_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.dashboard_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.dashboard_table.verticalHeader().setDefaultSectionSize(38)

        recent_l.addWidget(self.dashboard_table)
        lay.addWidget(recent, 1)

        # Load saved custom range.
        saved_min = self._settings.value("publish_delay_min", 10, type=int)
        saved_max = self._settings.value("publish_delay_max", 15, type=int)
        if saved_max < saved_min:
            saved_max = saved_min

        self.delay_min_spin.setValue(saved_min)
        self.delay_max_spin.setValue(saved_max)
        self.delay_min_spin.valueChanged.connect(self._delay_range_changed)
        self.delay_max_spin.valueChanged.connect(self._delay_range_changed)

        return page

    # ---------- custom delay ----------
    def _delay_range_changed(self, _value=None):
        minimum = int(self.delay_min_spin.value())
        maximum = int(self.delay_max_spin.value())

        sender = self.sender()
        if minimum > maximum:
            if sender is self.delay_min_spin:
                maximum = minimum
                self.delay_max_spin.blockSignals(True)
                self.delay_max_spin.setValue(maximum)
                self.delay_max_spin.blockSignals(False)
            else:
                minimum = maximum
                self.delay_min_spin.blockSignals(True)
                self.delay_min_spin.setValue(minimum)
                self.delay_min_spin.blockSignals(False)

        self._settings.setValue("publish_delay_min", minimum)
        self._settings.setValue("publish_delay_max", maximum)
        self._apply_delay_to_worker()

    def _sync_delay_controls(self):
        if not hasattr(self, "delay_min_spin"):
            return
        minimum = self._settings.value("publish_delay_min", 10, type=int)
        maximum = self._settings.value("publish_delay_max", 15, type=int)
        if maximum < minimum:
            maximum = minimum

        self.delay_min_spin.blockSignals(True)
        self.delay_max_spin.blockSignals(True)
        self.delay_min_spin.setValue(minimum)
        self.delay_max_spin.setValue(maximum)
        self.delay_min_spin.blockSignals(False)
        self.delay_max_spin.blockSignals(False)
        self._apply_delay_to_worker()

    def _apply_delay_to_worker(self):
        worker = getattr(self, "browser_worker", None)
        if worker and hasattr(worker, "set_publish_delay_range"):
            worker.set_publish_delay_range(
                self.delay_min_spin.value(),
                self.delay_max_spin.value(),
            )

    # ---------- real duplicate handling ----------
    def run_pending_tasks(self):
        if not self._ensure_browser():
            return

        if self.safe_mode.isChecked():
            QMessageBox.information(
                self,
                "发布后自动上架已关闭",
                "当前已切换到发布前安全停点。安全停点开启时请一次只运行 1 条任务。",
            )
            return

        rows = list(self.db.tasks(("待执行",)))
        if not rows:
            QMessageBox.information(self, "暂无待执行任务", "当前没有待执行任务。")
            return

        if self.auto_duplicate_checkbox.isChecked():
            unique = []
            seen = set()
            duplicate_codes = []
            for task in rows:
                signature = (
                    str(task["template_name"]).strip(),
                    str(task["new_title"]).strip(),
                    str(task["sku_name"]).strip(),
                    str(task["cover_image"]).strip(),
                )
                if signature in seen:
                    duplicate_codes.append(str(task["task_code"]))
                    continue
                seen.add(signature)
                unique.append(task)
            rows = unique

            if duplicate_codes:
                self.db.add_log(
                    "INFO",
                    "自动处理重复：跳过重复任务 " + "、".join(duplicate_codes[:30]),
                )

        queued = 0
        errors = []
        for task in rows:
            try:
                self._queue_task(task)
                queued += 1
            except Exception as exc:
                errors.append(f"{task['task_code']}: {exc}")

        self.refresh_all()
        self.run_status.setText("运行中" if queued else "未启动")
        self.run_desc.setText(
            f"已加入发布队列 {queued} 条。"
            if queued
            else "没有任务加入发布队列。"
        )

        if errors:
            QMessageBox.warning(
                self,
                "部分任务未加入队列",
                "\n".join(errors[:12]),
            )

    # ---------- auto-publish checkbox ↔ original safe-mode ----------
    def _sync_auto_publish_from_safe_mode(self, safe_checked: bool):
        if not hasattr(self, "auto_publish_checkbox"):
            return
        desired = not bool(safe_checked)
        if self.auto_publish_checkbox.isChecked() != desired:
            self.auto_publish_checkbox.blockSignals(True)
            self.auto_publish_checkbox.setChecked(desired)
            self.auto_publish_checkbox.blockSignals(False)

    # ---------- browser: same original logic, configurable worker ----------
    def open_store_browser(self):
        store = self._selected_store()
        if not store:
            self.db.add_store(
                "默认店铺",
                str(self.root / "data" / "profiles" / "default"),
            )
            store = self.db.stores()[0]
            self.refresh_all()

        if self.browser_worker and self.browser_worker.isRunning():
            if getattr(self.browser_worker, "connected", False):
                QMessageBox.information(
                    self,
                    "浏览器正在运行",
                    "当前店铺浏览器已经运行。需要结束时，请点击“开始运行”旁边的下拉箭头 → “结束运行”。",
                )
                return

            self.browser_worker.request_stop()
            self.browser_worker.wait(2500)
            if self.browser_worker.isRunning():
                QMessageBox.warning(
                    self,
                    "正在结束旧浏览器",
                    "请稍等 1~2 秒后再次点击开始运行。",
                )
                return

        self.active_store_id = int(store["id"])
        self.run_status.setText("正在启动")
        self.run_desc.setText(f"店铺：{store['name']}")
        self.run_progress.setValue(3)

        worker = ConfigurableCancellableBrowserWorker(
            Path(store["profile_dir"]),
            self.root / "config" / "selectors.json",
            self.root / "screenshots",
            self,
            delay_min=self.delay_min_spin.value(),
            delay_max=self.delay_max_spin.value(),
        )
        self.browser_worker = worker

        worker.ready.connect(
            lambda msg, sid=int(store["id"]): self._browser_ready(sid, msg)
        )
        worker.task_step.connect(self._task_step)
        worker.task_done.connect(self._task_done)
        worker.task_error.connect(self._task_error)
        worker.info.connect(self._worker_info)
        worker.connection_state.connect(self._connection_state)
        worker.queue_paused.connect(self._queue_paused)
        worker.pause_state.connect(self._pause_state_changed)
        worker.browser_closed.connect(self._browser_closed)
        worker.current_task_changed.connect(self._current_task_changed)
        worker.task_cancelled.connect(self._task_cancelled)
        worker.finished.connect(lambda w=worker: self._worker_finished_cleanup(w))

        self._run_started_monotonic = time.monotonic()
        self._last_run_seconds = 0.0
        worker.start()

        self.db.add_log("INFO", f"启动店铺浏览器：{store['name']}")

    # ---------- quantity / metrics / recent tasks ----------
    def refresh_all(self):
        super().refresh_all()
        self._sync_reference_dashboard()

    def _sync_reference_dashboard(self):
        if not hasattr(self, "publish_quantity_display"):
            return

        current_tasks = [
            x for x in self.db.tasks()
            if str(x["status"]).strip() not in ("成功",)
        ]
        publishable = [
            x for x in current_tasks
            if str(x["status"]).strip() in (
                "待执行", "排队中", "执行中", "待确认"
            )
        ]

        self.publish_quantity_display.setText(str(len(publishable)))
        self.task_selector_button.setText(
            "＋  点击选择裂变任务\n"
            f"支持多选，当前可发布 {len(publishable)} 个任务"
        )

        # Reference metric semantics.
        try:
            with self.db.connect() as conn:
                today_total = conn.execute(
                    """
                    SELECT COUNT(DISTINCT product_code)
                    FROM logs
                    WHERE product_code <> ''
                      AND date(created_at,'localtime') = date('now','localtime')
                    """
                ).fetchone()[0]

                today_success = conn.execute(
                    """
                    SELECT COUNT(DISTINCT product_code)
                    FROM logs
                    WHERE product_code <> ''
                      AND level='INFO'
                      AND date(created_at,'localtime') = date('now','localtime')
                      AND message LIKE '%成功%'
                    """
                ).fetchone()[0]

                today_failed = conn.execute(
                    """
                    SELECT COUNT(DISTINCT product_code)
                    FROM logs
                    WHERE product_code <> ''
                      AND level IN ('ERROR','WARNING')
                      AND date(created_at,'localtime') = date('now','localtime')
                    """
                ).fetchone()[0]
        except Exception:
            today_total = len(current_tasks) + int(getattr(self, "session_success_count", 0))
            today_success = int(getattr(self, "session_success_count", 0))
            today_failed = len(
                [x for x in current_tasks if str(x["status"]).strip() == "失败"]
            )

        self.m_total.value.setText(str(today_total))
        self.m_pending.value.setText(str(today_success))
        self.m_success.value.setText(str(today_failed))

        self._refresh_reference_recent_tasks(current_tasks)
        self._sync_reference_runtime()
        self.refresh_stores()

    def _refresh_reference_recent_tasks(self, rows):
        if not hasattr(self, "dashboard_table"):
            return

        rows = rows[:5]
        self.dashboard_table.setRowCount(len(rows))

        active_store = None
        if self.active_store_id:
            try:
                active_store = self.db.store(self.active_store_id)
            except Exception:
                active_store = None

        store_name = (
            active_store["name"]
            if active_store
            else (
                self.quick_store_combo.currentText()
                if hasattr(self, "quick_store_combo")
                else "-"
            )
        )

        for r, task in enumerate(rows):
            status = str(task["status"]).strip()
            success = 1 if status == "成功" else 0
            failed = 1 if status == "失败" else 0

            values = [
                task["task_code"],
                "商品发布",
                store_name or "-",
                "1",
                str(success),
                str(failed),
            ]

            for c, value in enumerate(values):
                self.dashboard_table.setItem(r, c, QTableWidgetItem(str(value)))

            self.dashboard_table.setCellWidget(
                r, 6, fixed_status_pill(task["status"])
            )
            self.dashboard_table.setItem(
                r, 7, QTableWidgetItem(str(task["created_at"] or "-"))
            )

            view = QPushButton("查看任务")
            view.setObjectName("TableLinkButton")
            view.setCursor(Qt.PointingHandCursor)
            view.clicked.connect(
                lambda _=False, tid=int(task["id"]): self._open_task_from_dashboard(tid)
            )
            self.dashboard_table.setCellWidget(r, 8, view)

        widths = [170, 110, 160, 85, 75, 75, 105, 165, 100]
        for i, width in enumerate(widths):
            self.dashboard_table.setColumnWidth(i, width)

    def _open_task_from_dashboard(self, task_id: int):
        task = self.db.task(int(task_id))
        if not task:
            self.refresh_all()
            return

        self.switch_page(1)
        self.refresh_tasks()

        failed = str(task["status"]).strip() == "失败"
        if hasattr(self, "task_tabs"):
            self.task_tabs.setCurrentIndex(1 if failed else 0)

        table = self.failed_table if failed else self.pending_table
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.text() == str(task_id):
                table.selectRow(row)
                table.scrollToItem(item)
                break

    # ---------- top store selector ----------
    def refresh_stores(self):
        super().refresh_stores()

        if not hasattr(self, "quick_store_combo"):
            return

        stores = self.db.stores()
        current = self.quick_store_combo.currentData()
        preferred = self.active_store_id if self.active_store_id else current

        self.quick_store_combo.blockSignals(True)
        self.quick_store_combo.clear()

        if not stores:
            self.quick_store_combo.addItem("暂无店铺", None)
        else:
            for store in stores:
                self.quick_store_combo.addItem(store["name"], int(store["id"]))

            idx = self.quick_store_combo.findData(preferred)
            self.quick_store_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.quick_store_combo.blockSignals(False)

    def _quick_store_changed(self, _index: int):
        if not hasattr(self, "store_table"):
            return

        store_id = self.quick_store_combo.currentData()
        if not store_id:
            return

        for row in range(self.store_table.rowCount()):
            item = self.store_table.item(row, 0)
            if item and item.text() == str(store_id):
                self.store_table.selectRow(row)
                self.store_table.scrollToItem(item)
                return

    def _sync_quick_store_from_table(self):
        if not hasattr(self, "store_table") or not hasattr(self, "quick_store_combo"):
            return

        row = self.store_table.currentRow()
        if row < 0:
            return

        item = self.store_table.item(row, 0)
        if not item:
            return

        try:
            store_id = int(item.text())
        except Exception:
            return

        idx = self.quick_store_combo.findData(store_id)
        if idx >= 0 and idx != self.quick_store_combo.currentIndex():
            self.quick_store_combo.blockSignals(True)
            self.quick_store_combo.setCurrentIndex(idx)
            self.quick_store_combo.blockSignals(False)

    # ---------- runtime panel ----------
    def _sync_reference_runtime(self):
        if not hasattr(self, "current_runtime_value"):
            return

        worker = getattr(self, "browser_worker", None)
        running = bool(worker and worker.isRunning())
        connected = bool(running and getattr(worker, "connected", False))

        if running:
            if self._run_started_monotonic is None:
                self._run_started_monotonic = time.monotonic()
            seconds = max(
                0.0,
                time.monotonic() - self._run_started_monotonic,
            )
            self._last_run_seconds = seconds
        else:
            seconds = self._last_run_seconds

        hours = seconds / 3600.0
        self.m_failed.value.setText(f"{hours:.1f} 小时")

        whole = int(seconds)
        hh = whole // 3600
        mm = (whole % 3600) // 60
        ss = whole % 60
        self.current_runtime_value.setText(f"{hh:02d}:{mm:02d}:{ss:02d}")

        progress = int(self.run_progress.value())
        self.current_progress_text.setText(f"{progress}%")

        code = getattr(worker, "current_task_code", "") if worker else ""
        self.current_task_value.setText(code or "-")

        if connected:
            self.engine_label.setText("浏览器已连接 ⌄")
            if not code and self.run_status.text() in (
                "浏览器已关闭", "未启动", "任务已结束"
            ):
                self.run_status.setText("运行中")
                self.run_desc.setText("浏览器已打开，等待发布任务…")
        elif running:
            self.engine_label.setText("正在连接 ⌄")
        else:
            self.engine_label.setText("浏览器未连接 ⌄")
            if self.run_status.text() not in ("成功", "失败", "任务已结束"):
                self.run_status.setText("浏览器已关闭")
                self.run_desc.setText("等待开始运行")

        if hasattr(self, "end_run_action"):
            self.end_run_action.setEnabled(running)

    def _worker_finished_cleanup(self, worker):
        if self._run_started_monotonic is not None:
            self._last_run_seconds = max(
                0.0,
                time.monotonic() - self._run_started_monotonic,
            )
        self._run_started_monotonic = None
        super()._worker_finished_cleanup(worker)
        self._sync_reference_runtime()

    # ---------- visual polish ----------
    def _install_reference_shadows(self):
        for frame in self.findChildren(QFrame):
            name = frame.objectName()
            if name in (
                "Card",
                "ReferenceMetricCard",
                "StartRunSplit",
            ):
                effect = QGraphicsDropShadowEffect(frame)
                effect.setBlurRadius(36)
                effect.setOffset(0, 7)
                effect.setColor(QColor(75, 82, 168, 40))
                frame.setGraphicsEffect(effect)
