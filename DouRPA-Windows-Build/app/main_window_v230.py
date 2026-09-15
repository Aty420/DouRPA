from __future__ import annotations

import os
import re
from pathlib import Path

from openpyxl import load_workbook
from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.collector_worker import ProductCollectorWorker
from app.collectors.douyin_mumu import MuMuAdbController
from app.main_window import StatusDot, button
from app.main_window_v220 import MainWindow as V220MainWindow
from app.url_downloader import DownloadWorker, sanitize_filename


class MainWindow(V220MainWindow):
    """V2.3.0 adds independent product collection and URL batch download tools."""

    TOP_BLOCK_W = 172
    TOP_BLOCK_H = 48

    def __init__(self, root: Path):
        self.collector_worker = None
        self.download_worker = None
        self._collector_rows = []
        self.tool_settings = QSettings("LocalOps", "DouRPA")
        super().__init__(root)

        self.stack.addWidget(self._collector_page())
        self.stack.addWidget(self._url_downloader_page())
        self.refresh_all()
        self._refresh_mumu_status(silent=True)

    # ---------- shell with 2 new tools ----------
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
        lay.addSpacing(16)

        self.nav = []
        items = [
            ("⌂", "工作台", 0),
            ("▱", "裂变任务", 1),
            ("▢", "店铺管理", 2),
            ("◇", "源商品模板", 3),
            ("≡", "运行日志", 4),
            ("⚙", "系统设置", 5),
            ("⌕", "商品素材采集", 6),
            ("⇩", "URL批量下载", 7),
        ]
        for glyph, text, idx in items:
            nav = QPushButton(f"  {glyph}     {text}")
            nav.setObjectName("NavButton")
            nav.setProperty("active", idx == 0)
            nav.setCursor(Qt.PointingHandCursor)
            nav.setFixedHeight(46)
            nav.clicked.connect(lambda _=False, i=idx: self.switch_page(i))
            lay.addWidget(nav)
            self.nav.append(nav)

        lay.addStretch(1)
        version = QLabel("DouRPA Pro  ·  v2.5.0")
        version.setObjectName("SidebarFooterPlain")
        lay.addWidget(version)
        return panel

    def _topbar(self):
        top = super()._topbar()
        self._titles = [
            ("工作台", "高效 · 稳定 · 智能  让重复的工作交给 DouRPA Pro"),
            ("裂变任务", "批量管理相似品发布任务"),
            ("店铺管理", "独立店铺 Profile 与登录状态"),
            ("源商品模板", "管理源商品定位与复用模板"),
            ("运行日志", "查看自动化节点、错误与恢复信息"),
            ("系统设置", "控制发布安全停点与页面配置"),
            ("商品素材采集", "淘宝 / 抖音 MuMu：采集商品标题、首图URL、SKU标题"),
            ("URL批量下载", "批量粘贴或导入首图URL，统一下载到本地文件夹"),
        ]
        return top

    # ---------- collector page ----------
    def _collector_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        control = QFrame()
        control.setObjectName("Card")
        cl = QVBoxLayout(control)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(10)

        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("商品素材采集")
        title.setObjectName("SectionTitle")
        sub = QLabel("只采集：商品标题 · 首图URL · SKU标题；抖音通过已登录的 MuMu 模拟器执行。")
        sub.setObjectName("SectionSub")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        head.addLayout(title_box)
        head.addStretch(1)
        self.mumu_status = QLabel("MuMu：未检测")
        self.mumu_status.setObjectName("ToolStatusPill")
        reconnect = button("重新连接 MuMu")
        reconnect.clicked.connect(lambda: self._refresh_mumu_status(silent=False))
        head.addWidget(self.mumu_status)
        head.addWidget(reconnect)
        cl.addLayout(head)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.collect_keyword = QLineEdit()
        self.collect_keyword.setPlaceholderText("输入产品关键词，例如：心相印厨房纸")
        self.collect_keyword.setMinimumWidth(360)
        self.collect_taobao = QCheckBox("淘宝")
        self.collect_taobao.setChecked(True)
        self.collect_douyin = QCheckBox("抖音")
        self.collect_douyin.setChecked(True)
        row1.addWidget(QLabel("产品关键词"))
        row1.addWidget(self.collect_keyword, 1)
        row1.addWidget(self.collect_taobao)
        row1.addWidget(self.collect_douyin)
        cl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.taobao_count = QSpinBox()
        self.taobao_count.setRange(1, 200)
        self.taobao_count.setValue(self.tool_settings.value("collector_taobao_count", 20, type=int))
        self.douyin_count = QSpinBox()
        self.douyin_count.setRange(1, 200)
        self.douyin_count.setValue(self.tool_settings.value("collector_douyin_count", 20, type=int))
        self.collect_auto_download = QCheckBox("采集后自动下载首图到统一文件夹")
        self.collect_auto_download.setChecked(True)
        row2.addWidget(QLabel("淘宝数量"))
        row2.addWidget(self.taobao_count)
        row2.addSpacing(12)
        row2.addWidget(QLabel("抖音数量"))
        row2.addWidget(self.douyin_count)
        row2.addSpacing(16)
        row2.addWidget(self.collect_auto_download)
        row2.addStretch(1)
        cl.addLayout(row2)

        row3 = QHBoxLayout()
        default_dir = Path(self.tool_settings.value("collector_output_dir", str(self.root / "采集结果")))
        self.collect_output_dir = QLineEdit(str(default_dir))
        choose_dir = button("选择目录")
        choose_dir.clicked.connect(self._choose_collect_output_dir)
        self.collect_start = button("开始采集", "primary")
        self.collect_start.clicked.connect(self._start_collection)
        self.collect_stop = button("停止采集", "danger")
        self.collect_stop.setEnabled(False)
        self.collect_stop.clicked.connect(self._stop_collection)
        row3.addWidget(QLabel("保存目录"))
        row3.addWidget(self.collect_output_dir, 1)
        row3.addWidget(choose_dir)
        row3.addWidget(self.collect_start)
        row3.addWidget(self.collect_stop)
        cl.addLayout(row3)

        self.collect_info = QLabel("等待开始采集")
        self.collect_info.setObjectName("SectionSub")
        self.collect_progress = QProgressBar()
        self.collect_progress.setRange(0, 100)
        self.collect_progress.setValue(0)
        cl.addWidget(self.collect_info)
        cl.addWidget(self.collect_progress)
        lay.addWidget(control)

        results = QFrame()
        results.setObjectName("Card")
        rl = QVBoxLayout(results)
        rl.setContentsMargins(16, 14, 16, 14)
        rh = QHBoxLayout()
        rt = QLabel("实时采集结果")
        rt.setObjectName("SectionTitle")
        self.collect_count_label = QLabel("0 条")
        self.collect_count_label.setObjectName("SelectionCount")
        rh.addWidget(rt)
        rh.addStretch(1)
        rh.addWidget(self.collect_count_label)
        rl.addLayout(rh)
        self.collect_table = self._table(["平台", "商品标题", "首图URL", "SKU标题", "状态"])
        self.collect_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        rl.addWidget(self.collect_table)
        lay.addWidget(results, 1)
        return page

    def _refresh_mumu_status(self, silent=False):
        if not hasattr(self, "mumu_status"):
            return
        try:
            text = MuMuAdbController().status()
            self.mumu_status.setText(text)
            self.mumu_status.setProperty("state", "ok")
        except Exception as exc:
            self.mumu_status.setText("MuMu：未连接")
            self.mumu_status.setProperty("state", "bad")
            if not silent:
                QMessageBox.warning(self, "MuMu 连接失败", str(exc))
        self.mumu_status.style().unpolish(self.mumu_status)
        self.mumu_status.style().polish(self.mumu_status)

    def _choose_collect_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择采集结果保存目录", self.collect_output_dir.text())
        if path:
            self.collect_output_dir.setText(path)
            self.tool_settings.setValue("collector_output_dir", path)

    def _start_collection(self):
        keyword = self.collect_keyword.text().strip()
        if not keyword:
            QMessageBox.information(self, "缺少关键词", "请先输入产品关键词。")
            return
        platforms = {}
        if self.collect_taobao.isChecked():
            platforms["淘宝"] = int(self.taobao_count.value())
        if self.collect_douyin.isChecked():
            platforms["抖音"] = int(self.douyin_count.value())
        if not platforms:
            QMessageBox.information(self, "未选择平台", "请至少选择淘宝或抖音。")
            return
        if self.collector_worker and self.collector_worker.isRunning():
            return

        output_parent = Path(self.collect_output_dir.text().strip() or self.root / "采集结果")
        output_parent.mkdir(parents=True, exist_ok=True)
        self.tool_settings.setValue("collector_output_dir", str(output_parent))
        self.tool_settings.setValue("collector_taobao_count", self.taobao_count.value())
        self.tool_settings.setValue("collector_douyin_count", self.douyin_count.value())

        self._collector_rows = []
        self.collect_table.setRowCount(0)
        self.collect_progress.setValue(0)
        self.collect_start.setEnabled(False)
        self.collect_stop.setEnabled(True)
        self.collect_info.setText("正在启动采集…")

        worker = ProductCollectorWorker(
            self.root,
            keyword,
            platforms,
            output_parent,
            auto_download_images=self.collect_auto_download.isChecked(),
            parent=self,
        )
        self.collector_worker = worker
        worker.info.connect(self.collect_info.setText)
        worker.item_result.connect(self._collector_item_result)
        worker.progress.connect(self._collector_progress)
        worker.finished_summary.connect(self._collector_finished)
        worker.start()

    def _stop_collection(self):
        if self.collector_worker and self.collector_worker.isRunning():
            self.collector_worker.request_stop()
            self.collect_info.setText("正在停止采集…")
            self.collect_stop.setEnabled(False)

    def _collector_item_result(self, item: dict):
        self._collector_rows.append(item)
        row = self.collect_table.rowCount()
        self.collect_table.insertRow(row)
        values = [
            item.get("platform", ""),
            item.get("title", ""),
            item.get("image_url", ""),
            "；".join(item.get("sku_titles") or []),
            item.get("status", ""),
        ]
        for c, value in enumerate(values):
            self.collect_table.setItem(row, c, QTableWidgetItem(str(value)))
        self.collect_table.setColumnWidth(0, 80)
        self.collect_table.setColumnWidth(1, 360)
        self.collect_table.setColumnWidth(2, 420)
        self.collect_table.setColumnWidth(3, 440)
        self.collect_table.setColumnWidth(4, 220)
        self.collect_count_label.setText(f"{len(self._collector_rows)} 条")

    def _collector_progress(self, done: int, total: int):
        pct = int(done * 100 / total) if total else 0
        self.collect_progress.setValue(max(0, min(100, pct)))

    def _collector_finished(self, summary: dict):
        self.collect_start.setEnabled(True)
        self.collect_stop.setEnabled(False)
        self.collect_progress.setValue(100 if not summary.get("stopped") else self.collect_progress.value())
        errors = summary.get("errors") or []
        self.collect_info.setText(
            f"采集结束：{summary.get('count', 0)} 条 · {summary.get('output_dir', '')}"
        )
        text = (
            f"共采集 {summary.get('count', 0)} 条。\n\n"
            f"Excel：{summary.get('excel_path', '')}\n"
            f"保存目录：{summary.get('output_dir', '')}"
        )
        if errors:
            text += "\n\n异常：\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "采集完成" if not summary.get("stopped") else "采集已停止", text)

    # ---------- URL downloader page ----------
    def _url_downloader_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        input_card = QFrame()
        input_card.setObjectName("Card")
        il = QVBoxLayout(input_card)
        il.setContentsMargins(18, 16, 18, 16)
        il.setSpacing(10)

        h = QHBoxLayout()
        tbox = QVBoxLayout()
        title = QLabel("URL 批量下载")
        title.setObjectName("SectionTitle")
        sub = QLabel("支持直接粘贴、TXT、Excel；采集结果 Excel 会自动识别“商品标题 / 首图URL”。")
        sub.setObjectName("SectionSub")
        tbox.addWidget(title)
        tbox.addWidget(sub)
        h.addLayout(tbox)
        h.addStretch(1)
        paste = button("粘贴剪贴板")
        paste.clicked.connect(self._paste_urls)
        import_txt = button("导入 TXT")
        import_txt.clicked.connect(self._import_url_txt)
        import_excel = button("导入 Excel", "primary")
        import_excel.clicked.connect(self._import_url_excel)
        h.addWidget(paste)
        h.addWidget(import_txt)
        h.addWidget(import_excel)
        il.addLayout(h)

        self.url_input = QPlainTextEdit()
        self.url_input.setObjectName("UrlInputBox")
        self.url_input.setPlaceholderText(
            "每行一个 URL；也支持：文件名<TAB>URL\n\n"
            "例如：\n001\thttps://example.com/a.webp\nhttps://example.com/b.jpg"
        )
        self.url_input.setMinimumHeight(170)
        il.addWidget(self.url_input)

        settings = QHBoxLayout()
        default_download = self.tool_settings.value(
            "url_download_dir", str(self.root / "URL下载")
        )
        self.url_download_dir = QLineEdit(str(default_download))
        choose = button("选择目录")
        choose.clicked.connect(self._choose_download_dir)
        self.url_concurrency = QSpinBox()
        self.url_concurrency.setRange(1, 16)
        self.url_concurrency.setValue(self.tool_settings.value("url_concurrency", 5, type=int))
        self.url_retries = QSpinBox()
        self.url_retries.setRange(0, 5)
        self.url_retries.setValue(self.tool_settings.value("url_retries", 2, type=int))
        settings.addWidget(QLabel("下载目录"))
        settings.addWidget(self.url_download_dir, 1)
        settings.addWidget(choose)
        settings.addSpacing(12)
        settings.addWidget(QLabel("并发"))
        settings.addWidget(self.url_concurrency)
        settings.addWidget(QLabel("失败重试"))
        settings.addWidget(self.url_retries)
        il.addLayout(settings)

        actions = QHBoxLayout()
        self.url_start = button("开始下载", "primary")
        self.url_start.clicked.connect(self._start_url_download)
        self.url_stop = button("停止下载", "danger")
        self.url_stop.setEnabled(False)
        self.url_stop.clicked.connect(self._stop_url_download)
        self.url_info = QLabel("等待导入 URL")
        self.url_info.setObjectName("SectionSub")
        actions.addWidget(self.url_start)
        actions.addWidget(self.url_stop)
        actions.addSpacing(12)
        actions.addWidget(self.url_info)
        actions.addStretch(1)
        il.addLayout(actions)
        lay.addWidget(input_card)

        result = QFrame()
        result.setObjectName("Card")
        rl = QVBoxLayout(result)
        rl.setContentsMargins(16, 14, 16, 14)
        rt = QLabel("下载任务")
        rt.setObjectName("SectionTitle")
        rl.addWidget(rt)
        self.url_table = self._table(["文件名", "URL", "状态", "进度"])
        self.url_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        rl.addWidget(self.url_table)
        lay.addWidget(result, 1)
        return page

    def _paste_urls(self):
        text = QApplication.clipboard().text().strip()
        if text:
            if self.url_input.toPlainText().strip():
                self.url_input.appendPlainText(text)
            else:
                self.url_input.setPlainText(text)

    def _import_url_txt(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 URL TXT", "", "Text (*.txt);;All (*.*)")
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        self.url_input.setPlainText(text)

    def _import_url_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 URL Excel", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        try:
            wb = load_workbook(path, data_only=True, read_only=True)
            ws = wb.active
            headers = [str(c.value or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1))]
            url_idx = -1
            name_idx = -1
            for i, h in enumerate(headers):
                hl = h.lower()
                if url_idx < 0 and ("url" in hl or "链接" in h):
                    url_idx = i
                if name_idx < 0 and ("商品标题" in h or h in ("标题", "文件名")):
                    name_idx = i
            lines = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                values = list(row)
                url = ""
                name = ""
                if 0 <= url_idx < len(values):
                    url = str(values[url_idx] or "").strip()
                else:
                    for value in values:
                        text = str(value or "").strip()
                        if text.startswith(("http://", "https://")):
                            url = text
                            break
                if 0 <= name_idx < len(values):
                    name = str(values[name_idx] or "").strip()
                if url:
                    lines.append(f"{name}\t{url}" if name else url)
            self.url_input.setPlainText("\n".join(lines))
            self.url_info.setText(f"已从 Excel 导入 {len(lines)} 个 URL")
        except Exception as exc:
            QMessageBox.critical(self, "Excel 导入失败", str(exc))

    def _choose_download_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择下载目录", self.url_download_dir.text())
        if path:
            self.url_download_dir.setText(path)
            self.tool_settings.setValue("url_download_dir", path)

    def _parse_url_items(self):
        items = []
        seen = set()
        for i, raw in enumerate(self.url_input.toPlainText().splitlines(), 1):
            raw = raw.strip()
            if not raw:
                continue
            name = ""
            url = raw
            if "\t" in raw:
                left, right = raw.split("\t", 1)
                if right.strip().startswith(("http://", "https://")):
                    name, url = left.strip(), right.strip()
            if not url.startswith(("http://", "https://")):
                match = re.search(r"https?://\S+", raw)
                if not match:
                    continue
                url = match.group(0)
                name = raw[: match.start()].strip(" \t-：:")
            if url in seen:
                continue
            seen.add(url)
            items.append({"name": sanitize_filename(name or f"{len(items)+1:03d}"), "url": url})
        return items

    def _start_url_download(self):
        items = self._parse_url_items()
        if not items:
            QMessageBox.information(self, "没有可下载 URL", "请粘贴或导入至少一个有效的 http/https URL。")
            return
        if self.download_worker and self.download_worker.isRunning():
            return
        dest = Path(self.url_download_dir.text().strip() or self.root / "URL下载")
        dest.mkdir(parents=True, exist_ok=True)
        self.tool_settings.setValue("url_download_dir", str(dest))
        self.tool_settings.setValue("url_concurrency", self.url_concurrency.value())
        self.tool_settings.setValue("url_retries", self.url_retries.value())

        self.url_table.setRowCount(len(items))
        for r, item in enumerate(items):
            self.url_table.setItem(r, 0, QTableWidgetItem(item["name"]))
            self.url_table.setItem(r, 1, QTableWidgetItem(item["url"]))
            self.url_table.setItem(r, 2, QTableWidgetItem("等待"))
            p = QProgressBar()
            p.setValue(0)
            self.url_table.setCellWidget(r, 3, p)
        self.url_table.setColumnWidth(0, 220)
        self.url_table.setColumnWidth(1, 650)
        self.url_table.setColumnWidth(2, 260)
        self.url_table.setColumnWidth(3, 180)

        self.url_start.setEnabled(False)
        self.url_stop.setEnabled(True)
        worker = DownloadWorker(
            items,
            dest,
            concurrency=self.url_concurrency.value(),
            retries=self.url_retries.value(),
            parent=self,
        )
        self.download_worker = worker
        worker.item_progress.connect(self._url_item_progress)
        worker.info.connect(self.url_info.setText)
        worker.finished_summary.connect(self._url_download_finished)
        worker.start()

    def _stop_url_download(self):
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.request_stop()
            self.url_info.setText("正在停止下载…")
            self.url_stop.setEnabled(False)

    def _url_item_progress(self, index: int, name: str, status: str, progress: int):
        if index < 0 or index >= self.url_table.rowCount():
            return
        self.url_table.setItem(index, 0, QTableWidgetItem(name))
        self.url_table.setItem(index, 2, QTableWidgetItem(status))
        widget = self.url_table.cellWidget(index, 3)
        if isinstance(widget, QProgressBar):
            widget.setValue(progress)

    def _url_download_finished(self, summary: dict):
        self.url_start.setEnabled(True)
        self.url_stop.setEnabled(False)
        self.url_info.setText(
            f"完成 {summary.get('success', 0)} · 失败 {summary.get('failed', 0)} · {summary.get('dest_dir', '')}"
        )
        text = (
            f"下载完成：{summary.get('success', 0)} 个\n"
            f"失败：{summary.get('failed', 0)} 个\n"
            f"目录：{summary.get('dest_dir', '')}"
        )
        if summary.get("errors"):
            text += "\n\n失败详情：\n" + "\n".join(summary["errors"][:10])
        QMessageBox.information(self, "URL 下载完成", text)
