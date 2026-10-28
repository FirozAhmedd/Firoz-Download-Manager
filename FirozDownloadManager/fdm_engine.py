# -*- coding: utf-8 -*-
"""Firoz Download Manager - download engine (standard library only)."""
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "Firoz Download Manager"
VERSION = "1.0"
DATA_DIR = Path.home() / ".firoz_dm"
CFG_FILE = DATA_DIR / "config.json"
LIST_FILE = DATA_DIR / "downloads.json"
PART_EXT = ".fdmpart"
API_PORT = 9614
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_RETRY = 6
CHUNK = 64 * 1024
MIN_SEG = 512 * 1024  # don't split into pieces smaller than this

CATEGORIES = {
    "Compressed": {"zip", "rar", "7z", "gz", "tar", "bz2", "xz", "iso", "cab"},
    "Documents": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "csv", "odt"},
    "Music": {"mp3", "wav", "flac", "aac", "ogg", "m4a", "wma"},
    "Video": {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "ts"},
    "Programs": {"exe", "msi", "apk", "dmg", "deb", "rpm", "bat", "appx", "msix"},
    "Pictures": {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tif", "tiff"},
}
ALL_EXT = set().union(*CATEGORIES.values())


def category_of(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return "General"


def fmt_size(n):
    if n is None or n < 0:
        return "Unknown"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024


def fmt_time(sec):
    if sec is None or sec < 0 or sec > 8640000:
        return "--"
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m:02d}m" if h else (f"{m}m {s:02d}s" if m else f"{s}s")


def looks_downloadable(text):
    """True if text is a single http(s) URL that points at a file-like path."""
    text = (text or "").strip()
    if "\n" in text or " " in text or not re.match(r"^https?://", text, re.I):
        return False
    path = urllib.parse.urlparse(text).path
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return ext in ALL_EXT


def name_from_response(headers, url):
    cd = headers.get("Content-Disposition", "") or ""
    name = ""
    m = re.search(r"filename\*\s*=\s*[\w-]*'[^']*'([^;]+)", cd, re.I)
    if m:
        name = urllib.parse.unquote(m.group(1).strip().strip('"'))
    else:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
        if m:
            name = m.group(1).strip()
    if not name:
        name = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "download"


def unique_name(folder, name):
    folder = Path(folder)
    stem, suffix = Path(name).stem, Path(name).suffix
    cand, i = name, 1
    while (folder / cand).exists() or (folder / (cand + PART_EXT)).exists():
        cand = f"{stem} ({i}){suffix}"
        i += 1
    return cand


# --------------------------------------------------------------------------
class RateLimiter:
    """Global speed limiter shared by every connection (bytes/second)."""

    def __init__(self):
        self.limit = 0
        self._next = 0.0
        self._lock = threading.Lock()

    def throttle(self, nbytes):
        if self.limit <= 0:
            return
        with self._lock:
            now = time.monotonic()
            self._next = max(self._next, now) + nbytes / self.limit
            delay = self._next - now
        if delay > 0:
            time.sleep(min(delay, 3))


LIMITER = RateLimiter()


class Settings(dict):
    DEFAULTS = {
        "download_dir": str(Path.home() / "Downloads"),
        "connections": 8,
        "max_active": 3,
        "speed_limit_kb": 0,
        "clipboard": True,
        "categorize": True,
    }

    def __init__(self):
        super().__init__(self.DEFAULTS)
        try:
            self.update(json.loads(CFG_FILE.read_text("utf-8")))
        except Exception:
            pass
        LIMITER.limit = int(self["speed_limit_kb"]) * 1024

    def save(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            CFG_FILE.write_text(json.dumps(self, indent=2), "utf-8")
        except Exception:
            pass
        LIMITER.limit = int(self["speed_limit_kb"]) * 1024


# --------------------------------------------------------------------------
class Download:
    PERSIST = ("id", "url", "final_url", "folder", "filename", "size", "status", "error",
               "segments", "resumable", "added", "finished", "auto_cat", "ready", "referer")

    def __init__(self, url, folder, auto_cat=True, referer="", cookie="", filename=""):
        self.id = uuid.uuid4().hex[:10]
        self.url = url
        self.final_url = ""
        self.folder = str(folder)
        self.filename = filename
        self.size = -1
        self.status = "queued"   # queued connecting downloading paused completed error
        self.error = ""
        self.segments = []       # [start, end(inclusive), next_byte_to_fetch]
        self.resumable = False
        self.added = time.time()
        self.finished = 0
        self.auto_cat = auto_cat
        self.ready = False
        self.referer = referer
        self.cookie = cookie     # kept in memory only, never written to disk
        self.speed = 0.0
        self.conns = 8
        self._stop = threading.Event()
        self._thread = None
        self._last = (time.monotonic(), 0)

    # ---- persistence
    def to_dict(self):
        return {k: getattr(self, k) for k in self.PERSIST}

    @classmethod
    def from_dict(cls, d):
        o = cls(d.get("url", ""), d.get("folder", ""))
        for k in cls.PERSIST:
            if k in d:
                setattr(o, k, d[k])
        if o.status in ("connecting", "downloading"):
            o.status = "paused"
        return o

    # ---- info
    @property
    def path(self):
        return Path(self.folder) / self.filename

    @property
    def part(self):
        return Path(self.folder) / (self.filename + PART_EXT)

    @property
    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    @property
    def done(self):
        if self.status == "completed":
            return max(self.size, 0)
        if self.size < 0:
            return self.segments[0][2] if self.segments else 0
        return sum(max(0, min(s[2], s[1] + 1) - s[0]) for s in self.segments)

    @property
    def percent(self):
        if self.status == "completed":
            return 100.0
        return min(100.0, self.done * 100.0 / self.size) if self.size > 0 else 0.0

    @property
    def eta(self):
        if self.status == "downloading" and self.speed > 1 and self.size > 0:
            return (self.size - self.done) / self.speed
        return None

    # ---- control
    def start(self, conns):
        if self.alive:
            return False
        self.conns = conns
        self._stop.clear()
        self.error = ""
        self.status = "connecting"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def pause(self):
        self._stop.set()
        if self.status in ("queued", "connecting", "downloading"):
            self.status = "paused"
        self.speed = 0

    # ---- network helpers
    def _open(self, url, start=None, end=None):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"})
        if self.referer:
            req.add_header("Referer", self.referer)
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        if start is not None:
            req.add_header("Range", f"bytes={start}-{'' if end is None or end < 0 else end}")
        return urllib.request.urlopen(req, timeout=20)

    def _probe(self):
        try:
            resp = self._open(self.url, 0, 0)
        except urllib.error.HTTPError as e:
            if e.code != 416:
                raise
            resp = self._open(self.url)
        with resp:
            self.final_url = resp.geturl()
            total = (resp.headers.get("Content-Range") or "").rsplit("/", 1)[-1]
            if resp.status == 206 and total.isdigit():
                self.size, self.resumable = int(total), True
            else:
                cl = resp.headers.get("Content-Length")
                self.size = int(cl) if cl and cl.isdigit() else -1
                self.resumable = False
            if not self.filename:
                self.filename = name_from_response(resp.headers, self.final_url)

    def _allocate(self):
        base = Path(self.folder)
        if self.auto_cat:
            base = base / category_of(self.filename)
        base.mkdir(parents=True, exist_ok=True)
        self.folder = str(base)
        self.filename = unique_name(base, self.filename)
        size = self.size
        if size == 0:
            self.segments = []
        elif size > 0 and self.resumable:
            n = max(1, min(self.conns, size // MIN_SEG))
            step = size // n
            self.segments = [[i * step, size - 1 if i == n - 1 else (i + 1) * step - 1, i * step]
                             for i in range(n)]
        elif size > 0:
            self.segments = [[0, size - 1, 0]]
        else:
            self.segments = [[0, -1, 0]]
        with open(self.part, "wb") as f:
            if size > 0:
                f.truncate(size)

    def _fail(self, msg):
        self.error = msg
        self.status = "error"
        self.speed = 0
        self._stop.set()

    # ---- worker for one segment
    def _worker(self, seg):
        tries = 0
        while not self._stop.is_set():
            if seg[1] >= 0 and seg[2] > seg[1]:
                return
            try:
                if not self.resumable:
                    seg[2] = 0
                url = self.final_url or self.url
                resp = self._open(url, seg[2], seg[1]) if self.resumable else self._open(url)
                with resp, open(self.part, "r+b", buffering=0) as f:
                    if self.resumable and resp.status != 206:
                        raise IOError("Server ignored the Range request")
                    f.seek(seg[2])
                    while not self._stop.is_set():
                        want = CHUNK if seg[1] < 0 else min(CHUNK, seg[1] - seg[2] + 1)
                        if want <= 0:
                            return
                        data = resp.read(want)
                        if not data:
                            break
                        LIMITER.throttle(len(data))
                        f.write(data)
                        seg[2] += len(data)
                        tries = 0
                if self._stop.is_set():
                    return
                if seg[1] < 0 or seg[2] > seg[1]:
                    return
                raise IOError("Connection closed early")
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404, 410):
                    return self._fail(f"HTTP {e.code} {e.reason}")
                err = f"HTTP {e.code}"
            except Exception as e:  # network error, timeout...
                err = str(e) or e.__class__.__name__
            if self._stop.is_set():
                return
            tries += 1
            if tries > MAX_RETRY:
                return self._fail(err)
            self._stop.wait(min(2 * tries, 10))

    def _tick_speed(self):
        now, done = time.monotonic(), self.done
        t0, d0 = self._last
        if now - t0 >= 1.0:
            inst = max(0, done - d0) / (now - t0)
            self.speed = inst if self.speed == 0 else 0.5 * self.speed + 0.5 * inst
            self._last = (now, done)

    def _run(self):
        try:
            if not self.ready or not self.part.exists():
                if self.ready:  # part file vanished -> start over
                    self.filename = ""
                self._probe()
                self._allocate()
                self.ready = True
            if self._stop.is_set():
                self.status = "paused"
                return
            if self.size == 0:
                return self._finish()
            self.status = "downloading"
            self._last = (time.monotonic(), self.done)
            workers = [threading.Thread(target=self._worker, args=(s,), daemon=True)
                       for s in self.segments if s[1] < 0 or s[2] <= s[1]]
            for w in workers:
                w.start()
            while any(w.is_alive() for w in workers):
                time.sleep(0.3)
                self._tick_speed()
            self.speed = 0
            if self.status == "error":
                return
            if self._stop.is_set():
                self.status = "paused"
                return
            if self.size < 0:
                self.size = self.segments[0][2]
            elif any(s[2] <= s[1] for s in self.segments):
                return self._fail("Download incomplete")
            self._finish()
        except urllib.error.HTTPError as e:
            self._fail(f"HTTP {e.code} {e.reason}")
        except Exception as e:
            self._fail(str(e) or e.__class__.__name__)

    def _finish(self):
        for i in range(10):  # antivirus may hold the file for a moment
            try:
                os.replace(self.part, self.path)
                break
            except OSError:
                if i == 9:
                    return self._fail("Could not rename the finished file")
                time.sleep(0.5)
        self.finished = time.time()
        self.speed = 0
        self.status = "completed"


# --------------------------------------------------------------------------
class Manager:
    def __init__(self, settings):
        self.s = settings
        self.items = []
        self._last_save = 0
        self.incoming = queue.Queue()  # links sent by the browser extension
        try:
            for d in json.loads(LIST_FILE.read_text("utf-8")):
                self.items.append(Download.from_dict(d))
        except Exception:
            pass

    def add(self, urls, folder, auto_cat, start=True, referer="", cookie=""):
        new = []
        for u in urls:
            d = Download(u, folder, auto_cat, referer, cookie)
            d.status = "queued" if start else "paused"
            self.items.append(d)
            new.append(d)
        self.save()
        return new

    def tick(self):
        busy = [d for d in self.items if d.status in ("connecting", "downloading")]
        slots = int(self.s["max_active"]) - len(busy)
        for d in self.items:
            if slots <= 0:
                break
            if d.status == "queued" and d.start(int(self.s["connections"])):
                slots -= 1
        if time.time() - self._last_save > 3:
            self.save()

    def resume(self, d):
        if d.status in ("paused", "error"):
            d.error = ""
            d.status = "queued"

    def pause_all(self):
        for d in self.items:
            if d.status in ("queued", "connecting", "downloading"):
                d.pause()

    def remove(self, d, delete_file=False):
        d.pause()
        if d in self.items:
            self.items.remove(d)
        self.save()

        def cleanup():
            if d._thread:
                d._thread.join(25)
            try:
                if d.part.exists():
                    d.part.unlink()
                if delete_file and d.status == "completed" and d.path.exists():
                    d.path.unlink()
            except OSError:
                pass
        threading.Thread(target=cleanup, daemon=True).start()

    @property
    def busy(self):
        return any(d.status in ("queued", "connecting", "downloading") for d in self.items)

    def save(self):
        self._last_save = time.time()
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = LIST_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps([d.to_dict() for d in self.items]), "utf-8")
            os.replace(tmp, LIST_FILE)
        except Exception:
            pass

    def shutdown(self):
        self.pause_all()
        for d in self.items:
            if d._thread:
                d._thread.join(3)
        self.save()


# --------------------------------------------------------------------------
class BrowserBridge:
    """Tiny local server (127.0.0.1 only) that the browser extension talks to."""

    def __init__(self, manager):
        self.manager = manager
        self.server = None

    def start(self):
        mgr = self.manager

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ok(self, body=b"FDM", code=200):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self._ok(b"", 204)

            def do_GET(self):
                self._ok() if self.path.startswith("/ping") else self._ok(b"", 404)

            def do_POST(self):
                origin = self.headers.get("Origin", "")
                # only the browser extension may talk to us, never a random web page
                if self.headers.get("X-FDM") != "1" or (
                        origin and not origin.startswith(("chrome-extension://", "moz-extension://"))):
                    return self._ok(b"forbidden", 403)
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
                    url = str(data.get("url", ""))
                    if not re.match(r"^https?://", url, re.I):
                        return self._ok(b"bad url", 400)
                    mgr.incoming.put({"url": url, "referer": str(data.get("referrer", "")),
                                      "cookie": str(data.get("cookie", ""))})
                    self._ok(b"queued")
                except Exception:
                    self._ok(b"error", 400)

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", API_PORT), Handler)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            return True
        except OSError:
            return False  # another copy is already running

    def stop(self):
        if self.server:
            self.server.shutdown()
