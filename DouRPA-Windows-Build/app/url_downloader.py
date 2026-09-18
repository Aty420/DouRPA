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


def _extract_goods_detail_from_url(url: str) -> dict:
    """Parse Douyin's percent-encoded goods_detail query parameter.

    Real Douyin share redirects often already contain:
      goods_detail = {
        "title": "...",
        "img": {"url_list": ["https://...first-image...", ...]}
      }

    This is a much stronger source for the first product image than DOM guessing.
    """
    result = {
        "found": False,
        "title": "",
        "image_url": "",
        "image_urls": [],
        "raw": {},
    }
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        values = query.get("goods_detail") or []
        if not values:
            return result

        raw_value = values[0]
        if isinstance(raw_value, str):
            obj = json.loads(raw_value)
        else:
            obj = raw_value

        if not isinstance(obj, dict):
            return result

        result["found"] = True
        result["raw"] = obj
        result["title"] = _clean(obj.get("title", ""))

        img = obj.get("img") or obj.get("image") or {}
        urls = []
        if isinstance(img, dict):
            candidate = img.get("url_list") or img.get("urls") or []
            if isinstance(candidate, str):
                candidate = [candidate]
            if isinstance(candidate, list):
                for item in candidate:
                    item = _clean(item)
                    if item.startswith(("http://", "https://")) and item not in urls:
                        urls.append(item)

        result["image_urls"] = urls
        result["image_url"] = urls[0] if urls else ""
        return result
    except Exception:
        return result


def _redact_sensitive(obj):
    """Best-effort redaction for diagnostic JSON. No cookies/headers are stored."""
    sensitive_words = (
        "token", "cookie", "session", "signature", "sign", "verifyfp",
        "a_bogus", "ms_token", "mstoken", "did", "secuid", "sec_author",
        "passport", "auth", "device_id",
    )

    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            key_text = str(key).lower()
            if any(word in key_text for word in sensitive_words):
                out[str(key)] = "<redacted>"
            else:
                out[str(key)] = _redact_sensitive(value)
        return out
    if isinstance(obj, list):
        return [_redact_sensitive(x) for x in obj[:500]]
    if isinstance(obj, str):
        return obj[:3000]
    return obj


def _interesting_json_paths(obj, limit=300):
    """Collect SKU/spec/property-related scalar paths for fast diagnosis."""
    rows = []
    hints = ("sku", "spec", "规格", "property", "prop", "sale", "pack", "product")
    for path, value in _walk(obj):
        joined = "/".join(str(x) for x in path).lower()
        if not any(h in joined for h in hints):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            rows.append({
                "path": "/".join(str(x) for x in path),
                "value": str(value)[:600],
            })
            if len(rows) >= limit:
                break
    return rows


def _write_json(path: Path, payload):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


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
_SKU_CONTEXT = (
    "sku", "spec", "规格", "saleprop", "sale_prop", "product_sku",
    "property", "properties", "prop_value", "sale_property",
    "variant", "variation", "option", "specification",
)


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
    """Download source image and always save a real JPEG.

    Also writes a diagnostic sidecar under 解析诊断 so that download/codec
    failures can be distinguished from metadata parsing failures.
    """
    last = None
    target = Path(target)
    diag_dir = target.parent.parent / "解析诊断"
    prefix = target.name.split("_", 1)[0] if "_" in target.name else target.stem
    diag_path = diag_dir / f"{prefix}_image_download.json"

    diag = {
        "image_url": url,
        "target": str(target),
        "attempts": [],
        "saved": False,
    }

    for attempt in range(retries + 1):
        attempt_info = {"attempt": attempt + 1}
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
                attempt_info["http_status"] = getattr(resp, "status", None)
                attempt_info["content_type"] = resp.headers.get("Content-Type", "")
                attempt_info["content_length_header"] = resp.headers.get("Content-Length", "")

            attempt_info["downloaded_bytes"] = len(data)

            with Image.open(io.BytesIO(data)) as img:
                attempt_info["source_format"] = img.format
                attempt_info["source_size"] = list(img.size)
                attempt_info["source_mode"] = img.mode

                if getattr(img, "n_frames", 1) > 1:
                    img.seek(0)
                if img.mode != "RGB":
                    img = img.convert("RGB")

                target.parent.mkdir(parents=True, exist_ok=True)
                img.save(target, "JPEG", quality=95, optimize=True)

            attempt_info["saved_path"] = str(target)
            attempt_info["saved_bytes"] = target.stat().st_size if target.exists() else 0
            attempt_info["success"] = target.exists() and target.stat().st_size > 0
            diag["attempts"].append(attempt_info)
            diag["saved"] = bool(attempt_info["success"])
            _write_json(diag_path, diag)

            if not diag["saved"]:
                raise RuntimeError("JPG保存后文件为空")
            return

        except Exception as exc:
            last = exc
            attempt_info["success"] = False
            attempt_info["error"] = str(exc)
            diag["attempts"].append(attempt_info)
            _write_json(diag_path, diag)

            if attempt < retries:
                time.sleep(0.7 * (attempt + 1))

    raise RuntimeError(f"JPG下载失败：{last}")



def _response_json_payload(response):
    """Read JSON responses robustly.

    Playwright response.json() can occasionally fail even when Content-Type is
    application/json. Fall back to response.text() + json.loads().
    """
    errors = []
    try:
        return response.json(), errors
    except Exception as exc:
        errors.append(f"response.json: {exc}")

    try:
        raw = response.text()
        return json.loads(raw), errors
    except Exception as exc:
        errors.append(f"response.text/json.loads: {exc}")

    try:
        raw = response.body().decode("utf-8", errors="ignore")
        return json.loads(raw), errors
    except Exception as exc:
        errors.append(f"response.body/json.loads: {exc}")

    return None, errors


_SKU_NOISE = {
    "包装规格", "选择规格", "选规格", "规格", "已选", "数量", "购买数量",
    "加入购物车", "立即购买", "去抢购", "客服", "购物车", "店铺",
    "确定", "取消", "关闭", "库存", "有货", "配送", "服务", "保障",
}


def _sku_like_text(value: str, product_title: str = "") -> bool:
    value = _clean(value)
    if not _human(value, 2, 180):
        return False
    if value in _SKU_NOISE:
        return False
    if product_title and value == _clean(product_title):
        return False
    if value.startswith(("¥", "￥")):
        return False
    if re.fullmatch(r"[\d\s.,/%+\-*]+", value):
        return False
    if any(x in value for x in ("运费险", "无理由退货", "商家资质", "商品评价", "回头客")):
        return False
    return True


def _extract_modal_skus(before_text: str, after_text: str, product_title: str = "") -> list[str]:
    """Extract newly appeared SKU/spec option lines after opening the purchase sheet."""
    before = {_clean(x) for x in str(before_text or "").splitlines() if _clean(x)}
    after = [_clean(x) for x in str(after_text or "").splitlines() if _clean(x)]

    # First use the existing explicit 包装规格 parser if the sheet exposes a heading.
    explicit = _visible_skus(after_text)
    explicit = [x for x in explicit if _sku_like_text(x, product_title)]
    if explicit:
        return explicit

    # Otherwise use text that appeared only after the bottom sheet opened.
    fresh = []
    for line in after:
        if line in before:
            continue
        if not _sku_like_text(line, product_title):
            continue
        fresh.append(line)

    # Strong preference: option text usually contains quantity/size/color tokens.
    strong_tokens = (
        "抽", "提", "包", "箱", "卷", "片", "支", "瓶", "盒", "袋",
        "ml", "ML", "g", "kg", "KG", "cm", "mm", "码", "色", "款",
    )
    strong = [x for x in fresh if any(tok in x for tok in strong_tokens)]

    result = strong if strong else fresh
    deduped = []
    for item in result:
        if item not in deduped:
            deduped.append(item)
    return deduped[:80]



_APP_SCHEMES = ("sslocal://", "snssdk://", "aweme://", "douyin://")


def _install_app_jump_blocker(page):
    """Best-effort block of Douyin app deep links while keeping page JS/network alive."""
    if page is None:
        return

    script = r"""
    (() => {
      const blocked = ['sslocal://', 'snssdk://', 'aweme://', 'douyin://'];
      const isBlocked = (u) => {
        try {
          const s = String(u || '').toLowerCase();
          return blocked.some(p => s.startsWith(p));
        } catch (_) {
          return false;
        }
      };

      // 1) Block anchor-based deep links in capture phase.
      document.addEventListener('click', (ev) => {
        try {
          const el = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
          if (el && isBlocked(el.getAttribute('href') || el.href)) {
            ev.preventDefault();
            ev.stopImmediatePropagation();
          }
        } catch (_) {}
      }, true);

      // 2) Block window.open deep links.
      try {
        const oldOpen = window.open;
        window.open = function(url, ...args) {
          if (isBlocked(url)) return null;
          return oldOpen.call(this, url, ...args);
        };
      } catch (_) {}

      // 3) Block location.assign / location.replace when writable.
      try {
        const oldAssign = Location.prototype.assign;
        Location.prototype.assign = function(url) {
          if (isBlocked(url)) return;
          return oldAssign.call(this, url);
        };
      } catch (_) {}

      try {
        const oldReplace = Location.prototype.replace;
        Location.prototype.replace = function(url) {
          if (isBlocked(url)) return;
          return oldReplace.call(this, url);
        };
      } catch (_) {}

      window.__DOU_RPA_DEEPLINK_BLOCKER__ = true;
    })();
    """

    try:
        page.add_init_script(script)
    except Exception:
        pass

    try:
        page.evaluate(script)
    except Exception:
        pass

    # CDP-level block as a second layer. Not every Chromium build handles
    # custom schemes here, but it is harmless and catches normal navigations.
    try:
        session = page.context.new_cdp_session(page)
        session.send("Network.enable")
        session.send(
            "Network.setBlockedURLs",
            {"urls": ["sslocal://*", "snssdk://*", "aweme://*", "douyin://*"]},
        )
    except Exception:
        pass


def _decode_nested_json(value):
    """Yield decoded JSON objects embedded inside string fields."""
    if not isinstance(value, str):
        return
    s = value.strip()
    if not s or s[0] not in "[{":
        return
    try:
        obj = json.loads(s)
    except Exception:
        return
    yield obj
    for _, child in _walk(obj):
        if isinstance(child, str):
            yield from _decode_nested_json(child)


def _sku_candidate_score(path: tuple, value: str, product_title: str = "") -> int:
    value = _clean(value)
    if not _sku_like_text(value, product_title):
        return -999

    joined = "/".join(str(x).lower() for x in path)
    key = str(path[-1]).lower() if path else ""

    score = 0
    context_tokens = (
        "sku", "spec", "property", "properties", "prop",
        "variant", "variation", "option", "specification",
        "sale_property", "saleprop",
    )
    if any(tok in joined for tok in context_tokens):
        score += 60

    if key in {
        "name", "title", "text", "label", "value", "desc", "display_name",
        "spec_name", "spec_value", "spec_value_name", "sku_name", "sku_title",
        "property_name", "property_value_name", "option_name",
    }:
        score += 30

    strong_tokens = (
        "抽", "提", "包", "箱", "卷", "片", "支", "瓶", "盒", "袋",
        "ml", "ML", "g", "kg", "KG", "cm", "mm", "码", "色", "款",
    )
    if any(tok in value for tok in strong_tokens):
        score += 25

    if "*" in value or "×" in value:
        score += 15
    if "(" in value or "（" in value:
        score += 8

    # Penalize obvious generic UI/marketing strings.
    if any(x in value for x in (
        "运费险", "无理由退货", "商家资质", "商品评价", "加入购物车",
        "去抢购", "立即购买", "现在下单", "包邮", "活动", "优惠",
    )):
        score -= 80

    return score


def _extract_sku_titles_from_payload(payload, product_title: str = "") -> list[str]:
    """Extract likely SKU option titles from arbitrary Douyin JSON structures."""
    scored = []

    def scan(obj):
        for path, value in _walk(obj):
            if isinstance(value, str):
                score = _sku_candidate_score(path, value, product_title)
                if score >= 60:
                    scored.append((score, len(_clean(value)), _clean(value)))

                # Some API fields contain JSON strings.
                for nested in _decode_nested_json(value) or []:
                    scan(nested)

    scan(payload)

    # Highest confidence first, dedupe by exact text.
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = []
    for _, _, value in scored:
        if value not in out:
            out.append(value)
    return out[:80]


def _safe_request_snapshot(request):
    try:
        url = request.url
    except Exception:
        url = ""
    try:
        method = request.method
    except Exception:
        method = ""
    try:
        post_data = request.post_data or ""
    except Exception:
        post_data = ""

    # Avoid dumping very large or sensitive payloads.
    if len(post_data) > 4000:
        post_data = post_data[:4000] + "...<truncated>"

    return {
        "url": url,
        "method": method,
        "resource_type": getattr(request, "resource_type", ""),
        "post_data": post_data,
    }


def _open_specs_or_purchase(page) -> tuple[bool, str]:
    """Trigger the SKU panel without intentionally leaving H5.

    Priority:
    1. Existing spec entry, if present.
    2. 加入购物车
    3. 去抢购
    4. 立即购买

    Before clicking, install several deep-link blockers. The click itself is
    dispatched with DOM click() so Playwright does not wait for app navigation.
    """
    if page is None:
        return False, ""

    _install_app_jump_blocker(page)

    labels = (
        "包装规格", "选择规格", "选规格", "规格",
        "加入购物车", "去抢购", "立即购买",
    )

    for label in labels:
        # First try exact text.
        try:
            loc = page.get_by_text(label, exact=True)
            for i in range(min(loc.count(), 10)):
                item = loc.nth(i)
                if item.is_visible():
                    try:
                        item.evaluate("(el) => el.click()")
                    except Exception:
                        item.click(timeout=2500, no_wait_after=True)
                    try:
                        page.wait_for_timeout(900)
                    except Exception:
                        pass
                    return True, label
        except Exception:
            pass

        # Then loose text, useful for buttons that contain nested spans.
        try:
            loc = page.get_by_text(label, exact=False)
            for i in range(min(loc.count(), 10)):
                item = loc.nth(i)
                if item.is_visible():
                    try:
                        item.evaluate("(el) => el.click()")
                    except Exception:
                        item.click(timeout=2500, no_wait_after=True)
                    try:
                        page.wait_for_timeout(900)
                    except Exception:
                        pass
                    return True, label
        except Exception:
            continue

    return False, ""


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
    modal_body_text = ""
    response_json_errors = []
    trigger_requests = []
    trigger_active = False
    opened_by = ""
    trigger_stage = "init"
    trigger_error = ""

    resolved_url = _resolve_http_redirect(source_url)
    goods_detail = _extract_goods_detail_from_url(resolved_url)

    # First priority for title/image: encoded product metadata already present in
    # the Douyin redirect URL itself. This avoids guessing the first image from DOM.
    if goods_detail.get("title"):
        result["title"] = goods_detail["title"]
    if goods_detail.get("image_url"):
        result["image_url"] = goods_detail["image_url"]

    page = None
    pack_payloads = []

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
                payload, parse_errors = _response_json_payload(response)
                if parse_errors:
                    response_json_errors.append({
                        "url": rurl,
                        "errors": parse_errors,
                    })

                if payload is not None:
                    payloads.append(payload)

                    low_url = rurl.lower()
                    if (
                        "/aweme/v2/shop/promotion/pack/h5/" in low_url
                        or "/aweme/v2/shop/promotion/pack/detail/" in low_url
                    ):
                        pack_payloads.append({
                            "url": rurl,
                            "payload": payload,
                        })
        except Exception:
            pass

    def on_request(request):
        nonlocal trigger_active
        if not trigger_active:
            return
        try:
            trigger_requests.append(_safe_request_snapshot(request))
        except Exception:
            pass

    popup_pages = []

    def on_popup(popup):
        # Only popups created FROM the already-created product page are closed.
        # Do not use context.on("page") before creating the main page, because
        # that would close the main page itself.
        try:
            popup_pages.append(_safe_page_url(popup, ""))
            popup.close()
        except Exception:
            pass

    try:
        context.on("response", on_response)
    except Exception:
        pass
    try:
        context.on("request", on_request)
    except Exception:
        pass
    try:
        trigger_stage = "create_main_page"
        page = context.new_page()
        _install_app_jump_blocker(page)
        try:
            page.on("popup", on_popup)
        except Exception:
            pass

        trigger_stage = "goto_product_h5"
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
            _install_app_jump_blocker(page)
            try:
                page.on("popup", on_popup)
            except Exception:
                pass
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
        before_payload_count = len(payloads)
        before_pack_count = len(pack_payloads)
        before_body_text = _safe_body_text(page)

        trigger_stage = "click_sku_trigger"
        trigger_active = True
        opened, opened_by = _open_specs_or_purchase(page)
        trigger_stage = "wait_trigger_network"

        page = _live_page(context, page)

        # Give the H5 page time to generate signed XHR/fetch requests after the
        # click. We do not navigate away; we only observe the resulting network.
        if opened:
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                page = _live_page(context, page)
                if page is not None:
                    try:
                        page.wait_for_timeout(250)
                    except Exception:
                        pass

                has_detail = any(
                    "/aweme/v2/shop/promotion/pack/detail/" in str(x.get("url", "")).lower()
                    for x in pack_payloads[before_pack_count:]
                )
                if has_detail:
                    # Keep a little extra time for late responses / modal rendering.
                    if page is not None:
                        try:
                            page.wait_for_timeout(700)
                        except Exception:
                            pass
                    break

        trigger_active = False
        trigger_stage = "parse_sku_payloads"

        # Parse every payload that arrived after opening the SKU sheet.
        for payload in payloads[before_payload_count:]:
            _merge(result, _metadata_from_json(payload))

        # Also explicitly parse the captured pack payloads, including late pack/detail.
        for record in pack_payloads:
            payload = record.get("payload")
            if payload is not None:
                _merge(result, _metadata_from_json(payload))
                for sku in _extract_sku_titles_from_payload(
                    payload,
                    result.get("title", ""),
                ):
                    if sku not in result["sku_titles"]:
                        result["sku_titles"].append(sku)

        body_text = _safe_body_text(page)
        modal_body_text = body_text

        # Existing heading-based parser.
        for sku in _visible_skus(body_text):
            if _sku_like_text(sku, result.get("title", "")) and sku not in result["sku_titles"]:
                result["sku_titles"].append(sku)

        # H5 fallback: capture text newly introduced by 加入购物车/去抢购 bottom sheet.
        if not result["sku_titles"] and opened:
            for sku in _extract_modal_skus(
                before_body_text,
                body_text,
                result.get("title", ""),
            ):
                if sku not in result["sku_titles"]:
                    result["sku_titles"].append(sku)

        if not result["title"]:
            result["title"] = _dom_title(page)
        if not result["image_url"]:
            result["image_url"] = _largest_image(page)

        # Final JSON pass in case late network responses arrived.
        for payload in payloads:
            _merge(result, _metadata_from_json(payload))

        # Save the exact two product-detail JSON families that are most likely
        # to contain SKU/spec information. Sensitive-looking keys are redacted.
        for seq, record in enumerate(pack_payloads, 1):
            api_url = record.get("url", "")
            payload = record.get("payload")
            kind = "pack_detail" if "/pack/detail/" in api_url.lower() else "pack_h5"
            _write_json(
                debug_dir / f"{index:03d}_{kind}_{seq}.json",
                {
                    "api_url": api_url,
                    "sku_spec_paths": _interesting_json_paths(payload),
                    "extracted_metadata": _metadata_from_json(payload),
                    "sku_candidates": _extract_sku_titles_from_payload(
                        payload,
                        result.get("title", ""),
                    ),
                    "payload": _redact_sensitive(payload),
                },
            )

        trigger_stage = "done"

    except Exception as exc:
        trigger_error = str(exc)
        raise

    finally:
        # ALWAYS save SKU-trigger diagnostics, even if the main page/trigger failed.
        _write_json(
            debug_dir / f"{index:03d}_sku_trigger_network.json",
            {
                "stage": trigger_stage,
                "error": trigger_error,
                "opened_by": opened_by,
                "request_count": len(trigger_requests),
                "popup_pages": popup_pages,
                "current_page_url": _safe_page_url(page, ""),
                "requests": trigger_requests[-250:],
            },
        )
        # Diagnostics must never throw if the page has already been closed.
        if not result["title"] or not result["image_url"] or not result["sku_titles"]:
            _save_debug(debug_dir, index, {
                "source_url": source_url,
                "pre_resolved_url": resolved_url,
                "final_url": _safe_page_url(page, result.get("final_url", resolved_url)),
                "page_title": _safe_page_title(page),
                "title_found": bool(result.get("title")),
                "image_found": bool(result.get("image_url")),
                "selected_image_url": result.get("image_url", ""),
                "sku_count": len(result.get("sku_titles") or []),
                "goods_detail": {
                    "found": bool(goods_detail.get("found")),
                    "title": goods_detail.get("title", ""),
                    "image_url": goods_detail.get("image_url", ""),
                    "image_urls": goods_detail.get("image_urls", []),
                },
                "pack_payload_count": len(pack_payloads),
                "pack_detail_count": sum(
                    1 for x in pack_payloads
                    if "/aweme/v2/shop/promotion/pack/detail/" in str(x.get("url", "")).lower()
                ),
                "trigger_stage": trigger_stage,
                "trigger_error": trigger_error,
                "opened_by": opened_by,
                "trigger_request_count": len(trigger_requests),
                "popup_pages": popup_pages,
                "sku_values": result.get("sku_titles", []),
                "body_preview": body_text[:6000],
                "modal_body_preview": modal_body_text[:6000],
                "response_json_errors": response_json_errors[-30:],
                "responses": responses[-180:],
            })

        try:
            context.remove_listener("response", on_response)
        except Exception:
            pass
        try:
            context.remove_listener("request", on_request)
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
            "正在后台静默启动 Edge 内核解析商品，不会显示浏览器窗口。已保存的登录状态会继续复用。"
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
