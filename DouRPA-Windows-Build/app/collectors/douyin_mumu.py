from __future__ import annotations

import ctypes
import html
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


DOUYIN_PACKAGE = "com.ss.android.ugc.aweme"
COMMON_PORTS = (16384, 16385, 7555, 62001, 5555)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _bounds(value: str):
    nums = [int(x) for x in re.findall(r"\d+", value or "")]
    if len(nums) >= 4:
        return nums[0], nums[1], nums[2], nums[3]
    return 0, 0, 0, 0


def _center(value: str):
    x1, y1, x2, y2 = _bounds(value)
    return (x1 + x2) // 2, (y1 + y2) // 2


def _set_windows_clipboard(text: str):
    if os.name != "nt":
        raise RuntimeError("MuMu 中文关键词自动粘贴目前仅支持 Windows")
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    data = (str(text) + "\0").encode("utf-16-le")
    h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h_mem:
        raise RuntimeError("无法分配剪贴板内存")
    ptr = kernel32.GlobalLock(h_mem)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(h_mem)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(h_mem)
        raise RuntimeError("无法打开 Windows 剪贴板")
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        h_mem = None
    finally:
        user32.CloseClipboard()
        if h_mem:
            kernel32.GlobalFree(h_mem)


def _get_windows_clipboard() -> str:
    if os.name != "nt":
        return ""
    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _normalize_web_url(value: str) -> str:
    value = html.unescape(str(value or ""))
    value = value.replace("\\u002F", "/").replace("\\u002f", "/")
    value = value.replace("\\u0026", "&").replace("\\/", "/")
    return value.strip('"\' ')


def resolve_douyin_image_url(share_url: str) -> str:
    if not share_url:
        return ""
    req = urllib.request.Request(
        share_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0 Mobile Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")

    patterns = (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'"url_list"\s*:\s*\[\s*"([^"]+)"',
        r'"cover"\s*:\s*\{[^{}]{0,800}?"url"\s*:\s*"([^"]+)"',
    )
    for pattern in patterns:
        m = re.search(pattern, raw, flags=re.I | re.S)
        if m:
            candidate = _normalize_web_url(m.group(1))
            if candidate.startswith("http"):
                return candidate

    candidates = re.findall(
        r'https?:\\?/\\?/[^"\'<> ]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\'<> ]*)?',
        raw,
        flags=re.I,
    )
    for candidate in candidates:
        candidate = _normalize_web_url(candidate)
        if candidate.startswith("http"):
            return candidate
    return ""


class MuMuAdbController:
    def __init__(self):
        self.adb = self._find_adb()
        self.serial = ""

    @staticmethod
    def _find_adb() -> str:
        """Locate MuMu's bundled adb.exe without assuming it is installed on C:.

        Search order:
        1) explicit MUMU_ADB_PATH
        2) adb on PATH
        3) common MuMu folders on C:-H:
        4) installation folders inferred from running MuMu/Nemu processes
        5) MuMu uninstall registry entries
        """
        candidates: list[str] = []

        def add(path):
            if not path:
                return
            try:
                value = str(Path(str(path).strip().strip('"')).expanduser())
            except Exception:
                return
            if value and value not in candidates:
                candidates.append(value)

        # 1. User override.
        add(os.environ.get("MUMU_ADB_PATH", "").strip())

        # 2. System PATH.
        add(shutil.which("adb"))

        # 3. Common install locations, including non-C drives and Global builds.
        rels = [
            r"Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
            r"Program Files\NetEase\MuMuPlayer-12.0\shell\adb.exe",
            r"Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
            r"Program Files\NetEase\MuMuPlayerGlobal-12.0\shell\adb.exe",
            r"Program Files (x86)\Netease\MuMuPlayer-12.0\shell\adb.exe",
            r"Program Files (x86)\NetEase\MuMuPlayer-12.0\shell\adb.exe",
            r"Program Files (x86)\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
            r"Program Files (x86)\NetEase\MuMuPlayerGlobal-12.0\shell\adb.exe",
            r"Netease\MuMuPlayer-12.0\shell\adb.exe",
            r"NetEase\MuMuPlayer-12.0\shell\adb.exe",
            r"MuMuPlayer-12.0\shell\adb.exe",
            r"MuMu\MuMuPlayer-12.0\shell\adb.exe",
        ]
        for drive in "CDEFGH":
            root = Path(f"{drive}:\\")
            if not root.exists():
                continue
            for rel in rels:
                add(root / rel)

        for base in (
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if base:
                for rel in (
                    r"Netease\MuMuPlayer-12.0\shell\adb.exe",
                    r"NetEase\MuMuPlayer-12.0\shell\adb.exe",
                    r"Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
                    r"NetEase\MuMuPlayerGlobal-12.0\shell\adb.exe",
                    r"MuMuPlayer-12.0\shell\adb.exe",
                ):
                    add(Path(base) / rel)

        # 4. Infer install folder from currently running MuMu/Nemu processes.
        #    This is the most reliable path for custom D:/E:/... installations.
        process_dirs: list[Path] = []
        if os.name == "nt":
            try:
                ps = (
                    "$ErrorActionPreference='SilentlyContinue';"
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.Name -match 'MuMu|Nemu|MuMuVMMS' } | "
                    "ForEach-Object { $_.ExecutablePath }"
                )
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=8,
                )
                for line in (proc.stdout or "").splitlines():
                    p = Path(line.strip().strip('"'))
                    if p.exists():
                        process_dirs.append(p.parent)
            except Exception:
                pass

        def add_nearby_adb(start: Path):
            """Search only near a MuMu install path, never an entire disk."""
            seen = set()
            roots = []
            cur = start
            for _ in range(5):
                if cur and cur not in seen and cur.exists():
                    roots.append(cur)
                    seen.add(cur)
                parent = cur.parent if cur else None
                if not parent or parent == cur:
                    break
                cur = parent

            direct_relatives = (
                r"adb.exe",
                r"shell\adb.exe",
                r"bin\adb.exe",
                r"tools\adb.exe",
                r"vms\myandrovm_vbox86\adb.exe",
            )
            for root in roots:
                for rel in direct_relatives:
                    add(root / rel)

            # Limited recursive scan under the two closest folders.
            # MuMu layouts differ by version, so this catches custom/newer layouts.
            for root in roots[:2]:
                scanned = 0
                try:
                    for current, dirs, files in os.walk(root):
                        scanned += 1
                        if scanned > 1800:
                            break
                        # Avoid huge irrelevant trees.
                        dirs[:] = [
                            d for d in dirs
                            if d.lower() not in {
                                "cache", "logs", "log", "temp", "tmp",
                                "screenshots", "download", "downloads"
                            }
                        ]
                        if "adb.exe" in {f.lower() for f in files}:
                            for f in files:
                                if f.lower() == "adb.exe":
                                    add(Path(current) / f)
                                    return
                except Exception:
                    pass

        for pdir in process_dirs:
            add_nearby_adb(pdir)

        # 5. Infer install folder from Windows uninstall registry.
        if os.name == "nt":
            try:
                import winreg
                registry_roots = [
                    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                ]
                for hive, key_path in registry_roots:
                    try:
                        with winreg.OpenKey(hive, key_path) as root_key:
                            count = winreg.QueryInfoKey(root_key)[0]
                            for i in range(count):
                                try:
                                    sub_name = winreg.EnumKey(root_key, i)
                                    with winreg.OpenKey(root_key, sub_name) as sub:
                                        try:
                                            display, _ = winreg.QueryValueEx(sub, "DisplayName")
                                        except OSError:
                                            display = ""
                                        if "mumu" not in str(display).lower():
                                            continue
                                        install = ""
                                        try:
                                            install, _ = winreg.QueryValueEx(sub, "InstallLocation")
                                        except OSError:
                                            pass
                                        if install:
                                            add_nearby_adb(Path(str(install)))
                                        try:
                                            icon, _ = winreg.QueryValueEx(sub, "DisplayIcon")
                                            if icon:
                                                icon_path = Path(str(icon).split(",")[0].strip().strip('"'))
                                                add_nearby_adb(icon_path.parent)
                                        except OSError:
                                            pass
                                except Exception:
                                    continue
                    except Exception:
                        continue
            except Exception:
                pass

        # Resolve in discovery order.
        for candidate in candidates:
            try:
                p = Path(candidate)
                if p.is_file() and p.name.lower() == "adb.exe":
                    return str(p.resolve())
            except Exception:
                continue

        raise RuntimeError(
            "未找到 MuMu 的 adb.exe。软件已自动检查系统 PATH、C:-H: 常见安装目录、"
            "正在运行的 MuMu 进程路径和 Windows 安装信息。\\n\\n"
            "请先保持 MuMu 模拟器处于打开状态后再点“重新连接 MuMu”。"
            "如果仍失败，可在任务管理器中右键 MuMu → 打开文件所在的位置，"
            "找到 adb.exe 后把其完整路径发给我。"
        )

    def _run(self, args, timeout=15, check=False) -> str:
        cmd = [self.adb]
        if self.serial and args and args[0] != "connect":
            cmd += ["-s", self.serial]
        cmd += list(args)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if check and proc.returncode != 0:
            raise RuntimeError(out.strip() or "ADB 执行失败")
        return out.strip()

    def connect(self) -> str:
        out = self._run(["devices"], timeout=10)
        serials = []
        for line in out.splitlines()[1:]:
            if "\tdevice" in line:
                serials.append(line.split("\t", 1)[0].strip())
        if not serials:
            for port in COMMON_PORTS:
                try:
                    self._run(["connect", f"127.0.0.1:{port}"], timeout=3)
                except Exception:
                    pass
            out = self._run(["devices"], timeout=10)
            for line in out.splitlines()[1:]:
                if "\tdevice" in line:
                    serials.append(line.split("\t", 1)[0].strip())
        if not serials:
            raise RuntimeError("没有检测到在线 MuMu ADB 设备。请先启动 MuMu 模拟器。")
        preferred = [s for s in serials if "127.0.0.1" in s or s.startswith("emulator-")]
        self.serial = (preferred or serials)[0]
        package = self._run(["shell", "pm", "path", DOUYIN_PACKAGE], timeout=10)
        if "package:" not in package:
            raise RuntimeError("MuMu 已连接，但没有检测到抖音 App（com.ss.android.ugc.aweme）。")
        return self.serial

    def launch_douyin(self):
        self._run(
            ["shell", "monkey", "-p", DOUYIN_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1"],
            timeout=15,
        )
        time.sleep(2.0)

    def keyevent(self, code: int):
        self._run(["shell", "input", "keyevent", str(int(code))], timeout=8)

    def tap(self, x: int, y: int):
        self._run(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=8)

    def swipe(self, x1, y1, x2, y2, duration=550):
        self._run(
            ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)],
            timeout=8,
        )

    def dump_xml(self) -> ET.Element:
        path = "/sdcard/dourpa_window.xml"
        self._run(["shell", "uiautomator", "dump", path], timeout=12)
        xml = self._run(["exec-out", "cat", path], timeout=12)
        start = xml.find("<?xml")
        if start >= 0:
            xml = xml[start:]
        return ET.fromstring(xml)

    @staticmethod
    def nodes(root: ET.Element):
        return list(root.iter("node"))

    def find_nodes(self, root, needle: str, *, exact=False):
        needle = str(needle)
        found = []
        for node in self.nodes(root):
            values = [node.attrib.get("text", ""), node.attrib.get("content-desc", "")]
            if exact:
                ok = any(_clean(v) == needle for v in values)
            else:
                ok = any(needle in _clean(v) for v in values)
            if ok:
                found.append(node)
        return found

    def click_text(self, text: str, *, exact=False) -> bool:
        root = self.dump_xml()
        nodes = self.find_nodes(root, text, exact=exact)
        if not nodes:
            return False
        x, y = _center(nodes[0].attrib.get("bounds", ""))
        if x <= 0 or y <= 0:
            return False
        self.tap(x, y)
        return True

    def paste_text(self, text: str):
        _set_windows_clipboard(text)
        time.sleep(0.35)
        self.keyevent(279)  # KEYCODE_PASTE; MuMu syncs host clipboard when clipboard sharing is enabled.
        time.sleep(0.6)
        root = self.dump_xml()
        visible = " ".join(
            _clean(n.attrib.get("text", "")) for n in self.nodes(root)
        )
        if text not in visible:
            if text.isascii():
                escaped = text.replace(" ", "%s")
                self._run(["shell", "input", "text", escaped], timeout=8)
            else:
                raise RuntimeError(
                    "中文关键词没有粘贴进 MuMu。请在 MuMu 设置中开启“剪贴板同步/共享剪贴板”，然后重试。"
                )

    def status(self) -> str:
        serial = self.connect()
        return f"MuMu 已连接：{serial} · 抖音已安装"


class DouyinMuMuCollector:
    def __init__(self, progress_cb=None, stop_event: threading.Event | None = None):
        self.progress_cb = progress_cb or (lambda *args, **kwargs: None)
        self.stop_event = stop_event or threading.Event()
        self.adb = MuMuAdbController()

    def _emit(self, text: str):
        self.progress_cb(text)

    def _check_stop(self):
        if self.stop_event.is_set():
            raise RuntimeError("采集已停止")

    @staticmethod
    def _node_text(node) -> str:
        return _clean(node.attrib.get("text", "") or node.attrib.get("content-desc", ""))

    @staticmethod
    def _is_noise(text: str) -> bool:
        rejects = (
            "首页", "朋友", "消息", "我", "搜索", "综合", "视频", "用户", "直播", "商品",
            "筛选", "销量", "价格", "店铺", "评价", "分享", "收藏", "客服", "购物车",
            "立即购买", "加购", "选规格", "选择规格", "包装规格", "万人购买", "人购买",
        )
        if not text or len(text) < 6 or len(text) > 90:
            return True
        if text.startswith(("¥", "￥")) or re.fullmatch(r"[\d.,%+/\-]+", text):
            return True
        return text in rejects

    def _click_node(self, node):
        x, y = _center(node.attrib.get("bounds", ""))
        if x and y:
            self.adb.tap(x, y)
            return True
        return False

    def _search(self, keyword: str):
        self.adb.connect()
        self.adb.launch_douyin()
        self._emit("抖音：已连接 MuMu，准备搜索")

        root = self.adb.dump_xml()
        search_nodes = self.adb.find_nodes(root, "搜索")
        if search_nodes:
            self._click_node(search_nodes[0])
        else:
            self.adb.keyevent(84)  # Android search key
        time.sleep(1.0)

        root = self.adb.dump_xml()
        edits = [n for n in self.adb.nodes(root) if "EditText" in n.attrib.get("class", "")]
        if edits:
            self._click_node(edits[0])
        self.adb.paste_text(keyword)
        self.adb.keyevent(66)  # enter/search
        time.sleep(2.5)

        # Prefer the actual 商品 tab when the result page exposes it.
        try:
            if self.adb.click_text("商品", exact=True):
                time.sleep(1.8)
        except Exception:
            pass

    def _candidate_result_nodes(self, root):
        candidates = []
        for node in self.adb.nodes(root):
            text = self._node_text(node)
            if self._is_noise(text):
                continue
            x1, y1, x2, y2 = _bounds(node.attrib.get("bounds", ""))
            if y1 < 120 or y1 > 1750 or x2 - x1 < 50:
                continue
            candidates.append((y1, x1, node, text))
        candidates.sort(key=lambda x: (x[0], x[1]))
        unique = []
        seen = set()
        for _, _, node, text in candidates:
            key = text[:60]
            if key in seen:
                continue
            seen.add(key)
            unique.append((node, text))
        return unique

    def _extract_sku_titles(self, root, product_title: str) -> list[str]:
        nodes = self.adb.nodes(root)
        priced = []
        for node in nodes:
            text = self._node_text(node)
            if text.startswith(("¥", "￥")):
                x, y = _center(node.attrib.get("bounds", ""))
                priced.append((x, y))

        candidates = []
        for node in nodes:
            text = self._node_text(node)
            if not text or text == product_title or len(text) < 6 or len(text) > 100:
                continue
            if any(x in text for x in ("万人购买", "人购买", "包装规格", "大图", "¥", "￥")):
                continue
            x, y = _center(node.attrib.get("bounds", ""))
            if not x or not y:
                continue
            if priced and any(abs(py - y) <= 42 and px >= x for px, py in priced):
                candidates.append(text)

        # Fallback: texts under 包装规格 when card-price pairing isn't exposed by accessibility.
        if not candidates:
            headers = [n for n in nodes if "包装规格" in self._node_text(n)]
            if headers:
                _, hy = _center(headers[0].attrib.get("bounds", ""))
                for node in nodes:
                    text = self._node_text(node)
                    _, y = _center(node.attrib.get("bounds", ""))
                    if y <= hy or y > hy + 620:
                        continue
                    if not text or text == product_title or len(text) < 6 or len(text) > 100:
                        continue
                    if any(x in text for x in ("万人购买", "人购买", "包装规格", "大图", "¥", "￥")):
                        continue
                    candidates.append(text)

        result = []
        for text in candidates:
            if text not in result:
                result.append(text)
        return result[:30]

    def _copy_share_link(self) -> str:
        sentinel = "DOURPA_CLIPBOARD_WAITING"
        try:
            _set_windows_clipboard(sentinel)
        except Exception:
            pass

        root = self.adb.dump_xml()
        share_nodes = self.adb.find_nodes(root, "分享")
        if not share_nodes:
            return ""
        self._click_node(share_nodes[0])
        time.sleep(0.8)

        if not self.adb.click_text("复制链接"):
            # Some Douyin versions expose “复制口令/复制链接”.
            if not self.adb.click_text("复制"):
                self.adb.keyevent(4)
                return ""
        time.sleep(0.8)

        for _ in range(6):
            clip = _get_windows_clipboard().strip()
            match = re.search(r'https?://[^\s]+', clip)
            if match and sentinel not in clip:
                return match.group(0).rstrip("，。；;)")
            time.sleep(0.4)
        return ""

    def _collect_detail(self, title_hint: str) -> dict:
        time.sleep(1.5)
        root = self.adb.dump_xml()

        # Keep the search-result title unless a more complete detail title is obvious.
        title = title_hint
        text_nodes = [self._node_text(n) for n in self.adb.nodes(root)]
        long_texts = [t for t in text_nodes if 8 <= len(t) <= 120 and not self._is_noise(t)]
        if long_texts:
            best = max(long_texts, key=len)
            if len(best) >= len(title_hint):
                title = best

        sku_titles = self._extract_sku_titles(root, title)

        share_url = ""
        image_url = ""
        try:
            share_url = self._copy_share_link()
            if share_url:
                image_url = resolve_douyin_image_url(share_url)
        except Exception as exc:
            self._emit(f"抖音：分享链接/首图URL解析失败：{exc}")

        # Close share panel if needed, then return to search results.
        try:
            self.adb.keyevent(4)
            time.sleep(0.35)
            self.adb.keyevent(4)
            time.sleep(1.0)
        except Exception:
            pass

        return {
            "platform": "抖音",
            "title": title or title_hint,
            "image_url": image_url,
            "sku_titles": sku_titles,
            "source_url": share_url,
        }

    def collect(self, keyword: str, limit: int) -> list[dict]:
        keyword = _clean(keyword)
        limit = max(1, int(limit))
        self._search(keyword)
        results: list[dict] = []
        seen_titles: set[str] = set()

        for scroll_index in range(40):
            self._check_stop()
            root = self.adb.dump_xml()
            candidates = self._candidate_result_nodes(root)
            clicked = False
            for node, title in candidates:
                self._check_stop()
                key = title[:70]
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                if not self._click_node(node):
                    continue
                clicked = True
                self._emit(f"抖音：采集 {len(results) + 1}/{limit} · {title[:24]}")
                try:
                    result = self._collect_detail(title)
                    results.append(result)
                except Exception as exc:
                    self._emit(f"抖音：商品采集失败：{exc}")
                    try:
                        self.adb.keyevent(4)
                        time.sleep(0.8)
                    except Exception:
                        pass
                if len(results) >= limit:
                    return results
                # Refresh list after returning; old node coordinates may be stale.
                break

            if len(results) >= limit:
                break
            if not clicked:
                self.adb.swipe(540, 1500, 540, 620, 650)
                time.sleep(1.0)
            else:
                # Move a little so the next cycle is less likely to reopen the same card.
                self.adb.swipe(540, 1450, 540, 900, 450)
                time.sleep(0.7)

        return results
