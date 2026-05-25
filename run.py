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

    app = wx.App()
    frame = MainFrame()
    frame.Show()

    wx.CallAfter(ensure_tesseract, frame)

    app.MainLoop()


if __name__ == "__main__":
    main()
