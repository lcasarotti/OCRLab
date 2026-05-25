"""Controllo aggiornamenti via GitHub Releases API."""

import threading
from dataclasses import dataclass
from typing import Callable

import urllib.request
import urllib.error
import json

_RELEASES_API = "https://api.github.com/repos/lcasarotti/OCRLab/releases/latest"
_TIMEOUT = 8


def _parse_version(tag: str) -> tuple[int, ...]:
    """Converte "v1.2.3" o "1.2.3" in (1, 2, 3)."""
    tag = tag.lstrip("v")
    try:
        return tuple(int(x) for x in tag.split("."))
    except ValueError:
        return (0,)


@dataclass
class ReleaseInfo:
    tag: str
    url: str
    body: str


def fetch_latest_release() -> ReleaseInfo | None:
    """Interroga GitHub e restituisce le info dell'ultima release, o None in caso di errore."""
    try:
        req = urllib.request.Request(
            _RELEASES_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "OCRLab-updater"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return ReleaseInfo(
            tag=data.get("tag_name", ""),
            url=data.get("html_url", ""),
            body=data.get("body", ""),
        )
    except Exception:
        return None


def is_newer(remote_tag: str, current_version: str) -> bool:
    return _parse_version(remote_tag) > _parse_version(current_version)


def check_for_updates_async(
    current_version: str,
    on_update_found: Callable[[ReleaseInfo], None],
    on_no_update: Callable[[], None] | None = None,
    on_error: Callable[[], None] | None = None,
) -> None:
    """Esegue il controllo in un thread daemon. Chiama i callback sul thread wx via wx.CallAfter."""
    import wx

    def _worker():
        info = fetch_latest_release()
        if info is None:
            if on_error:
                wx.CallAfter(on_error)
            return
        if is_newer(info.tag, current_version):
            wx.CallAfter(on_update_found, info)
        else:
            if on_no_update:
                wx.CallAfter(on_no_update)

    threading.Thread(target=_worker, daemon=True).start()
