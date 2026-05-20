"""Entry point per OCR Lab."""

import wx

from app.main_frame import MainFrame
from app.engine.tesseract_setup import ensure_tesseract


def main():
    app = wx.App()
    frame = MainFrame()
    frame.Show()

    # Controlla la disponibilità di Tesseract dopo che la finestra è visibile
    wx.CallAfter(ensure_tesseract, frame)

    app.MainLoop()


if __name__ == "__main__":
    main()
