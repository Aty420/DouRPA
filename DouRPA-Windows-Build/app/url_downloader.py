from __future__ import annotations

import html
import json
import mimetypes
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from PySide6.QtCore import QThread, Signal


_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}


def sanitize_filename(name: str, fallback: str = "image") -> str:
    name = _INVALID_FILENAME.sub("_", str(name or "")).strip(" ._")
    name = re.sub(r"\s+", " ", name)
    return (name[:120] or fallback).strip()


def _clean_text(value: str) -> str:
    value = html.unescape(str(value or ""))
    value = value.replace("\\u002F", "/").replace("\\u002f", "/")
    value = value.replace("\\u0026", "&").replace("\\/", "/")
    value = value.replace("\\n", " ").replace("\\t", " ")
    return re.sub(r"\s+", " ", value).strip(" \"'\\,")


def _content_ext(url: str, content_type: str = "") -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in _IMAGE_EXTS:
        return suffix
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    guessed = mimetypes.guess_extension(ctype) if ctype else None
    if guessed == ".jpe":
        guessed = ".jpg"
    return guessed or ".jpg"


def download_one(
    url: str,
    dest_dir: Path,
    stem: str,
    *,
    retries: int = 2,
    timeout: int = 30,
    stop_event: threading.Event | None = None,
    skip_existing: bool = True,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_filename(stem)
    last_exc: Exception | None = None

    for attempt in range(max(0, int(retries)) + 1):
        if stop_event and stop_event.is_set():
            raise RuntimeError("处理已停止")
        try:
            parsed = urllib.parse.urlparse(url)
            referer = urllib.parse.urlunparse(
                (parsed.scheme or "https", parsed.netloc, "/", "", "", "")
            )
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/138.0 Safari/537.36"
                    ),
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": referer,
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type", "")
            if not data:
                raise RuntimeError("图片响应为空")
            ext = _content_ext(url, content_type)
            target = dest_dir / f"{stem}{ext}"
            if target.exists() and skip_existing:
                return target
            if target.exists():
                n = 2
                while (dest_dir / f"{stem}_{n:02d}{ext}").exists():
                    n += 1
                target = dest_dir / f"{stem}_{n:02d}{ext}"
            target.write_bytes(data)
            return target
        except Exception as exc:
            last_exc = exc
            if attempt >= max(0, int(retries)):
                break
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(str(last_exc or "下载失败"))


def _fetch_html(url: str, timeout: int = 25) -> tuple[str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0 Mobile Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="ignore"), final_url
    except Exception:
        return raw.decode("utf-8", errors="ignore"), final_url


def _meta(raw: str, key: str, *, attr: str = "property") -> str:
    patterns = (
        rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr}=["\']{re.escape(key)}["\']',
    )
    for pattern in patterns:
        m = re.search(pattern, raw, flags=re.I | re.S)
        if m:
            return _clean_text(m.group(1))
    return ""


def _json_ld_products(raw: str) -> list[dict]:
    results = []
    for body in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        raw,
        flags=re.I | re.S,
    ):
        body = html.unescape(body).strip()
        try:
            value = json.loads(body)
        except Exception:
            continue
        stack = value if isinstance(value, list) else [value]
        for obj in stack:
            if isinstance(obj, dict):
                if str(obj.get("@type", "")).lower() == "product":
                    results.append(obj)
                graph = obj.get("@graph")
                if isinstance(graph, list):
                    results.extend(
                        x for x in graph
                        if isinstance(x, dict)
                        and str(x.get("@type", "")).lower() == "product"
                    )
    return results


def _first_http(value) -> str:
    if isinstance(value, str):
        value = _clean_text(value)
        return value if value.startswith(("http://", "https://")) else ""
    if isinstance(value, list):
        for item in value:
            found = _first_http(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("url", "src", "image", "imageUrl", "url_list"):
            if key in value:
                found = _first_http(value[key])
                if found:
                    return found
    return ""


def _extract_title(raw: str) -> str:
    for candidate in (
        _meta(raw, "og:title"),
        _meta(raw, "twitter:title", attr="name"),
    ):
        if candidate and candidate not in {"抖音", "抖音商城", "淘宝", "天猫"}:
            return candidate

    for product in _json_ld_products(raw):
        name = _clean_text(product.get("name", ""))
        if name:
            return name

    m = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.I | re.S)
    if m:
        title = _clean_text(re.sub(r"<[^>]+>", "", m.group(1)))
        title = re.sub(r"[-_|]\s*(抖音|淘宝|天猫).*$", "", title).strip()
        if title and title not in {"抖音", "淘宝", "天猫"}:
            return title

    patterns = (
        r'"product_title"\s*:\s*"([^"]{4,220})"',
        r'"productTitle"\s*:\s*"([^"]{4,220})"',
        r'"goods_title"\s*:\s*"([^"]{4,220})"',
        r'"goodsTitle"\s*:\s*"([^"]{4,220})"',
        r'"itemTitle"\s*:\s*"([^"]{4,220})"',
    )
    for pattern in patterns:
        m = re.search(pattern, raw, flags=re.I | re.S)
        if m:
            return _clean_text(m.group(1))
    return ""


def _extract_image(raw: str) -> str:
    for candidate in (
        _meta(raw, "og:image"),
        _meta(raw, "twitter:image", attr="name"),
    ):
        if candidate.startswith(("http://", "https://")):
            return candidate

    for product in _json_ld_products(raw):
        found = _first_http(product.get("image"))
        if found:
            return found

    patterns = (
        r'"url_list"\s*:\s*\[\s*"([^"]+)"',
        r'"origin_img"\s*:\s*"([^"]+)"',
        r'"originImg"\s*:\s*"([^"]+)"',
        r'"main_image"\s*:\s*"([^"]+)"',
        r'"mainImage"\s*:\s*"([^"]+)"',
        r'"image_url"\s*:\s*"([^"]+)"',
        r'"imageUrl"\s*:\s*"([^"]+)"',
        r'"pic_url"\s*:\s*"([^"]+)"',
        r'"picUrl"\s*:\s*"([^"]+)"',
        r'"cover"\s*:\s*\{[^{}]{0,1500}?"url"\s*:\s*"([^"]+)"',
    )
    for pattern in patterns:
        m = re.search(pattern, raw, flags=re.I | re.S)
        if m:
            candidate = _clean_text(m.group(1))
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            if candidate.startswith(("http://", "https://")):
                return candidate

    for candidate in re.findall(
        r'https?:\\?/\\?/[^"\'<> ]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\'<> ]*)?',
        raw,
        flags=re.I,
    ):
        candidate = _clean_text(candidate)
        if candidate.startswith(("http://", "https://")):
            return candidate
    return ""


def _valid_sku_text(value: str) -> bool:
    value = _clean_text(value)
    if not (3 <= len(value) <= 180):
        return False
    if value.startswith(("http://", "https://", "¥", "￥")):
        return False
    if re.fullmatch(r"[\d\s.,/%+\-*]+", value):
        return False
    bad = {
        "包装规格", "选择规格", "规格", "商品", "默认", "购买数量",
        "颜色分类", "尺寸", "款式", "套餐", "数量",
    }
    return value not in bad


def _extract_skus(raw: str) -> list[str]:
    result: list[str] = []

    patterns = (
        r'"sku_name"\s*:\s*"([^"]{3,220})"',
        r'"skuName"\s*:\s*"([^"]{3,220})"',
        r'"sku_title"\s*:\s*"([^"]{3,220})"',
        r'"skuTitle"\s*:\s*"([^"]{3,220})"',
        r'"spec_value_name"\s*:\s*"([^"]{3,220})"',
        r'"specValueName"\s*:\s*"([^"]{3,220})"',
        r'"spec_desc"\s*:\s*"([^"]{3,220})"',
        r'"specDesc"\s*:\s*"([^"]{3,220})"',
        r'"combination_text"\s*:\s*"([^"]{3,220})"',
        r'"combinationText"\s*:\s*"([^"]{3,220})"',
    )
    for pattern in patterns:
        for value in re.findall(pattern, raw, flags=re.I | re.S):
            value = _clean_text(value)
            if _valid_sku_text(value) and value not in result:
                result.append(value)

    # Some share pages serialize SKU rows as title/name fields inside an sku list.
    sku_blocks = re.findall(
        r'"(?:sku_list|skuList|skus)"\s*:\s*\[(.{0,250000}?)\]',
        raw,
        flags=re.I | re.S,
    )
    for block in sku_blocks[:4]:
        for pattern in (
            r'"title"\s*:\s*"([^"]{3,220})"',
            r'"name"\s*:\s*"([^"]{3,220})"',
            r'"desc"\s*:\s*"([^"]{3,220})"',
        ):
            for value in re.findall(pattern, block, flags=re.I | re.S):
                value = _clean_text(value)
                if _valid_sku_text(value) and value not in result:
                    result.append(value)

    return result[:80]


def _extract_visible_skus(text: str) -> list[str]:
    """Fallback for rendered pages after clicking 规格/包装规格."""
    lines = [_clean_text(x) for x in str(text or "").splitlines()]
    lines = [x for x in lines if x]
    start = -1
    for i, line in enumerate(lines):
        if "包装规格" in line or line in {"选择规格", "选规格"}:
            start = i
            break
    if start < 0:
        return []

    result = []
    stop_words = ("立即购买", "加入购物车", "客服", "配送", "服务保障")
    for line in lines[start + 1 : start + 45]:
        if any(w in line for w in stop_words):
            if result:
                break
            continue
        if any(w in line for w in ("万人购买", "人购买", "已售", "大图")):
            continue
        if line.startswith(("¥", "￥")) or re.fullmatch(r"[\d\s.,/%+\-*]+", line):
            continue
        if _valid_sku_text(line) and line not in result:
            result.append(line)
    return result[:40]


def _resolve_with_playwright(url: str) -> dict:
    """Headless Edge fallback for dynamic product pages.

    This does not search products and does not use MuMu. It only opens the URL
    supplied by the user and tries to read the rendered public product page.
    """
    result = {"title": "", "image_url": "", "sku_titles": []}
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge",
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0 Safari/537.36"
                ),
                viewport={"width": 1365, "height": 900},
            )
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1800)

            raw = page.content()
            result["title"] = _extract_title(raw)
            result["image_url"] = _extract_image(raw)
            result["sku_titles"] = _extract_skus(raw)

            if not result["sku_titles"]:
                for label in ("包装规格", "选择规格", "选规格", "规格"):
                    try:
                        loc = page.get_by_text(label, exact=False).first
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=2500)
                            page.wait_for_timeout(700)
                            break
                    except Exception:
                        continue
                try:
                    visible = page.locator("body").inner_text(timeout=3000)
                    result["sku_titles"] = _extract_visible_skus(visible)
                except Exception:
                    pass

            browser.close()
    except Exception:
        pass
    return result


def resolve_product_metadata(url: str) -> dict:
    """Resolve one user-supplied product link.

    Output fields intentionally match the user's required Excel columns:
    title / image_url / sku_titles.
    """
    parsed = urllib.parse.urlparse(url)
    if Path(parsed.path).suffix.lower() in _IMAGE_EXTS:
        return {
            "title": "",
            "image_url": url,
            "sku_titles": [],
            "final_url": url,
        }

    raw = ""
    final_url = url
    try:
        raw, final_url = _fetch_html(url)
    except Exception:
        raw = ""

    result = {
        "title": _extract_title(raw) if raw else "",
        "image_url": _extract_image(raw) if raw else "",
        "sku_titles": _extract_skus(raw) if raw else [],
        "final_url": final_url,
    }

    if not result["title"] or not result["image_url"] or not result["sku_titles"]:
        rendered = _resolve_with_playwright(final_url or url)
        if not result["title"]:
            result["title"] = rendered.get("title", "")
        if not result["image_url"]:
            result["image_url"] = rendered.get("image_url", "")
        if not result["sku_titles"]:
            result["sku_titles"] = rendered.get("sku_titles") or []

    return result


class ProductLinkWorker(QThread):
    item_result = Signal(int, object)
    info = Signal(str)
    progress = Signal(int, int)
    finished_summary = Signal(object)

    def __init__(
        self,
        urls: list[str],
        output_parent: Path,
        *,
        concurrency: int = 3,
        retries: int = 2,
        parent=None,
    ):
        super().__init__(parent)
        self.urls = list(urls)
        self.output_parent = Path(output_parent)
        self.concurrency = max(1, min(6, int(concurrency)))
        self.retries = max(0, int(retries))
        self.stop_event = threading.Event()

    def request_stop(self):
        self.stop_event.set()

    def _process_one(self, idx: int, url: str, image_dir: Path) -> dict:
        if self.stop_event.is_set():
            raise RuntimeError("处理已停止")

        metadata = resolve_product_metadata(url)
        title = _clean_text(metadata.get("title", ""))
        image_url = _clean_text(metadata.get("image_url", ""))
        sku_titles = metadata.get("sku_titles") or []

        status_parts = []
        if not title:
            title = f"商品_{idx + 1:03d}"
            status_parts.append("未解析到商品标题")
        if not image_url:
            status_parts.append("未解析到首图URL")
        if not sku_titles:
            status_parts.append("未解析到SKU标题")

        image_path = ""
        if image_url:
            try:
                path = download_one(
                    image_url,
                    image_dir,
                    f"{idx + 1:03d}_{title}",
                    retries=self.retries,
                    stop_event=self.stop_event,
                    skip_existing=False,
                )
                image_path = str(path)
            except Exception as exc:
                status_parts.append(f"首图下载失败：{exc}")

        return {
            "source_url": url,
            "title": title,
            "image_url": image_url,
            "sku_titles": sku_titles,
            "image_path": image_path,
            "status": "完成" if not status_parts else "；".join(status_parts),
        }

    @staticmethod
    def _save_excel(rows: list[dict], path: Path):
        wb = Workbook()
        ws = wb.active
        ws.title = "商品解析结果"

        # Strictly only the three fields requested by the user.
        ws.append(["商品标题", "首图URL", "SKU标题"])
        for row in rows:
            ws.append([
                row.get("title", ""),
                row.get("image_url", ""),
                "；".join(row.get("sku_titles") or []),
            ])

        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="5B6FF5")
            cell.alignment = Alignment(horizontal="center")

        ws.column_dimensions["A"].width = 65
        ws.column_dimensions["B"].width = 85
        ws.column_dimensions["C"].width = 100
        ws.freeze_panes = "A2"
        path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)

    def run(self):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        output_dir = self.output_parent / f"商品URL解析_{stamp}"
        image_dir = output_dir / "首图"
        output_dir.mkdir(parents=True, exist_ok=True)
        image_dir.mkdir(parents=True, exist_ok=True)

        total = len(self.urls)
        rows_by_index: dict[int, dict] = {}
        errors: list[str] = []
        done = 0

        self.info.emit(f"开始解析 {total} 个商品链接")

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = {
                executor.submit(self._process_one, idx, url, image_dir): (idx, url)
                for idx, url in enumerate(self.urls)
            }

            for future in as_completed(futures):
                idx, url = futures[future]
                if self.stop_event.is_set():
                    break
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        "source_url": url,
                        "title": f"商品_{idx + 1:03d}",
                        "image_url": "",
                        "sku_titles": [],
                        "image_path": "",
                        "status": f"失败：{exc}",
                    }
                    errors.append(f"{idx + 1}: {exc}")

                rows_by_index[idx] = row
                done += 1
                self.item_result.emit(idx, row)
                self.progress.emit(done, total)
                self.info.emit(f"已处理 {done}/{total}")

            if self.stop_event.is_set():
                for future in futures:
                    future.cancel()

        rows = [rows_by_index[i] for i in sorted(rows_by_index)]
        excel_path = output_dir / "商品解析结果.xlsx"
        try:
            self._save_excel(rows, excel_path)
        except Exception as exc:
            errors.append(f"Excel导出失败：{exc}")

        success = sum(1 for row in rows if row.get("image_url"))
        self.finished_summary.emit(
            {
                "count": len(rows),
                "success": success,
                "failed": max(0, len(rows) - success),
                "stopped": self.stop_event.is_set(),
                "errors": errors,
                "output_dir": str(output_dir),
                "excel_path": str(excel_path),
                "image_dir": str(image_dir),
            }
        )
