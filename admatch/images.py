"""Downloads and caches offer images (plain HTTP download, no AI service)."""
from __future__ import annotations
import hashlib
import shutil
import urllib.request
from pathlib import Path

from PIL import Image


class ImageStore:
    def __init__(self, cache_dir: Path | str, timeout: int = 20, download: bool = True):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.download = download
        self.failed: dict[str, str] = {}

    def get(self, url: str | None) -> Path | None:
        """Local file for an image URL (or local path). None if there is no usable image."""
        if not url:
            return None
        if url in self.failed:
            return None
        dest = self.dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".png")
        if dest.exists():
            return dest
        try:
            local = Path(url[7:] if url.startswith("file://") else url)
            if not url.lower().startswith(("http://", "https://")):
                if not local.exists():
                    raise FileNotFoundError(str(local))
                src = local
            else:
                if not self.download:
                    raise RuntimeError("download switched off in config")
                tmp = dest.with_suffix(".tmp")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ad-match tool)"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp, open(tmp, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
                src = tmp
            with Image.open(src) as im:               # checks it really is a picture; stored as PNG
                im.convert("RGB").save(dest, "PNG")
            if src != local:
                src.unlink(missing_ok=True)
            return dest
        except Exception as e:                        # a missing image never stops the run
            self.failed[url] = f"{type(e).__name__}: {e}"
            return None
