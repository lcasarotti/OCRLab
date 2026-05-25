"""Internationalization support for OCRLab.

Usage:
  from app.i18n import _

  # In run.py, before creating any UI:
  from app.i18n import set_language
  set_language("it")

Translation files live in locale/<lang>.json next to this package's root.
"""

import json
import os
import sys


def _resolve_locale_dir() -> str:
    # PyInstaller onedir/.app bundle: data files extracted under sys._MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = os.path.join(meipass, "locale")
        if os.path.isdir(bundled):
            return bundled
    # Dev mode: locale/ sits next to the app package.
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "locale")


_LOCALE_DIR = _resolve_locale_dir()

_translations: dict[str, str] = {}
_current_lang: str = "en"

AVAILABLE_LANGUAGES: dict[str, str] = {
    "en": "English",
    "it": "Italiano",
}


def detect_system_language() -> str:
    """Rileva la lingua di sistema e restituisce un codice supportato, o 'en' come fallback."""
    import subprocess

    # macOS: usa `defaults read -g AppleLanguages` (unico metodo affidabile nei bundle .app)
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True, text=True, timeout=2,
            )
            # Output: (\n    "it-IT",\n    "en-US",\n    ...\n)
            for token in r.stdout.split():
                code = token.strip('(",)').split("-")[0].lower()
                if code in AVAILABLE_LANGUAGES:
                    return code
        except Exception:
            pass

    # Fallback: variabile LANG / locale Python
    import locale as _locale
    try:
        lang_code = _locale.getlocale()[0] or ""
        prefix = lang_code.split("_")[0].lower()
        if prefix in AVAILABLE_LANGUAGES:
            return prefix
    except Exception:
        pass
    lang_env = os.environ.get("LANG", "")
    prefix = lang_env.split("_")[0].lower()
    if prefix in AVAILABLE_LANGUAGES:
        return prefix
    return "en"


def get_language() -> str:
    return _current_lang


def set_language(lang: str) -> None:
    global _translations, _current_lang
    if lang not in AVAILABLE_LANGUAGES:
        lang = "en"
    _current_lang = lang
    path = os.path.join(_LOCALE_DIR, f"{lang}.json")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            _translations = json.load(f)
    else:
        _translations = {}


def _(text: str) -> str:
    """Return the translated string, or the original if no translation exists."""
    return _translations.get(text, text)
