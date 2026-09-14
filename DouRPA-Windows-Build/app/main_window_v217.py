from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.main_window import StatusDot, button
from app.main_window_v212 import fixed_status_pill
from app.main_window_v216 import MainWindow as V216MainWindow


class RefMetricCard(QFrame):
    """Reference-layout metric card that stays compatible with refresh_all()."""

    def __init__(self, icon_text: str, title: str, value="0", hint="", accent="blue", parent=None):
        super().__init__(parent)
        self.setObjectName("RefMetricCard")
        self.setProperty("accent", accent)
        self.setMinimumHeight(102)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 13, 16, 13)
        lay.setSpacing(12)

        icon = QLabel(icon_text)
        icon.setObjectName("RefMetricIcon")
        icon.setProperty("accent", accent)
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(48, 48)
        icon_font = QFont("Microsoft YaHei UI", 18)
        icon_font.setWeight(QFont.DemiBold)
        icon.setFont(icon_font)
        lay.addWidget(icon)

        text_box = QVBoxLayout()
        text_box.setSpacing(1)

        t = QLabel(title)
        t.setObjectName("MetricTitle")
        self.value = QLabel(value)
        self.value.setObjectName("MetricValue")
        h = QLabel(hint)
        h.setObjectName("MetricHint")

        text_box.addWidget(t)
        text_box.addWidget(self.value)
        text_box.addWidget(h)
        lay.addLayout(text_box, 1)

        bars = QLabel("▂ ▅ ▃ ▆ ▇")
        bars.setObjectName("MiniBars")
        bars.setProperty("accent", accent)
        bars.setAlignment(Qt.AlignBottom | Qt.AlignRight)
        bars.setFixedWidth(66)
        lay.addWidget(bars)


class FlowStep(QFrame):
    def __init__(self, number: str, text: str, active=False, parent=None):
        super().__init__(parent)
        self.setObjectName("FlowStep")
        self.setProperty("active", bool(active))
        self.setMinimumHeight(44)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(8)

        n = QLabel(number)
        n.setObjectName("FlowStepNumber")
        n.setProperty("active", bool(active))
        n.setAlignment(Qt.AlignCenter)
        n.setFixedSize(28, 28)

        t = QLabel(text)
        t.setObjectName("FlowStepText")
        t.setProperty("active", bool(active))

        lay.addWidget(n)
        lay.addWidget(t)
        lay.addStretch(1)


class MainWindow(V216MainWindow):
    """V2.1.7 — reference-image layout reconstruction.

    This class changes layout and presentation only. All business actions are
    wired to existing V2.1.5/V2.1.6 methods and signals.
    """

    def __init__(self, root: Path):
        super().__init__(root)
        self.resize(1672, 941)
        self.setMinimumSize(1360, 800)

        if hasattr(self, "store_table"):
            try:
                self.store_table.itemSelectionChanged.connect(self._sync_quick_store_from_table)
            except Exception:
                pass

        self._install_reference_shadows()
        self.refresh_all()

    # ---------- shell ----------
    def _sidebar(self):
        panel = QFrame()
        panel.setObjectName("Sidebar")
        panel.setFixedWidth(270)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(20, 24, 18, 20)
        lay.setSpacing(8)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(12)

        icon = QLabel()
        icon.setObjectName("BrandIcon")
        icon.setFixedSize(48, 48)
        pix = QApplication.windowIcon().pixmap(42, 42)
        icon.setPixmap(pix)
        icon.setAlignment(Qt.AlignCenter)

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
        lay.addSpacing(18)

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
            b = QPushButton(f"  {glyph}     {text}")
            b.setObjectName("NavButton")
            b.setProperty("active", idx == 0)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(48)
            b.clicked.connect(lambda _=False, i=idx: self.switch_page(i))
            lay.addWidget(b)
            self.nav.append(b)

        lay.addStretch(1)

        blob = QFrame()
        blob.setObjectName("SidebarLiquidBlob")
        blob_lay = QVBoxLayout(blob)
        blob_lay.setContentsMargins(18, 18, 18, 18)
        blob_lay.setSpacing(5)

        big = QLabel("AI 助力")
        big.setObjectName("SidebarBlobTitle")
        small = QLabel("放大生意增长")
        small.setObjectName("SidebarBlobSub")
        ver = QLabel("DouRPA Pro\n专业版")
        ver.setObjectName("SidebarVersion")

        blob_lay.addStretch(1)
        blob_lay.addWidget(big)
        blob_lay.addWidget(small)
        blob_lay.addStretch(1)
        blob_lay.addWidget(ver)

        lay.addWidget(blob)
        return panel

    def _topbar(self):
        f = QFrame()
        f.setObjectName("Topbar")
        f.setFixedHeight(92)

        lay = QHBoxLayout(f)
        lay.setContentsMargins(4, 0, 0, 0)
        lay.setSpacing(10)

        self._titles = [
            ("工作台", "高效 · 稳定 · 智能  让重复的工作交给 DouRPA Pro"),
            ("裂变任务", "批量管理相似品发布任务"),
            ("店铺管理", "独立店铺 Profile 与登录状态"),
            ("源商品模板", "管理源商品定位与复用模板"),
            ("运行日志", "查看自动化节点、错误与恢复信息"),
            ("系统设置", "控制发布安全停点与页面配置"),
        ]

        vl = QVBoxLayout()
        vl.setSpacing(3)
        self.page_title = QLabel(self._titles[0][0])
        self.page_title.setObjectName("PageTitle")
        self.page_sub = QLabel(self._titles[0][1])
        self.page_sub.setObjectName("PageSub")
        vl.addWidget(self.page_title)
        vl.addWidget(self.page_sub)

        lay.addLayout(vl)
        lay.addStretch(1)

        live = QFrame()
        live.setObjectName("ConnectionPill")
        ll = QHBoxLayout(live)
        ll.setContentsMargins(13, 8, 13, 8)
        ll.setSpacing(7)
        ll.addWidget(StatusDot("#20C67A", 8))
        self.engine_label = QLabel("浏览器未连接")
        self.engine_label.setObjectName("EngineStatus")
        ll.addWidget(self.engine_label)
        lay.addWidget(live)

        self.quick_store_combo = QComboBox()
        self.quick_store_combo.setObjectName("QuickStoreCombo")
        self.quick_store_combo.setMinimumWidth(150)
        self.quick_store_combo.currentIndexChanged.connect(self._quick_store_changed)
        lay.addWidget(self.quick_store_combo)

        open_btn = button("打开当前店铺", "primary")
        open_btn.setObjectName("TopRunButton")
        open_btn.clicked.connect(self.open_store_browser)
        lay.addWidget(open_btn)

        return f

    # ---------- exact workbench composition ----------
    def _dashboard_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # Top four cards — same visual rhythm as the reference.
        metrics = QHBoxLayout()
        metrics.setSpacing(12)

        self.m_total = RefMetricCard("▤", "裂变任务", "0", "当前未完成任务", "blue")
        self.m_pending = RefMetricCard("◈", "待处理", "0", "待执行 / 排队 / 待确认", "purple")
        self.m_success = RefMetricCard("✓", "本次完成", "0", "本次启动后成功发布", "green")
        self.m_failed = RefMetricCard("×", "失败任务", "0", "可重新执行", "red")

        for m in (self.m_total, self.m_pending, self.m_success, self.m_failed):
            metrics.addWidget(m, 1)
        lay.addLayout(metrics)

        # Middle: left wide batch panel + right current run panel.
        middle = QHBoxLayout()
        middle.setSpacing(14)

        batch = QFrame()
        batch.setObjectName("Card")
        batch_lay = QVBoxLayout(batch)
        batch_lay.setContentsMargins(18, 16, 18, 16)
        batch_lay.setSpacing(12)

        batch_head = QHBoxLayout()
        batch_title_box = QVBoxLayout()
        batch_title_box.setSpacing(1)

        batch_title = QLabel("批量发布")
        batch_title.setObjectName("SectionTitle")
        batch_sub = QLabel("导入裂变任务，按原版逻辑批量发布到当前店铺")
        batch_sub.setObjectName("SectionSub")

        batch_title_box.addWidget(batch_title)
        batch_title_box.addWidget(batch_sub)
        batch_head.addLayout(batch_title_box)
        batch_head.addStretch(1)

        settings_btn = button("系统设置")
        settings_btn.clicked.connect(lambda: self.switch_page(5))
        batch_head.addWidget(settings_btn)
        batch_lay.addLayout(batch_head)

        steps = QHBoxLayout()
        steps.setSpacing(8)
        step_defs = [
            ("1", "导入任务"),
            ("2", "打开店铺"),
            ("3", "执行修改"),
            ("4", "提交发布"),
        ]
        for i, (num, text) in enumerate(step_defs):
            s = FlowStep(num, text, active=(i == 0))
            steps.addWidget(s, 1)
            if i < len(step_defs) - 1:
                arrow = QLabel("›")
                arrow.setObjectName("FlowArrow")
                arrow.setAlignment(Qt.AlignCenter)
                arrow.setFixedWidth(12)
                steps.addWidget(arrow)
        batch_lay.addLayout(steps)

        self.dashboard_import_button = QPushButton(
            "＋  点击导入裂变任务 Excel\n"
            "按原版模板导入，导入后可在“裂变任务”中多选执行"
        )
        self.dashboard_import_button.setObjectName("DropZoneButton")
        self.dashboard_import_button.setCursor(Qt.PointingHandCursor)
        self.dashboard_import_button.setMinimumHeight(92)
        self.dashboard_import_button.clicked.connect(self.import_excel)
        batch_lay.addWidget(self.dashboard_import_button)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        run_selected = button("运行已选择")
        run_selected.clicked.connect(self.run_selected_tasks)

        self.pause_button = button("暂停执行", "danger")
        self.pause_button.clicked.connect(self.pause_execution)

        self.resume_button = button("继续执行")
        self.resume_button.clicked.connect(self.resume_execution)
        self.resume_button.setEnabled(False)

        run_all = button("▶  运行全部待执行", "primary")
        run_all.setObjectName("DashboardRunButton")
        run_all.clicked.connect(self.run_pending_tasks)
        run_all.setMinimumWidth(200)

        action_row.addWidget(run_selected)
        action_row.addWidget(self.pause_button)
        action_row.addWidget(self.resume_button)
        action_row.addStretch(1)
        action_row.addWidget(run_all)

        batch_lay.addLayout(action_row)
        middle.addWidget(batch, 2)

        run = QFrame()
        run.setObjectName("Card")
        run_lay = QVBoxLayout(run)
        run_lay.setContentsMargins(18, 16, 18, 16)
        run_lay.setSpacing(9)

        run_head = QHBoxLayout()
        run_title = QLabel("当前运行")
        run_title.setObjectName("SectionTitle")
        detail = QPushButton("查看任务  →")
        detail.setObjectName("TextLinkButton")
        detail.setCursor(Qt.PointingHandCursor)
        detail.clicked.connect(lambda: self.switch_page(1))
        run_head.addWidget(run_title)
        run_head.addStretch(1)
        run_head.addWidget(detail)
        run_lay.addLayout(run_head)

        orb = QFrame()
        orb.setObjectName("RunOrb")
        orb.setFixedSize(104, 104)
        orb_l = QVBoxLayout(orb)
        orb_l.setContentsMargins(18, 18, 18, 18)
        app_icon = QLabel()
        app_icon.setAlignment(Qt.AlignCenter)
        app_icon.setPixmap(QApplication.windowIcon().pixmap(58, 58))
        orb_l.addWidget(app_icon, 1, Qt.AlignCenter)
        run_lay.addWidget(orb, 0, Qt.AlignHCenter)

        self.run_status = QLabel("未启动")
        self.run_status.setObjectName("RunStatusBig")
        self.run_status.setAlignment(Qt.AlignCenter)

        self.run_desc = QLabel("选择店铺并打开浏览器后，即可执行裂变任务。")
        self.run_desc.setObjectName("RunDescription")
        self.run_desc.setWordWrap(True)
        self.run_desc.setAlignment(Qt.AlignCenter)

        self.run_progress = QProgressBar()
        self.run_progress.setValue(0)

        run_lay.addWidget(self.run_status)
        run_lay.addWidget(self.run_desc)
        run_lay.addWidget(self.run_progress)

        control_row = QHBoxLayout()
        control_row.setSpacing(7)

        self.end_task_button = button("结束任务", "danger")
        self.end_task_button.setEnabled(False)
        self.end_task_button.clicked.connect(self.end_current_task)

        self.end_run_button = button("结束运行", "danger")
        self.end_run_button.setEnabled(False)
        self.end_run_button.clicked.connect(self.end_current_run)

        control_row.addWidget(self.end_task_button)
        control_row.addWidget(self.end_run_button)
        run_lay.addLayout(control_row)

        middle.addWidget(run, 1)
        lay.addLayout(middle)

        # Bottom: full-width recent/current task table like the reference.
        recent = QFrame()
        recent.setObjectName("Card")
        recent_lay = QVBoxLayout(recent)
        recent_lay.setContentsMargins(16, 13, 16, 14)
        recent_lay.setSpacing(8)

        recent_head = QHBoxLayout()
        recent_title = QLabel("最近任务")
        recent_title.setObjectName("SectionTitle")
        more = QPushButton("查看更多  →")
        more.setObjectName("TextLinkButton")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(lambda: self.switch_page(1))

        recent_head.addWidget(recent_title)
        recent_head.addStretch(1)
        recent_head.addWidget(more)
        recent_lay.addLayout(recent_head)

        self.dashboard_table = self._table([
            "任务编号",
            "源模板",
            "新标题",
            "SKU名称",
            "进度",
            "当前步骤",
            "状态",
            "操作",
        ])
        self.dashboard_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.dashboard_table.setSelectionMode(QAbstractItemView.SingleSelection)
        recent_lay.addWidget(self.dashboard_table)

        lay.addWidget(recent, 1)
        return page

    # ---------- reference-layout data refresh ----------
    def refresh_dashboard(self):
        if not hasattr(self, "dashboard_table"):
            return

        rows = [
            x for x in self.db.tasks()
            if str(x["status"]).strip() != "成功"
        ][:7]

        self.dashboard_table.setRowCount(len(rows))
        self.dashboard_table.verticalHeader().setDefaultSectionSize(38)

        for r, x in enumerate(rows):
            values = [
                x["task_code"],
                x["template_name"],
                x["new_title"],
                x["sku_name"],
            ]
            for c, v in enumerate(values):
                self.dashboard_table.setItem(r, c, QTableWidgetItem(str(v)))

            prog = QProgressBar()
            prog.setValue(int(x["progress"] or 0))
            self.dashboard_table.setCellWidget(r, 4, prog)

            self.dashboard_table.setItem(
                r, 5, QTableWidgetItem(x["current_step"] or "等待")
            )
            self.dashboard_table.setCellWidget(
                r, 6, fixed_status_pill(x["status"])
            )

            view = QPushButton("查看任务")
            view.setObjectName("TableLinkButton")
            view.setCursor(Qt.PointingHandCursor)
            view.clicked.connect(
                lambda _=False, tid=int(x["id"]): self._open_task_from_dashboard(tid)
            )
            self.dashboard_table.setCellWidget(r, 7, view)

        self.dashboard_table.resizeColumnsToContents()
        self.dashboard_table.setColumnWidth(0, 120)
        self.dashboard_table.setColumnWidth(1, 155)
        self.dashboard_table.setColumnWidth(2, 360)
        self.dashboard_table.setColumnWidth(3, 160)
        self.dashboard_table.setColumnWidth(4, 130)
        self.dashboard_table.setColumnWidth(5, 190)
        self.dashboard_table.setColumnWidth(6, 110)
        self.dashboard_table.setColumnWidth(7, 90)

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

    # ---------- top quick-store selector ----------
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
            if idx < 0:
                idx = 0
            self.quick_store_combo.setCurrentIndex(idx)
        self.quick_store_combo.blockSignals(False)

    def _quick_store_changed(self, _index: int):
        store_id = self.quick_store_combo.currentData()
        if not store_id or not hasattr(self, "store_table"):
            return

        for row in range(self.store_table.rowCount()):
            item = self.store_table.item(row, 0)
            if item and item.text() == str(store_id):
                self.store_table.selectRow(row)
                self.store_table.scrollToItem(item)
                return

    def _sync_quick_store_from_table(self):
        if not hasattr(self, "quick_store_combo") or not hasattr(self, "store_table"):
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

    # ---------- visual polish only ----------
    def _install_reference_shadows(self):
        for frame in self.findChildren(QFrame):
            if frame.objectName() not in ("Card", "RefMetricCard"):
                continue
            effect = QGraphicsDropShadowEffect(frame)
            effect.setBlurRadius(38)
            effect.setOffset(0, 8)
            effect.setColor(QColor(78, 91, 170, 34))
            frame.setGraphicsEffect(effect)
