from __future__ import annotations

import re
import threading
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _valid_sku_text(text: str) -> bool:
    text = _clean(text)
    if not text or len(text) > 80:
        return False
    lowered = text.lower()
    rejects = (
        "¥", "￥", "价格", "数量", "购买", "优惠", "配送", "服务",
        "尺码表", "立即购买", "加入购物车", "已选", "库存", "月销",
    )
    if any(x in text for x in rejects):
        return False
    if re.fullmatch(r"[\d.]+", text):
        return False
    return len(text) >= 2 and not lowered.startswith("http")


class TaobaoCollector:
    def __init__(self, profile_dir: Path, progress_cb=None, stop_event: threading.Event | None = None):
        self.profile_dir = Path(profile_dir)
        self.progress_cb = progress_cb or (lambda *args, **kwargs: None)
        self.stop_event = stop_event or threading.Event()

    def _check_stop(self):
        if self.stop_event.is_set():
            raise RuntimeError("采集已停止")

    def _emit(self, text: str):
        self.progress_cb(text)

    def collect(self, keyword: str, limit: int) -> list[dict]:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict] = []
        keyword = _clean(keyword)
        limit = max(1, int(limit))

        with sync_playwright() as pw:
            kwargs = dict(
                user_data_dir=str(self.profile_dir),
                headless=False,
                no_viewport=True,
                args=["--start-maximized"],
            )
            try:
                context = pw.chromium.launch_persistent_context(channel="msedge", **kwargs)
            except Exception:
                context = pw.chromium.launch_persistent_context(**kwargs)

            page = context.pages[0] if context.pages else context.new_page()
            search_url = "https://s.taobao.com/search?q=" + urllib.parse.quote(keyword)
            self._emit(f"淘宝：搜索“{keyword}”")
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            links: list[tuple[str, str, str]] = []
            seen: set[str] = set()
            for _ in range(18):
                self._check_stop()
                anchors = page.locator(
                    'a[href*="item.taobao.com"], a[href*="detail.tmall.com"], a[href*="item.htm"]'
                )
                count = min(anchors.count(), 300)
                for i in range(count):
                    a = anchors.nth(i)
                    try:
                        href = a.get_attribute("href") or ""
                    except Exception:
                        continue
                    if href.startswith("//"):
                        href = "https:" + href
                    if not href.startswith("http") or href in seen:
                        continue
                    title = ""
                    for attr in ("title", "aria-label"):
                        try:
                            title = _clean(a.get_attribute(attr) or "")
                        except Exception:
                            pass
                        if title:
                            break
                    if not title:
                        try:
                            title = _clean(a.inner_text(timeout=800))
                        except Exception:
                            title = ""
                    if len(title) < 4:
                        continue
                    img = ""
                    try:
                        image = a.locator("img").first
                        for attr in ("src", "data-src", "data-ks-lazyload"):
                            img = image.get_attribute(attr) or ""
                            if img:
                                break
                    except Exception:
                        pass
                    if img.startswith("//"):
                        img = "https:" + img
                    seen.add(href)
                    links.append((href, title[:160], img))
                    if len(links) >= limit:
                        break
                if len(links) >= limit:
                    break
                page.mouse.wheel(0, 1500)
                page.wait_for_timeout(1200)

            if not links:
                self._emit("淘宝：没有读取到商品结果。若页面要求登录/验证，请人工完成后重试。")

            for index, (href, listing_title, listing_img) in enumerate(links[:limit], 1):
                self._check_stop()
                self._emit(f"淘宝：采集 {index}/{min(limit, len(links))}")
                detail = context.new_page()
                title = listing_title
                image_url = listing_img
                sku_titles: list[str] = []
                try:
                    detail.goto(href, wait_until="domcontentloaded", timeout=50000)
                    detail.wait_for_timeout(1800)

                    for selector in (
                        'meta[property="og:title"]',
                        'meta[name="twitter:title"]',
                    ):
                        try:
                            el = detail.locator(selector).first
                            content = _clean(el.get_attribute("content") or "")
                            if content:
                                title = content
                                break
                        except Exception:
                            pass
                    if not title:
                        for selector in ("h1", '[class*="Title"]', '[class*="title"]'):
                            try:
                                text = _clean(detail.locator(selector).first.inner_text(timeout=900))
                                if len(text) >= 4:
                                    title = text
                                    break
                            except Exception:
                                pass

                    for selector in (
                        'meta[property="og:image"]',
                        'meta[name="twitter:image"]',
                    ):
                        try:
                            content = detail.locator(selector).first.get_attribute("content") or ""
                            if content:
                                image_url = content
                                break
                        except Exception:
                            pass
                    if not image_url:
                        for selector in (
                            '[class*="Pic"] img', '[class*="pic"] img',
                            '[class*="Gallery"] img', '[class*="gallery"] img',
                        ):
                            try:
                                im = detail.locator(selector).first
                                for attr in ("src", "data-src"):
                                    value = im.get_attribute(attr) or ""
                                    if value:
                                        image_url = value
                                        break
                                if image_url:
                                    break
                            except Exception:
                                pass
                    if image_url.startswith("//"):
                        image_url = "https:" + image_url

                    candidates: list[str] = []
                    selectors = (
                        '[class*="sku"] button', '[class*="Sku"] button',
                        '[class*="sku"] [class*="item"]', '[class*="Sku"] [class*="Item"]',
                        '[class*="sku"] li', '[class*="Sku"] li',
                        '[data-value]',
                    )
                    for selector in selectors:
                        try:
                            loc = detail.locator(selector)
                            for j in range(min(loc.count(), 120)):
                                try:
                                    text = _clean(loc.nth(j).inner_text(timeout=300))
                                except Exception:
                                    continue
                                if _valid_sku_text(text):
                                    candidates.append(text)
                        except Exception:
                            continue
                    for text in candidates:
                        if text not in sku_titles:
                            sku_titles.append(text)
                    sku_titles = sku_titles[:40]
                except Exception as exc:
                    self._emit(f"淘宝：商品详情读取异常：{exc}")
                finally:
                    try:
                        detail.close()
                    except Exception:
                        pass

                results.append(
                    {
                        "platform": "淘宝",
                        "title": title or listing_title,
                        "image_url": image_url,
                        "sku_titles": sku_titles,
                        "source_url": href,
                    }
                )

            context.close()
        return results
