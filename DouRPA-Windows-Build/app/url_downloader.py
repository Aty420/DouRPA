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
        return value if value.startswith(("http://", "https://")) else ""
    if isinstance(value, list):
        for item in value:
            found = _first_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("url", "src", "url_list", "urls", "image", "imageUrl"):
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
    "pic_url", "cover_url", "origin_img", "images", "url_list",
}

_SKU_KEYS = {
    "skuname", "skutitle", "sku_name", "sku_title",
    "specdesc", "spec_desc", "specvaluename", "spec_value_name",
    "combinationtext", "combination_text",
    "propertyvaluename", "property_value_name",
    "salepropertyname", "sale_property_name",
}

_SKU_CONTEXT = ("sku", "spec", "规格", "saleprop", "sale_prop", "product_sku")


def _from_json(payload) -> dict:
    titles = []
    images = []
    skus = []

    title_norm = {x.replace("_", "") for x in _TITLE_KEYS}
    image_norm = {x.replace("_", "") for x in _IMAGE_KEYS}
    sku_norm = {x.replace("_", "") for x in _SKU_KEYS}

    for path, value in _walk(payload):
        if not path:
            continue

        key = path[-1].replace("-", "_").lower()
        norm = key.replace("_", "")
        joined = "/".join(x.lower().replace("-", "_") for x in path)

        if isinstance(value, str):
            text = _clean(value)

            if norm in title_norm and _human(text, 5, 220):
                score = 20
                if any(x in joined for x in ("product", "goods", "item", "商品")):
                    score += 20
                if any(x in text for x in ("抽", "提", "包", "箱", "卷", "片", "装", "纸", "巾")):
                    score += 10
                titles.append((score, len(text), text))

            if norm in sku_norm and _human(text, 3, 180):
                if text not in skus:
                    skus.append(text)
            elif any(h in joined for h in _SKU_CONTEXT):
                if key in {"name", "title", "desc", "text", "label", "value"} and _human(text, 3, 180):
                    if text not in skus:
                        skus.append(text)

        if norm in image_norm:
            found = _first_url(value)
            if found and found not in images:
                images.append(found)

    title = ""
    if titles:
        titles.sort(reverse=True)
        title = titles[0][2]

    return {
        "title": title,
        "image_url": images[0] if images else "",
        "sku_titles": skus[:100],
    }


def _merge(dst: dict, src: dict):
    if not dst.get("title") and src.get("title"):
        dst["title"] = src["title"]
    if not dst.get("image_url") and src.get("image_url"):
        dst["image_url"] = src["image_url"]

    current = dst.setdefault("sku_titles", [])
    for value in src.get("sku_titles") or []:
        value = _clean(value)
        if _human(value, 3, 180) and value not in current:
            current.append(value)


def _dom_title(page) -> str:
    try:
        meta = page.locator('meta[property="og:title"]').get_attribute("content")
        if _human(meta, 5, 220):
            return _clean(meta)
    except Exception:
        pass

    for selector in ("h1", "[class*='title']", "[class*='Title']"):
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 10)):
                value = _clean(loc.nth(i).inner_text(timeout=1000))
                if _human(value, 5, 220):
                    return value
        except Exception:
            pass

    try:
        value = _clean(page.title())
        value = re.sub(r"[-_|]\s*(抖音|抖音商城|淘宝|天猫).*$", "", value).strip()
        if _human(value, 5, 220) and value not in {"抖音", "淘宝", "天猫"}:
            return value
    except Exception:
        pass

    return ""


def _largest_image(page) -> str:
    try:
        return page.evaluate(
            """() => {
              const bad = /(avatar|logo|icon|emoji|qrcode|qr-code)/i;
              const rows = [...document.images].map(img => ({
                src: img.currentSrc || img.src || '',
                w: img.naturalWidth || 0,
                h: img.naturalHeight || 0,
                area: (img.naturalWidth || 0) * (img.naturalHeight || 0)
              })).filter(x => /^https?:/i.test(x.src)
                    && x.w >= 250 && x.h >= 250 && !bad.test(x.src))
                .sort((a,b) => b.area - a.area);
              return rows.length ? rows[0].src : '';
            }"""
        ) or ""
    except Exception:
        return ""


def _open_specs(page):
    for label in ("包装规格", "选择规格", "选规格", "规格"):
        try:
            loc = page.get_by_text(label, exact=False)
            for i in range(min(loc.count(), 6)):
                item = loc.nth(i)
                if item.is_visible():
                    item.click(timeout=2500)
                    page.wait_for_timeout(900)
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


def _profile_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "DouRPA" if base else Path.home() / "AppData" / "Local" / "DouRPA"
    path = root / "data" / "url_parser_profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_debug(debug_dir: Path, index: int, info: dict):
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / f"{index:03d}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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


def resolve_page(context, url: str, index: int, debug_dir: Path) -> dict:
    result = {
        "title": "",
        "image_url": "",
        "sku_titles": [],
        "final_url": url,
    }
    responses = []
    payloads = []
    body_text = ""

    page = context.new_page()

    def on_response(response):
        try:
            ctype = (response.headers.get("content-type") or "").lower()
            rurl = response.url
            interesting = (
                "json" in ctype
                or any(x in rurl.lower() for x in (
                    "product", "goods", "item", "sku", "spec",
                    "mall", "ecom", "detail", "shop"
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

    page.on("response", on_response)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4500)
        result["final_url"] = page.url

        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        for payload in payloads:
            _merge(result, _from_json(payload))

        if not result["title"]:
            result["title"] = _dom_title(page)
        if not result["image_url"]:
            result["image_url"] = _largest_image(page)

        before = len(payloads)
        _open_specs(page)
        page.wait_for_timeout(1200)

        for payload in payloads[before:]:
            _merge(result, _from_json(payload))

        try:
            body_text = page.locator("body").inner_text(timeout=4000)
            for sku in _visible_skus(body_text):
                if sku not in result["sku_titles"]:
                    result["sku_titles"].append(sku)
        except Exception:
            pass

        if not result["title"]:
            result["title"] = _dom_title(page)
        if not result["image_url"]:
            result["image_url"] = _largest_image(page)

        if not result["title"] or not result["image_url"] or not result["sku_titles"]:
            _save_debug(debug_dir, index, {
                "source_url": url,
                "final_url": result["final_url"],
                "page_title": page.title(),
                "title_found": bool(result["title"]),
                "image_found": bool(result["image_url"]),
                "sku_count": len(result["sku_titles"]),
                "body_preview": body_text[:6000],
                "responses": responses[-150:],
            })
    finally:
        page.close()

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
        self.concurrency = 1  # real persistent browser processes URLs sequentially
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
            "正在启动真实 Edge。首次出现抖音/淘宝登录页时，请在打开的 Edge 中登录；登录状态会保存。"
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
                    context.close()
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
