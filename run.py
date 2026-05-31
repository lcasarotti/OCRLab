"""Entry point per OCR Lab."""

import wx

from app.config import load_config
from app import i18n


def main():
    config = load_config()
    lang = config.get("ui_language", "auto")
    if lang == "auto":
        lang = i18n.detect_system_language()
    i18n.set_language(lang)

    from app.main_frame import MainFrame
    from app.engine.tesseract_setup import ensure_tesseract
    from app.engine.surya_installer import ensure_surya
    from app.engine.surya20_installer import ensure_surya20

    app = wx.App()
    frame = MainFrame()
    frame.Show()

    def _startup_checks():
        ensure_tesseract(frame)
        ensure_surya(frame)
        if config.get("ocr_engine") == "surya20":
            ensure_surya20(frame)

    wx.CallAfter(_startup_checks)

    app.MainLoop()


if __name__ == "__main__":
    main()
