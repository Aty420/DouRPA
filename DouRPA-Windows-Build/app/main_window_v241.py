from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from openpyxl import load_workbook
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QTableWidgetItem,
)

from app.main_window_v240 import MainWindow as V240MainWindow
from app.url_downloader import (
    ProductLinkWorker,
    _clean,
    _is_closed_error,
    _profile_dir,
    _save_jpg_from_url,
    resolve_page,
    sanitize_filename,
)


def _extract_records(text: str) -> list[dict]:
    """Parse raw Douyin/Taobao share text and preserve the product-title hint.

    Example:
    ... https://v.douyin.com/xxxx/ 【建议拍三件】心相印金装经典...
    长按复制此条消息...

    Returns:
    [{"url": "...", "title_hint": "【建议拍三件】心相印金装经典..."}]
    """
    text = str(text or "")
    matches = list(re.finditer(r"https?://[^\s，。；;]+", text))
    result = []
    seen = set()

    for i, match in enumerate(matches):
        url = match.group(0).rstrip(")】]}>\"'")
        if not url or url in seen:
            continue

        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        tail = text[match.end():end]

        # The actual copied product title is usually directly after the URL.
        # Stop before Douyin's instructional sentence or at the next line.
        tail = re.split(
            r"(?:\r?\n|长按复制此条消息|打开抖音搜索|查看商品详情)",
            tail,
            maxsplit=1,
        )[0]
        title_hint = tail.strip(" \t\r\n：:，,。；;")

        # Remove platform-only labels if a client happens to repeat them after the URL.
        title_hint = re.sub(r"^(?:【抖音商城】|【淘宝】|【天猫】)\s*", "", title_hint).strip()

        # Ignore obviously non-title fragments.
        if not title_hint or title_hint.startswith(("http://", "https://")):
            title_hint = ""

        seen.add(url)
        result.append(
            {
                "url": url,
                "title_hint": title_hint,
                "raw_text": text[match.start():end].strip(),
            }
        )

    return result


class ShareTextProductLinkWorker(ProductLinkWorker):
    """Product URL worker that keeps the title embedded in the user's share text."""

    def __init__(
        self,
        records: list[dict],
        output_parent: Path,
        *,
        concurrency: int = 1,
        retries: int = 2,
        parent=None,
    ):
        # Do not call ProductLinkWorker.__init__ because it intentionally flattens
        # dict inputs to URL strings. We need to preserve title_hint.
        super(ProductLinkWorker, self).__init__(parent)
        self.records = list(records)
        self.output_parent = Path(output_parent)
        self.concurrency = 1
        self.retries = max(0, int(retries))
        self.stop_event = threading.Event()

    def request_stop(self):
        self.stop_event.set()

    def run(self):
        from playwright.sync_api import sync_playwright

        stamp = time.strftime("%Y%m%d_%H%M%S")
        output_dir = self.output_parent / f"商品URL解析_{stamp}"
        image_dir = output_dir / "首图"
        debug_dir = output_dir / "解析诊断"
        output_dir.mkdir(parents=True, exist_ok=True)
        image_dir.mkdir(parents=True, exist_ok=True)

        rows = []
        errors = []
        total = len(self.records)

        self.info.emit(
            "正在启动 Edge 商品解析浏览器。分享文案中的商品标题会直接作为标题来源；"
            "Edge 只负责补充首图URL和SKU。"
        )

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(_profile_dir()),
                    channel="msedge",
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--disable-notifications",
                    ],
                )

                try:
                    for idx, record in enumerate(self.records):
                        if self.stop_event.is_set():
                            break

                        url = str(record.get("url") or "").strip()
                        title_hint = _clean(record.get("title_hint") or "")
                        self.info.emit(f"正在解析 {idx + 1}/{total}")

                        try:
                            meta = resolve_page(context, url, idx + 1, debug_dir)
                            parsed_title = _clean(meta.get("title", ""))
                            image_url = _clean(meta.get("image_url", ""))
                            skus = meta.get("sku_titles") or []

                            # For copied Douyin share text, the title embedded in the
                            # share message is authoritative and should not be discarded.
                            title = title_hint or parsed_title
                            status = []

                            if not title:
                                title = f"商品_{idx + 1:03d}"
                                status.append("未解析到商品标题")
                            if not image_url:
                                status.append("未解析到首图URL")
                            if not skus:
                                status.append("未解析到SKU标题")

                            if image_url:
                                try:
                                    filename = sanitize_filename(f"{idx + 1:03d}_{title}")
                                    _save_jpg_from_url(
                                        image_url,
                                        image_dir / f"{filename}.jpg",
                                        retries=self.retries,
                                    )
                                except Exception as exc:
                                    status.append(str(exc))

                            row = {
                                "source_url": url,
                                "title": title,
                                "image_url": image_url,
                                "sku_titles": skus,
                                "status": "完成" if not status else "；".join(status),
                            }

                        except Exception as exc:
                            # Even if browser parsing fails, keep the title we already
                            # extracted from the user's share text.
                            if _is_closed_error(exc):
                                msg = "Edge 页面被关闭，首图/SKU未完成解析"
                            else:
                                msg = f"解析失败：{exc}"

                            row = {
                                "source_url": url,
                                "title": title_hint or f"商品_{idx + 1:03d}",
                                "image_url": "",
                                "sku_titles": [],
                                "status": msg,
                            }
                            errors.append(f"{idx + 1}: {msg}")

                        rows.append(row)
                        self.item_result.emit(idx, row)
                        self.progress.emit(idx + 1, total)

                finally:
                    try:
                        context.close()
                    except Exception:
                        pass

        except Exception as exc:
            errors.append(f"Edge启动失败：{exc}")

        excel_path = output_dir / "商品解析结果.xlsx"
        try:
            self._save_excel(rows, excel_path)
        except Exception as exc:
            errors.append(f"Excel导出失败：{exc}")

        self.finished_summary.emit(
            {
                "count": len(rows),
                "success": sum(1 for row in rows if row.get("image_url")),
                "failed": sum(1 for row in rows if not row.get("image_url")),
                "stopped": self.stop_event.is_set(),
                "errors": errors,
                "dest_dir": str(output_dir),
                "output_dir": str(output_dir),
                "excel_path": str(excel_path),
                "image_dir": str(image_dir),
                "debug_dir": str(debug_dir),
            }
        )


class MainWindow(V240MainWindow):
    """V2.6.4: preserve full share text and extract title hints before URL parsing."""

    def _import_excel(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "上传商品链接 Excel", "", "Excel (*.xlsx *.xlsm)"
        )
        if not path:
            return

        try:
            wb = load_workbook(path, data_only=True, read_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                raise RuntimeError("Excel 为空")

            headers = [str(x or "").strip() for x in rows[0]]
            preferred = (
                "商品链接",
                "商品URL",
                "产品链接",
                "分享链接",
                "分享文案",
                "URL",
                "url",
                "链接",
            )

            url_idx = -1
            for name in preferred:
                for i, header in enumerate(headers):
                    if header == name or name.lower() == header.lower():
                        url_idx = i
                        break
                if url_idx >= 0:
                    break

            values_with_links = []
            data_rows = rows[1:] if url_idx >= 0 else rows

            for row in data_rows:
                values = list(row)
                candidates = []

                if 0 <= url_idx < len(values):
                    candidates.append(str(values[url_idx] or ""))
                else:
                    candidates.extend(str(v or "") for v in values)

                for value in candidates:
                    if re.search(r"https?://[^\s，。；;]+", value):
                        # Preserve the complete share text instead of stripping it
                        # down to a bare URL. This keeps the copied product title.
                        values_with_links.append(value.strip())
                        break

            self.link_input.setPlainText("\n\n".join(values_with_links))
            records = _extract_records(self.link_input.toPlainText())
            self.info_label.setText(
                f"已从 Excel 导入 {len(records)} 个商品链接，分享文案中的标题会直接保留"
            )

        except Exception as exc:
            QMessageBox.critical(self, "Excel 导入失败", str(exc))

    def _parse_links(self) -> list[dict]:
        return _extract_records(self.link_input.toPlainText())

    def _start(self):
        records = self._parse_links()

        if not records:
            QMessageBox.information(
                self,
                "没有商品链接",
                "请粘贴商品分享文案/链接，或上传包含“商品链接 / 分享文案 / URL / 链接”列的 Excel。",
            )
            return

        if self.link_worker and self.link_worker.isRunning():
            return

        output = Path(
            self.output_dir.text().strip() or self.root / "URL下载"
        )
        output.mkdir(parents=True, exist_ok=True)

        self.tool_settings.setValue("product_url_output_dir", str(output))
        self.tool_settings.setValue("product_url_retries", self.retries.value())

        self.result_table.setRowCount(len(records))

        for row, record in enumerate(records):
            self.result_table.setItem(
                row, 0, QTableWidgetItem(record.get("url", ""))
            )
            self.result_table.setItem(
                row, 1, QTableWidgetItem(record.get("title_hint", ""))
            )
            self.result_table.setItem(row, 2, QTableWidgetItem(""))
            self.result_table.setItem(row, 3, QTableWidgetItem(""))
            self.result_table.setItem(row, 4, QTableWidgetItem("等待"))

        self.progress.setValue(0)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.info_label.setText(
            f"正在处理 {len(records)} 个商品链接；已先从分享文案提取商品标题"
        )

        worker = ShareTextProductLinkWorker(
            records,
            output,
            concurrency=1,
            retries=self.retries.value(),
            parent=self,
        )
        self.link_worker = worker
        worker.item_result.connect(self._item_result)
        worker.info.connect(self.info_label.setText)
        worker.progress.connect(self._progress)
        worker.finished_summary.connect(self._finished)
        worker.start()
