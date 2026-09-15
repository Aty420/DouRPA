from __future__ import annotations

import html
import io
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from PIL import Image
from PySide6.QtCore import QThread, Signal


_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}


def sanitize_filename(name: str, fallback: str = "image") -> str:
    name = _INVALID_FILENAME.sub("_", str(name or "")).strip(" ._")
    name = re.sub(r"\s+", " ", name)
    return (name[:120] or fallback).strip()


def _clean_text(value) -> str:
    value = html.unescape(str(value or ""))
    value = value.replace("\\u002F", "/").replace("\\u002f", "/")
    value = value.replace("\\u0026", "&").replace("\\/", "/")
    value = value.replace("\\n", " ").replace("\\t", " ")
    try:
        if "\\u" in value:
            value = bytes(value, "utf-8").decode("unicode_escape", errors="ignore")
    except Exception:
        pass
    return re.sub(r"\s+", " ", value).strip(" \"'\\,")


def _save_jpeg(data: bytes, target: Path, quality: int = 95):
    """Always write a real JPEG file, regardless of source image format."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(io.BytesIO(data)) as img:
            if getattr(img, "n_frames", 1) > 1:
                img.seek(0)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")
            img.save(target, format="JPEG", quality=quality, optimize=True)
            return
    except Exception as exc:
        # Last-resort: source itself may already be valid JPEG.
        if data[:2] == b"\xff\xd8":
            target.write_bytes(data)
            return
        raise RuntimeError(f"图片转 JPG 失败：{exc}") from exc


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
    target = dest_dir / f"{stem}.jpg"

    if target.exists() and skip_existing:
        return target

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

            if not data:
                raise RuntimeError("图片响应为空")

            if target.exists() and not skip_existing:
                n = 2
                while (dest_dir / f"{stem}_{n:02d}.jpg").exists():
                    n += 1
                target = dest_dir / f"{stem}_{n:02d}.jpg"

            _save_jpeg(data, target)
            return target
        except Exception as exc:
            last_exc = exc
            if attempt >= max(0, int(retries)):
                break
            time.sleep(0.6 * (attempt + 1))

    raise RuntimeError(str(last_exc or "下载失败"))


def _fetch_html(url: str, timeout: int = 25, mobile: bool = True) -> tuple[str, str]:
    ua = (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0 Mobile Safari/537.36"
        if mobile
        else
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0 Safari/537.36"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="ignore"), final_url


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


def _safe_json_loads(text: str):
    if not text:
        return None
    text = html.unescape(text).strip()
    candidates = [text]
    try:
        unquoted = urllib.parse.unquote(text)
        if unquoted != text:
            candidates.append(unquoted)
    except Exception:
        pass

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except Exception:
            pass
    return None


def _script_json_payloads(raw: str) -> list[object]:
    payloads: list[object] = []

    # Douyin/ByteDance pages commonly place product data inside URI-encoded RENDER_DATA.
    for sid in ("RENDER_DATA", "__NEXT_DATA__", "SIGI_STATE"):
        pattern = rf'<script[^>]+id=["\']{re.escape(sid)}["\'][^>]*>(.*?)</script>'
        for body in re.findall(pattern, raw, flags=re.I | re.S):
            value = _safe_json_loads(body)
            if value is not None:
                payloads.append(value)

    # Standard JSON / JSON-LD script blocks.
    for body in re.findall(
        r'<script[^>]+type=["\'](?:application/ld\+json|application/json)["\'][^>]*>(.*?)</script>',
        raw,
        flags=re.I | re.S,
    ):
        value = _safe_json_loads(body)
        if value is not None:
            payloads.append(value)

    return payloads


def _walk(obj, path=()):
    if isinstance(obj, dict):
        for key, value in obj.items():
            current = path + (str(key),)
            yield current, value
            yield from _walk(value, current)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            current = path + (str(idx),)
            yield current, value
            yield from _walk(value, current)


def _first_http(value) -> str:
    if isinstance(value, str):
        value = _clean_text(value)
        if value.startswith("//"):
            value = "https:" + value
        return value if value.startswith(("http://", "https://")) else ""
    if isinstance(value, list):
        for item in value:
            found = _first_http(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("url", "src", "image", "imageUrl", "url_list", "urls"):
            if key in value:
                found = _first_http(value[key])
                if found:
                    return found
    return ""


def _looks_like_product_title(text: str) -> bool:
    text = _clean_text(text)
    if not (5 <= len(text) <= 220):
        return False
    if text.startswith(("http://", "https://", "¥", "￥")):
        return False
    if text in {"抖音", "抖音商城", "淘宝", "天猫", "商品详情"}:
        return False
    if re.fullmatch(r"[\d\s.,/%+\-*]+", text):
        return False
    return True


_TITLE_KEYS = {
    "title", "producttitle", "product_title", "productname", "product_name",
    "goodstitle", "goods_title", "goodsname", "goods_name",
    "itemtitle", "item_title", "itemname", "item_name",
    "shorttitle", "short_title", "name",
}

_IMAGE_KEYS = {
    "image", "images", "imageurl", "image_url", "mainimage", "main_image",
    "mainpic", "main_pic", "picurl", "pic_url", "cover", "coverurl", "cover_url",
    "originimg", "origin_img", "url_list",
}

_SKU_DIRECT_KEYS = {
    "skuname", "sku_name", "skutitle", "sku_title",
    "specdesc", "spec_desc", "specification", "specificationname",
    "specification_name", "combinationtext", "combination_text",
    "specvaluename", "spec_value_name", "specvalue", "spec_value",
    "sellpropertyname", "sell_property_name", "propertyvalue", "property_value",
}

_SKU_CONTEXT_HINTS = (
    "sku", "spec", "规格", "saleprop", "sale_prop", "property",
    "productsku", "product_sku", "skuinfo", "sku_info",
)


def _extract_title_from_payloads(payloads: list[object]) -> str:
    scored = []
    for payload in payloads:
        for path, value in _walk(payload):
            if not isinstance(value, str):
                continue
            key = path[-1].replace("-", "_").lower() if path else ""
            norm_key = key.replace("_", "")
            if key in _TITLE_KEYS or norm_key in {x.replace("_", "") for x in _TITLE_KEYS}:
                text = _clean_text(value)
                if not _looks_like_product_title(text):
                    continue
                score = 10
                joined = "/".join(x.lower() for x in path)
                if any(h in joined for h in ("product", "goods", "item", "商品")):
                    score += 20
                if key != "name":
                    score += 15
                if any(ch in text for ch in ("抽", "提", "包", "箱", "卷", "片", "装", "纸", "巾")):
                    score += 10
                scored.append((score, len(text), text))
    if scored:
        scored.sort(reverse=True)
        return scored[0][2]
    return ""


def _extract_image_from_payloads(payloads: list[object]) -> str:
    for payload in payloads:
        for path, value in _walk(payload):
            if not path:
                continue
            key = path[-1].replace("-", "_").lower()
            norm_key = key.replace("_", "")
            if key in _IMAGE_KEYS or norm_key in {x.replace("_", "") for x in _IMAGE_KEYS}:
                found = _first_http(value)
                if found:
                    return found
    return ""


def _valid_sku_text(text: str) -> bool:
    text = _clean_text(text)
    if not (3 <= len(text) <= 180):
        return False
    if text.startswith(("http://", "https://", "¥", "￥")):
        return False
    if re.fullmatch(r"[\d\s.,/%+\-*]+", text):
        return False
    if text in {
        "包装规格", "选择规格", "选规格", "规格", "商品",
        "默认", "购买数量", "颜色分类", "尺寸", "款式", "套餐", "数量",
        "配送", "服务",
    }:
        return False
    return True


def _extract_skus_from_payloads(payloads: list[object]) -> list[str]:
    result: list[str] = []
    direct_norm = {x.replace("_", "") for x in _SKU_DIRECT_KEYS}

    for payload in payloads:
        for path, value in _walk(payload):
            if not isinstance(value, str) or not path:
                continue

            text = _clean_text(value)
            if not _valid_sku_text(text):
                continue

            key = path[-1].replace("-", "_").lower()
            norm_key = key.replace("_", "")
            joined = "/".join(x.lower().replace("-", "_") for x in path)

            direct = key in _SKU_DIRECT_KEYS or norm_key in direct_norm
            contextual = (
                any(h in joined for h in _SKU_CONTEXT_HINTS)
                and key in {"name", "title", "desc", "value", "text", "label"}
            )

            if (direct or contextual) and text not in result:
                result.append(text)

    return result[:100]


def _extract_title(raw: str, payloads: list[object]) -> str:
    for candidate in (
        _meta(raw, "og:title"),
        _meta(raw, "twitter:title", attr="name"),
    ):
        if _looks_like_product_title(candidate):
            return candidate

    title = _extract_title_from_payloads(payloads)
    if title:
        return title

    m = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.I | re.S)
    if m:
        title = _clean_text(re.sub(r"<[^>]+>", "", m.group(1)))
        title = re.sub(r"[-_|]\s*(抖音|淘宝|天猫).*$", "", title).strip()
        if _looks_like_product_title(title):
            return title

    for pattern in (
        r'"product_title"\s*:\s*"([^"]{4,220})"',
        r'"productTitle"\s*:\s*"([^"]{4,220})"',
        r'"goods_title"\s*:\s*"([^"]{4,220})"',
        r'"goodsTitle"\s*:\s*"([^"]{4,220})"',
        r'"itemTitle"\s*:\s*"([^"]{4,220})"',
        r'"item_name"\s*:\s*"([^"]{4,220})"',
    ):
        m = re.search(pattern, raw, flags=re.I | re.S)
        if m:
            title = _clean_text(m.group(1))
            if _looks_like_product_title(title):
                return title
    return ""


def _extract_image(raw: str, payloads: list[object]) -> str:
    for candidate in (
        _meta(raw, "og:image"),
        _meta(raw, "twitter:image", attr="name"),
    ):
        if candidate.startswith(("http://", "https://")):
            return candidate

    image = _extract_image_from_payloads(payloads)
    if image:
        return image

    for pattern in (
        r'"url_list"\s*:\s*\[\s*"([^"]+)"',
        r'"origin_img"\s*:\s*"([^"]+)"',
        r'"originImg"\s*:\s*"([^"]+)"',
        r'"main_image"\s*:\s*"([^"]+)"',
        r'"mainImage"\s*:\s*"([^"]+)"',
        r'"image_url"\s*:\s*"([^"]+)"',
        r'"imageUrl"\s*:\s*"([^"]+)"',
        r'"pic_url"\s*:\s*"([^"]+)"',
        r'"picUrl"\s*:\s*"([^"]+)"',
        r'"cover"\s*:\s*\{[^{}]{0,1800}?"url"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pattern, raw, flags=re.I | re.S)
        if m:
            candidate = _clean_text(m.group(1))
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            if candidate.startswith(("http://", "https://")):
                return candidate
    return ""


def _extract_skus(raw: str, payloads: list[object]) -> list[str]:
    result = _extract_skus_from_payloads(payloads)

    for pattern in (
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
    ):
        for value in re.findall(pattern, raw, flags=re.I | re.S):
            value = _clean_text(value)
            if _valid_sku_text(value) and value not in result:
                result.append(value)

    return result[:100]


def _extract_visible_skus(text: str) -> list[str]:
    lines = [_clean_text(x) for x in str(text or "").splitlines()]
    lines = [x for x in lines if x]
    result: list[str] = []

    start_indexes = [
        i for i, line in enumerate(lines)
        if "包装规格" in line or line in {"选择规格", "选规格", "规格"}
    ]

    for start in start_indexes[:3]:
        for line in lines[start + 1 : start + 60]:
            if any(w in line for w in ("立即购买", "加入购物车", "客服", "配送", "服务保障")):
                if result:
                    break
                continue
            if any(w in line for w in ("万人购买", "人购买", "已售", "大图")):
                continue
            if line.startswith(("¥", "￥")):
                continue
            if _valid_sku_text(line) and line not in result:
                result.append(line)

    return result[:80]


def _resolve_with_playwright(url: str) -> dict:
    result = {"title": "", "image_url": "", "sku_titles": []}
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge",
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--disable-notifications",
                ],
            )
            context = browser.new_context(
                viewport={"width": 430, "height": 932},
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0 Mobile Safari/537.36"
                ),
                locale="zh-CN",
                is_mobile=True,
                has_touch=True,
                device_scale_factor=1,
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(3000)

            raw = page.content()
            payloads = _script_json_payloads(raw)

            result["title"] = _extract_title(raw, payloads)
            result["image_url"] = _extract_image(raw, payloads)
            result["sku_titles"] = _extract_skus(raw, payloads)

            if not result["sku_titles"]:
                # Try to expose the package-spec panel on rendered pages.
                for label in ("包装规格", "选择规格", "选规格", "规格"):
                    try:
                        loc = page.get_by_text(label, exact=False).first
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=3000)
                            page.wait_for_timeout(900)
                            break
                    except Exception:
                        continue

                try:
                    visible = page.locator("body").inner_text(timeout=4000)
                    result["sku_titles"] = _extract_visible_skus(visible)
                except Exception:
                    pass

            # Re-read after possible interaction.
            if not result["title"] or not result["image_url"]:
                raw2 = page.content()
                payloads2 = _script_json_payloads(raw2)
                if not result["title"]:
                    result["title"] = _extract_title(raw2, payloads2)
                if not result["image_url"]:
                    result["image_url"] = _extract_image(raw2, payloads2)

            context.close()
            browser.close()
    except Exception:
        pass

    return result


def resolve_product_metadata(url: str) -> dict:
    parsed = urllib.parse.urlparse(url)
    if Path(parsed.path).suffix.lower() in _IMAGE_EXTS:
        return {
            "title": "",
            "image_url": url,
            "sku_titles": [],
            "final_url": url,
        }

    raw_mobile = ""
    raw_desktop = ""
    final_url = url

    try:
        raw_mobile, final_url = _fetch_html(url, mobile=True)
    except Exception:
        pass

    try:
        raw_desktop, desktop_final = _fetch_html(final_url or url, mobile=False)
        if desktop_final:
            final_url = desktop_final
    except Exception:
        pass

    combined = "\n".join(x for x in (raw_mobile, raw_desktop) if x)
    payloads = _script_json_payloads(combined) if combined else []

    result = {
        "title": _extract_title(combined, payloads) if combined else "",
        "image_url": _extract_image(combined, payloads) if combined else "",
        "sku_titles": _extract_skus(combined, payloads) if combined else [],
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
        concurrency: int = 2,
        retries: int = 2,
        parent=None,
    ):
        super().__init__(parent)
        self.urls = list(urls)
        self.output_parent = Path(output_parent)
        self.concurrency = max(1, min(4, int(concurrency)))
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
