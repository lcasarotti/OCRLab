"""Smoke test del MainFrame su Mac.

Lancia il frame, attende 2 secondi e chiude. Salta `ensure_tesseract` (che
aprirebbe un dialog modale bloccante quando Tesseract non e' installato).

Eseguire con:
    cd ~/projects/OCRLab
    .venv/bin/python mac-port-test/smoke_boot.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wx

from app.main_frame import MainFrame


class SmokeApp(wx.App):
    def OnInit(self):
        self.frame = MainFrame()
        self.frame.Show()
        # Esce automaticamente dopo 2 secondi
        wx.CallLater(2000, self._exit)
        return True

    def _exit(self):
        print("Boot OK: frame mostrato e chiuso senza errori.")
        self.frame.Close()


def main():
    app = SmokeApp()
    app.MainLoop()


if __name__ == "__main__":
    main()
