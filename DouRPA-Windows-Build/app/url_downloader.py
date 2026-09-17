from __future__ import annotations

import html
import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from PIL import Image
from PySide6.QtCore import QThread, Signal


_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_CLOSE_ERROR_HINTS = (
    "target page, context or browser has been closed",
    "page has been closed",
    "browser has been closed",
    "context has been closed",
)


def sanitize_filename(name: str, fallback: str = "image") -> str:
    name = _INVALID_FILENAME.sub("_", str(name or "")).strip(" ._")
    name = re.sub(r"\s+", " ", name)
    return (name[:120] or fallback).strip()


def _clean(value) -> str:
    value = html.unescape(str(value or ""))
    value = value.replace("\\u002F", "/").replace("\\u002f", "/")
    value = value.replace("\\u0026", "&").replace("\\/", "/")
    return re.sub(r"\s+", " ", value).strip(" \"'\\,")


def _human(text: str, lo=3, hi=220) -> bool:
    text = _clean(text)
    if not (lo <= len(text) <= hi):
        return False
    if text.startswith(("http://", "https://", "¥", "￥")):
        return False
    if re.fullmatch(r"[\d\s.,:/%+\-*#_]+", text):
        return False
    return True


def _is_closed_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(x in msg for x in _CLOSE_ERROR_HINTS)


def _profile_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "DouRPA" if base else Path.home() / "AppData" / "Local" / "DouRPA"
    path = root / "data" / "url_parser_profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_page_url(page, fallback="") -> str:
    try:
        if page and not page.is_closed():
            return page.url
    except Exception:
        pass
    return fallback


def _safe_page_title(page) -> str:
    try:
        if page and not page.is_closed():
            return _clean(page.title())
    except Exception:
        pass
    return ""


def _live_page(context, preferred=None):
    try:
        if preferred is not None and not preferred.is_closed():
            return preferred
    except Exception:
        pass

    try:
        for page in reversed(context.pages):
            try:
                if not page.is_closed():
                    return page
            except Exception:
                continue
    except Exception:
        pass
    return None


def _safe_close_page(page):
    try:
        if page is not None and not page.is_closed():
            page.close()
    except Exception:
        pass


def _resolve_http_redirect(url: str) -> str:
    """Resolve short links before browser navigation to reduce app-launch/auto-close pages."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.geturl() or url
    except Exception:
        return url


def _walk(obj, path=()):
    if isinstance(obj, dict):
        for key, value in obj.items():
            p = path + (str(key),)
            yield p, value
            yield from _walk(value, p)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            p = path + (str(i),)
            yield p, value
            yield from _walk(value, p)


def _first_url(value) -> str:
    if isinstance(value, str):
        value = _clean(value)
        if value.startswith("//"):
            value = "https:" + value
        if value.startswith(("http://", "https://")):
            return value
    elif isinstance(value, list):
        for item in value:
            found = _first_url(item)
            if found:
                return found
    elif isinstance(value, dict):
        for key in ("url", "src", "url_list", "urls", "image", "imageUrl", "uri"):
            if key in value:
                found = _first_url(value[key])
                if found:
                    return found
    return ""


_TITLE_KEYS = {
    "productname", "producttitle", "goodsname", "goodstitle",
    "itemname", "itemtitle", "product_name", "product_title",
    "goods_name", "goods_title", "item_name", "item_title",
    "shorttitle", "short_title",
}

_IMAGE_KEYS = {
    "mainimage", "mainpic", "imageurl", "picurl", "coverurl",
    "originimg", "main_image", "main_pic", "image_url",
    "pic_url", "cover_url", "origin_img", "images",
    "url_list", "image_list", "img_list",
}

_SKU_DIRECT_KEYS = {
    "skuname", "skutitle", "sku_name", "sku_title",
    "skudesc", "sku_desc",
    "specdesc", "spec_desc",
    "specvaluename", "spec_value_name",
    "specvalue", "spec_value",
    "specname", "spec_name",
    "combinationtext", "combination_text",
    "propertyvaluename", "property_value_name",
    "salepropertyname", "sale_property_name",
}

_PRODUCT_CONTEXT = ("product", "goods", "item", "commodity", "商品")
_SKU_CONTEXT = ("sku", "spec", "规格", "saleprop", "sale_prop", "product_sku")


def _metadata_from_json(payload) -> dict:
    title_candidates = []
    image_candidates = []
    skus = []

    title_norm = {x.replace("_", "") for x in _TITLE_KEYS}
    image_norm = {x.replace("_", "") for x in _IMAGE_KEYS}
    sku_norm = {x.replace("_", "") for x in _SKU_DIRECT_KEYS}

    for path, value in _walk(payload):
        if not path:
            continue

        key = path[-1].replace("-", "_").lower()
        norm = key.replace("_", "")
        joined = "/".join(x.lower().replace("-", "_") for x in path)

        if isinstance(value, str):
            text = _clean(value)

            # Strong product-title keys.
            if norm in title_norm and _human(text, 5, 220):
                score = 50
                if any(x in joined for x in _PRODUCT_CONTEXT):
                    score += 40
                if any(x in text for x in ("抽", "提", "包", "箱", "卷", "片", "装", "纸", "巾")):
                    score += 15
                title_candidates.append((score, len(text), text))

            # Generic title/name inside product context.
            elif key in {"title", "name"} and any(x in joined for x in _PRODUCT_CONTEXT):
                if _human(text, 5, 220):
                    score = 35
                    if any(x in text for x in ("抽", "提", "包", "箱", "卷", "片", "装", "纸", "巾")):
                        score += 15
                    title_candidates.append((score, len(text), text))

            # Direct SKU fields.
            if norm in sku_norm and _human(text, 3, 180):
                if text not in skus:
                    skus.append(text)

            # Generic SKU child fields inside an sku/spec subtree.
            elif any(h in joined for h in _SKU_CONTEXT):
                if key in {"name", "title", "desc", "text", "label", "value", "display_name"}:
                    if _human(text, 3, 180) and text not in skus:
                        skus.append(text)

        # Image fields.
        if norm in image_norm or (
            any(x in joined for x in _PRODUCT_CONTEXT)
            and key in {"image", "images", "pic", "pics", "cover", "url_list"}
        ):
            found = _first_url(value)
            if found:
                score = 50 if any(x in joined for x in _PRODUCT_CONTEXT) else 20
                if any(bad in found.lower() for bad in ("avatar", "logo", "icon", "qrcode", "emoji")):
                    score -= 50
                image_candidates.append((score, found))

    title = ""
    if title_candidates:
        title_candidates.sort(reverse=True)
        title = title_candidates[0][2]

    image_url = ""
    if image_candidates:
        image_candidates.sort(key=lambda x: x[0], reverse=True)
        image_url = image_candidates[0][1]

    return {
        "title": title,
        "image_url": image_url,
        "sku_titles": skus[:100],
    }


def _merge(dst: dict, src: dict):
    if not dst.get("title") and src.get("title"):
        dst["title"] = src["title"]
    if not dst.get("image_url") and src.get("image_url"):
        dst["image_url"] = src["image_url"]

    existing = dst.setdefault("sku_titles", [])
    for sku in src.get("sku_titles") or []:
        sku = _clean(sku)
        if _human(sku, 3, 180) and sku not in existing:
            existing.append(sku)


def _dom_title(page) -> str:
    if page is None:
        return ""

    try:
        meta = page.locator('meta[property="og:title"]').get_attribute("content")
        if _human(meta, 5, 220):
            return _clean(meta)
    except Exception:
        pass

    for selector in (
        "h1",
        "[class*='product-title']",
        "[class*='goods-title']",
        "[class*='item-title']",
        "[class*='title']",
        "[class*='Title']",
    ):
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 10)):
                value = _clean(loc.nth(i).inner_text(timeout=1200))
                if _human(value, 5, 220):
                    return value
        except Exception:
            pass

    value = _safe_page_title(page)
    value = re.sub(r"[-_|]\s*(抖音|抖音商城|淘宝|天猫).*$", "", value).strip()
    if _human(value, 5, 220) and value not in {"抖音", "淘宝", "天猫"}:
        return value

    return ""


def _largest_image(page) -> str:
    if page is None:
        return ""
    try:
        return page.evaluate(
            """() => {
              const bad = /(avatar|logo|icon|emoji|qrcode|qr-code|banner)/i;
              const rows = [...document.images].map((img, idx) => ({
                src: img.currentSrc || img.src || '',
                w: img.naturalWidth || 0,
                h: img.naturalHeight || 0,
                area: (img.naturalWidth || 0) * (img.naturalHeight || 0),
                idx
              })).filter(x =>
                    /^https?:/i.test(x.src) &&
                    x.w >= 250 && x.h >= 250 &&
                    !bad.test(x.src))
                .sort((a,b) => {
                    if (a.idx < 8 && b.idx >= 8) return -1;
                    if (b.idx < 8 && a.idx >= 8) return 1;
                    return b.area - a.area;
                });
              return rows.length ? rows[0].src : '';
            }"""
        ) or ""
    except Exception:
        return ""


def _open_specs(page) -> bool:
    if page is None:
        return False
    for label in ("包装规格", "选择规格", "选规格", "规格"):
        try:
            loc = page.get_by_text(label, exact=False)
            for i in range(min(loc.count(), 8)):
                item = loc.nth(i)
                if item.is_visible():
                    item.click(timeout=2500)
                    page.wait_for_timeout(800)
                    return True
        except Exception:
            continue
    return False


def _visible_skus(text: str) -> list[str]:
    lines = [_clean(x) for x in str(text or "").splitlines()]
    lines = [x for x in lines if x]
    result = []

    starts = [
        i for i, line in enumerate(lines)
        if "包装规格" in line or line in {"选择规格", "选规格", "规格"}
    ]

    for start in starts[:4]:
        for line in lines[start + 1:start + 70]:
            if any(x in line for x in ("立即购买", "加入购物车", "客服", "配送", "服务保障")):
                if result:
                    break
                continue
            if any(x in line for x in ("万人购买", "人购买", "已售", "大图")):
                continue
            if line.startswith(("¥", "￥")):
                continue
            if _human(line, 3, 180) and line not in result:
                result.append(line)

    return result[:80]


def _safe_body_text(page) -> str:
    try:
        if page is not None and not page.is_closed():
            return page.locator("body").inner_text(timeout=4000)
    except Exception:
        pass
    return ""


def _save_debug(debug_dir: Path, index: int, info: dict):
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"{index:03d}.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _save_jpg_from_url(url: str, target: Path, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            parsed = urllib.parse.urlparse(url)
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
                    ),
                    "Referer": f"{parsed.scheme or 'https'}://{parsed.netloc}/",
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()

            with Image.open(io.BytesIO(data)) as img:
                if getattr(img, "n_frames", 1) > 1:
                    img.seek(0)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(target, "JPEG", quality=95, optimize=True)
            return
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"JPG下载失败：{last}")


def resolve_page(context, source_url: str, index: int, debug_dir: Path) -> dict:
    """Robust product-link resolver.

    Key fixes:
    - pre-resolve short URL
    - listen on BrowserContext, so popup/replacement pages are captured too
    - recover when the original page closes itself
    - never let page.close()/page.title() mask a successfully captured result
    """
    result = {
        "title": "",
        "image_url": "",
        "sku_titles": [],
        "final_url": source_url,
    }

    responses = []
    payloads = []
    body_text = ""

    resolved_url = _resolve_http_redirect(source_url)
    page = None

    def on_response(response):
        try:
            ctype = (response.headers.get("content-type") or "").lower()
            rurl = response.url
            interesting = (
                "json" in ctype
                or any(x in rurl.lower() for x in (
                    "product", "goods", "item", "sku", "spec",
                    "mall", "ecom", "detail", "shop", "commodity"
                ))
            )
            if not interesting:
                return

            responses.append({
                "url": rurl,
                "status": response.status,
                "content_type": ctype,
            })

            if "json" in ctype:
                try:
                    payloads.append(response.json())
                except Exception:
                    pass
        except Exception:
            pass

    try:
        context.on("response", on_response)
    except Exception:
        pass

    try:
        page = context.new_page()

        try:
            page.goto(resolved_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            if not _is_closed_error(exc):
                raise

        # Short-link / app-launch pages may close the original tab and open another.
        time.sleep(1.0)
        page = _live_page(context, page)
        if page is None:
            # Re-open resolved URL if site closed all tabs.
            page = context.new_page()
            page.goto(resolved_url, wait_until="domcontentloaded", timeout=45000)

        try:
            page.wait_for_timeout(3500)
        except Exception as exc:
            if _is_closed_error(exc):
                page = _live_page(context, page)

        if page is None:
            raise RuntimeError("商品页面已关闭，且未检测到替代页面")

        result["final_url"] = _safe_page_url(page, resolved_url)

        try:
            page.wait_for_load_state("networkidle", timeout=7000)
        except Exception:
            pass

        # Parse all JSON seen during page load / redirects / popup navigation.
        for payload in list(payloads):
            _merge(result, _metadata_from_json(payload))

        if not result["title"]:
            result["title"] = _dom_title(page)
        if not result["image_url"]:
            result["image_url"] = _largest_image(page)

        # Trigger SKU/spec data.
        before = len(payloads)
        _open_specs(page)

        page = _live_page(context, page)
        if page is not None:
            try:
                page.wait_for_timeout(1000)
            except Exception:
                pass

        for payload in payloads[before:]:
            _merge(result, _metadata_from_json(payload))

        body_text = _safe_body_text(page)
        for sku in _visible_skus(body_text):
            if sku not in result["sku_titles"]:
                result["sku_titles"].append(sku)

        if not result["title"]:
            result["title"] = _dom_title(page)
        if not result["image_url"]:
            result["image_url"] = _largest_image(page)

        # Final JSON pass in case late network responses arrived.
        for payload in payloads:
            _merge(result, _metadata_from_json(payload))

    finally:
        # Diagnostics must never throw if the page has already been closed.
        if not result["title"] or not result["image_url"] or not result["sku_titles"]:
            _save_debug(debug_dir, index, {
                "source_url": source_url,
                "pre_resolved_url": resolved_url,
                "final_url": _safe_page_url(page, result.get("final_url", resolved_url)),
                "page_title": _safe_page_title(page),
                "title_found": bool(result.get("title")),
                "image_found": bool(result.get("image_url")),
                "sku_count": len(result.get("sku_titles") or []),
                "body_preview": body_text[:6000],
                "responses": responses[-180:],
            })

        try:
            context.remove_listener("response", on_response)
        except Exception:
            pass

        _safe_close_page(page)

    return result


class ProductLinkWorker(QThread):
    item_result = Signal(int, object)
    info = Signal(str)
    progress = Signal(int, int)
    finished_summary = Signal(object)

    def __init__(
        self,
        urls: list[dict] | list[str],
        output_parent: Path,
        *,
        concurrency: int = 1,
        retries: int = 2,
        parent=None,
    ):
        super().__init__(parent)
        self.urls = [
            str(x.get("url") if isinstance(x, dict) else x).strip()
            for x in urls
        ]
        self.output_parent = Path(output_parent)
        self.concurrency = 1
        self.retries = max(0, int(retries))
        self.stop_event = threading.Event()

    def request_stop(self):
        self.stop_event.set()

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
        ws.column_dimensions["B"].width = 90
        ws.column_dimensions["C"].width = 110
        ws.freeze_panes = "A2"
        path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)

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
        total = len(self.urls)

        self.info.emit(
            "正在启动 Edge 商品解析浏览器。解析过程中请不要手动关闭 Edge；首次登录后会自动复用登录状态。"
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
                    for idx, url in enumerate(self.urls):
                        if self.stop_event.is_set():
                            break

                        self.info.emit(f"正在解析 {idx + 1}/{total}")
                        try:
                            meta = resolve_page(context, url, idx + 1, debug_dir)
                            title = _clean(meta.get("title", ""))
                            image_url = _clean(meta.get("image_url", ""))
                            skus = meta.get("sku_titles") or []
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
                            # If the user manually closed Edge, stop cleanly instead of hiding
                            # the real reason behind another page.close() exception.
                            if _is_closed_error(exc):
                                msg = "Edge 浏览器被关闭，已停止解析。请重新运行并保持解析浏览器打开。"
                                errors.append(f"{idx + 1}: {msg}")
                                row = {
                                    "source_url": url,
                                    "title": f"商品_{idx + 1:03d}",
                                    "image_url": "",
                                    "sku_titles": [],
                                    "status": msg,
                                }
                                rows.append(row)
                                self.item_result.emit(idx, row)
                                self.progress.emit(idx + 1, total)
                                break

                            row = {
                                "source_url": url,
                                "title": f"商品_{idx + 1:03d}",
                                "image_url": "",
                                "sku_titles": [],
                                "status": f"失败：{exc}",
                            }
                            errors.append(f"{idx + 1}: {exc}")

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

        self.finished_summary.emit({
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
        })
