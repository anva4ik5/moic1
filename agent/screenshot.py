"""Screenshot capture (JPEG bytes)."""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

try:
    import mss
except ImportError:
    mss = None

from PIL import Image

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

log = logging.getLogger("monitor.agent.screenshot")


def take_jpeg(quality: int = 60, max_width: int = 1600) -> Optional[bytes]:
    try:
        if mss:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                raw = sct.grab(monitor)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        elif ImageGrab:
            img = ImageGrab.grab()
        else:
            log.warning("No screenshot library available (mss or PIL.ImageGrab)")
            return None

        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warning("Screenshot failed: %s", exc)
        return None
