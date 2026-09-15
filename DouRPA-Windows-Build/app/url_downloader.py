from __future__ import annotations

import mimetypes
import re
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PySide6.QtCore import QThread, Signal


_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def sanitize_filename(name: str, fallback: str = "image") -> str:
    name = _INVALID_FILENAME.sub("_", str(name or "")).strip(" ._")
    name = re.sub(r"\s+", " ", name)
    return (name[:120] or fallback).strip()


def _content_ext(url: str, content_type: str = "") -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}:
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
            raise RuntimeError("下载已停止")
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/138.0 Safari/537.36"
                    ),
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": urllib.parse.urlunparse(
                        (urllib.parse.urlparse(url).scheme or "https", urllib.parse.urlparse(url).netloc, "/", "", "", "")
                    ),
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
            if target.exists() and not skip_existing:
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
    raise RuntimeError(str(last_exc or "下载失败"))


class DownloadWorker(QThread):
    item_progress = Signal(int, str, str, int)
    info = Signal(str)
    finished_summary = Signal(object)

    def __init__(
        self,
        items: list[dict],
        dest_dir: Path,
        *,
        concurrency: int = 5,
        retries: int = 2,
        skip_existing: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.items = list(items)
        self.dest_dir = Path(dest_dir)
        self.concurrency = max(1, min(16, int(concurrency)))
        self.retries = max(0, int(retries))
        self.skip_existing = bool(skip_existing)
        self.stop_event = threading.Event()

    def request_stop(self):
        self.stop_event.set()

    def _download(self, idx: int, item: dict):
        url = str(item.get("url") or "").strip()
        name = sanitize_filename(item.get("name") or f"{idx + 1:03d}")
        self.item_progress.emit(idx, name, "下载中", 10)
        path = download_one(
            url,
            self.dest_dir,
            name,
            retries=self.retries,
            stop_event=self.stop_event,
            skip_existing=self.skip_existing,
        )
        self.item_progress.emit(idx, path.name, "已完成", 100)
        return path

    def run(self):
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        success = 0
        failed = 0
        errors: list[str] = []
        self.info.emit(f"开始下载 {len(self.items)} 个 URL，并发数 {self.concurrency}")

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = {
                executor.submit(self._download, idx, item): (idx, item)
                for idx, item in enumerate(self.items)
                if str(item.get("url") or "").strip()
            }
            for future in as_completed(futures):
                idx, item = futures[future]
                if self.stop_event.is_set():
                    break
                try:
                    future.result()
                    success += 1
                except Exception as exc:
                    failed += 1
                    name = sanitize_filename(item.get("name") or f"{idx + 1:03d}")
                    msg = str(exc)
                    errors.append(f"{name}: {msg}")
                    self.item_progress.emit(idx, name, f"失败：{msg}", 0)

            if self.stop_event.is_set():
                for f in futures:
                    f.cancel()

        self.finished_summary.emit(
            {
                "success": success,
                "failed": failed,
                "stopped": self.stop_event.is_set(),
                "errors": errors,
                "dest_dir": str(self.dest_dir),
            }
        )
