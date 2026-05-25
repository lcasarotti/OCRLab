"""Dialog per il download delle lingue aggiuntive di Tesseract OCR."""

import os
import threading
import urllib.request
import urllib.error

import wx

from app.config import get_lang_name_map
from app.engine.tesseract_setup import USER_TESSDATA_DIR
from app.i18n import _

_TESSDATA_URL = (
    "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{code}.traineddata"
)

# Lingue già bundled nell'app: non si possono rimuovere
_BUNDLED_LANGS = frozenset({"ita", "eng"})

# Escludi dalla lista: non sono lingue reali
_EXCLUDED = frozenset({"osd", "equ"})


def _installed_langs() -> set[str]:
    """Restituisce i codici delle lingue già presenti in USER_TESSDATA_DIR."""
    if not os.path.isdir(USER_TESSDATA_DIR):
        return set()
    return {
        f[:-12]  # rimuove ".traineddata"
        for f in os.listdir(USER_TESSDATA_DIR)
        if f.endswith(".traineddata")
    }


class TesseractLangDialog(wx.Dialog):
    """Dialog per visualizzare, scaricare e rimuovere le lingue Tesseract."""

    def __init__(self, parent):
        super().__init__(
            parent, title=_("Tesseract OCR Languages"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            size=(520, 540),
        )
        self._build_ui()
        self._populate()

    def _build_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            self,
            label=_("Select languages to download or remove.\n"
                    "Checked languages are already installed."),
        )
        sizer.Add(intro, 0, wx.ALL, 12)

        self.checklist = wx.CheckListBox(self, style=wx.LB_SORT)
        sizer.Add(self.checklist, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        self.log = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
            size=(-1, 90),
        )
        self.log.SetFont(wx.Font(
            10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL,
        ))
        sizer.Add(self.log, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        self.progress = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        sizer.Add(self.progress, 0, wx.EXPAND | wx.ALL, 12)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_download = wx.Button(self, label=_("Download selected"))
        self.btn_remove = wx.Button(self, label=_("Remove selected"))
        self.btn_close = wx.Button(self, wx.ID_CLOSE, _("Close"))
        btn_sizer.Add(self.btn_download, 0, wx.RIGHT, 8)
        btn_sizer.Add(self.btn_remove, 0, wx.RIGHT, 8)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(self.btn_close, 0)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(sizer)

        self.btn_download.Bind(wx.EVT_BUTTON, self._on_download)
        self.btn_remove.Bind(wx.EVT_BUTTON, self._on_remove)
        self.btn_close.Bind(wx.EVT_BUTTON, lambda _: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))

    def _populate(self):
        installed = _installed_langs()
        # Costruisce lista (nome_visualizzato, codice) per tutte le lingue tranne escluse
        lang_map = get_lang_name_map()
        entries = [
            (name, code)
            for code, name in lang_map.items()
            if code not in _EXCLUDED
        ]
        # Ordina per nome
        entries.sort(key=lambda x: x[0])

        self._codes = []  # parallelo agli item della checklist
        for name, code in entries:
            label = name if code not in installed else _("{name} (installed)").format(name=name)
            idx = self.checklist.Append(label)
            self._codes.append(code)
            if code in installed:
                self.checklist.Check(idx, True)

    def _log(self, text: str):
        wx.CallAfter(self.log.AppendText, text)

    def _set_busy(self, busy: bool):
        wx.CallAfter(self.btn_download.Enable, not busy)
        wx.CallAfter(self.btn_remove.Enable, not busy)
        wx.CallAfter(self.btn_close.Enable, not busy)

    def _selected_codes(self) -> list[str]:
        return [
            self._codes[i]
            for i in range(self.checklist.GetCount())
            if self.checklist.IsSelected(i)
        ]

    # ---- Download ----

    def _on_download(self, _event):
        codes = self._selected_codes()
        if not codes:
            wx.MessageBox(
                _("Select at least one language from the list."),
                _("No selection"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        already = _installed_langs()
        to_download = [c for c in codes if c not in already]
        if not to_download:
            wx.MessageBox(
                _("All selected languages are already installed."),
                _("Already installed"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        self._set_busy(True)
        self.log.Clear()
        threading.Thread(
            target=self._download_thread, args=(to_download,), daemon=True
        ).start()

    def _download_thread(self, codes: list[str]):
        os.makedirs(USER_TESSDATA_DIR, exist_ok=True)
        lang_map = get_lang_name_map()
        total = len(codes)
        for i, code in enumerate(codes):
            name = lang_map.get(code, code)
            self._log(f"Scarico {name} ({code})...\n")
            url = _TESSDATA_URL.format(code=code)
            dest = os.path.join(USER_TESSDATA_DIR, f"{code}.traineddata")
            try:
                def _reporthook(block, block_size, total_size):
                    if total_size > 0:
                        pct = min(int(block * block_size * 100 / total_size), 100)
                        wx.CallAfter(self.progress.SetValue, pct)
                    else:
                        wx.CallAfter(self.progress.Pulse)

                urllib.request.urlretrieve(url, dest, reporthook=_reporthook)
                self._log(f"  OK\n")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self._log(f"  Non disponibile in tessdata_fast.\n")
                else:
                    self._log(f"  Errore HTTP {e.code}.\n")
                if os.path.isfile(dest):
                    os.remove(dest)
            except Exception as e:
                self._log(f"  Errore: {e}\n")
                if os.path.isfile(dest):
                    os.remove(dest)

            wx.CallAfter(self.progress.SetValue, int((i + 1) * 100 / total))

        self._log(_("Done. Restart OCRLab to use the new languages.\n"))
        self._set_busy(False)
        wx.CallAfter(self._refresh_list)

    # ---- Rimozione ----

    def _on_remove(self, _event):
        codes = self._selected_codes()
        if not codes:
            wx.MessageBox(
                _("Select at least one language from the list."),
                _("No selection"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        lang_map = get_lang_name_map()
        installed = _installed_langs()
        removable = [c for c in codes if c in installed and c not in _BUNDLED_LANGS]
        protected = [c for c in codes if c in installed and c in _BUNDLED_LANGS]

        if protected:
            names = ", ".join(lang_map.get(c, c) for c in protected)
            wx.MessageBox(
                _("The following languages are bundled with the app and cannot be removed:\n{names}").format(names=names),
                _("Protected language"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
        if not removable:
            return

        names = ", ".join(lang_map.get(c, c) for c in removable)
        answer = wx.MessageBox(
            _("Remove the following languages?\n{names}").format(names=names),
            _("Confirm removal"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            self,
        )
        if answer != wx.YES:
            return

        self.log.Clear()
        for code in removable:
            name = lang_map.get(code, code)
            dest = os.path.join(USER_TESSDATA_DIR, f"{code}.traineddata")
            try:
                os.remove(dest)
                self._log(_("Removed: {name}\n").format(name=name))
            except Exception as e:
                self._log(_("Error removing {name}: {e}\n").format(name=name, e=e))
        self._refresh_list()

    # ---- Aggiornamento lista ----

    def _refresh_list(self):
        lang_map = get_lang_name_map()
        installed = _installed_langs()
        for i, code in enumerate(self._codes):
            name = lang_map.get(code, code)
            label = _("{name} (installed)").format(name=name) if code in installed else name
            self.checklist.SetString(i, label)
            self.checklist.Check(i, code in installed)
