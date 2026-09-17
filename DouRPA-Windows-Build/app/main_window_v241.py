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


_SUPPORTED_PRODUCT_HOSTS = (
    "v.douyin.com",
    "www.douyin.com",
    "douyin.com",
    "haohuo.jinritemai.com",
    "jinritemai.com",
    "m.tb.cn",
    "item.taobao.com",
    "taobao.com",
    "detail.tmall.com",
    "tmall.com",
)

_BLOCKED_ASSET_SUFFIXES = (
    ".zip", ".js", ".css", ".json", ".wasm", ".bin",
    ".apk", ".exe", ".dll", ".woff", ".woff2", ".ttf",
)


def _is_supported_product_url(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(str(url or "").strip())
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
    except Exception:
        return False

    if not host:
        return False

    if path.endswith(_BLOCKED_ASSET_SUFFIXES):
        return False

    if any(bad in host for bad in (
        "static", "cdn", "verifycenter", "rc-verify", "byteimg", "snssdk"
    )):
        return False

    return any(
        host == allowed or host.endswith("." + allowed)
        for allowed in _SUPPORTED_PRODUCT_HOSTS
    )


def _url_score(url: str) -> int:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
    except Exception:
        return -999

    score = 0
    if host == "v.douyin.com":
        score += 100
    elif host == "m.tb.cn":
        score += 95
    elif "item.taobao.com" in host:
        score += 90
    elif "detail.tmall.com" in host:
        score += 90
    elif "douyin.com" in host:
        score += 80
    elif "jinritemai.com" in host:
        score += 75

    if any(k in path for k in ("/item", "/product", "/detail", "/goods")):
        score += 20

    if path.endswith(_BLOCKED_ASSET_SUFFIXES):
        score -= 200

    return score


def _extract_records(text: str) -> list[dict]:
    text = str(text or "")
    all_matches = list(re.finditer(r"https?://[^\s，。；;]+", text))

    candidates = []
    for match in all_matches:
        url = match.group(0).rstrip(")】]}>\"'")
        if _is_supported_product_url(url):
            candidates.append((match, url))

    if not candidates:
        return []

    result = []
    seen = set()

    for i, (match, url) in enumerate(candidates):
        if url in seen:
            continue

        end = candidates[i + 1][0].start() if i + 1 < len(candidates) else len(text)
        tail = text[match.end():end]

        tail = re.split(
            r"(?:\r?\n|长按复制此条消息|打开抖音搜索|查看商品详情)",
            tail,
            maxsplit=1,
        )[0]
        title_hint = tail.strip(" \t\r\n：:，,。；;")
        title_hint = re.sub(
            r"^(?:【抖音商城】|【淘宝】|【天猫】)\s*",
            "",
            title_hint,
        ).strip()

        if not title_hint or title_hint.startswith(("http://", "https://")):
            title_hint = ""

        seen.add(url)
        result.append(
            {
                "url": url,
                "title_hint": title_hint,
                "raw_text": text[match.start():end].strip(),
                "_score": _url_score(url),
            }
        )

    result.sort(key=lambda x: x.get("_score", 0), reverse=True)
    for item in result:
        item.pop("_score", None)
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
            "正在后台静默解析商品。不会弹出 Edge 窗口；分享文案中的商品标题直接保留，"
            "后台浏览器仅负责补充首图URL和SKU。"
        )

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(_profile_dir()),
                    channel="msedge",
                    headless=True,
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
                    records_in_value = _extract_records(value)
                    if records_in_value:
                        # Preserve the complete share text. Arbitrary CDN/static URLs
                        # are ignored by _extract_records().
                        values_with_links.append(value.strip())
                        break

            self.link_input.setPlainText("\n\n".join(values_with_links))
            records = _extract_records(self.link_input.toPlainText())
            preview = records[0]["url"] if records else "未识别到有效商品链接"
            self.info_label.setText(
                f"已从 Excel 导入 {len(records)} 个商品链接 · 首条：{preview}"
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
