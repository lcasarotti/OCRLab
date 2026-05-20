"""Tab Impostazioni: configurazione OCR, LLM e chunking."""

import re
import sys
import threading

import wx
import requests

from app.config import load_config, save_config, OCR_LANGUAGES, LANG_CODE_TO_NAME, LANG_NAME_TO_CODE, ENABLE_CHANDRA
from app.speech import announce

# Motori OCR disponibili. Windows OCR e' solo su Windows; Chandra e' condizionale.
_IS_WINDOWS = sys.platform == "win32"

_OCR_ENGINE_KEYS = (
    ["tesseract", "vlm"]
    + (["windows"] if _IS_WINDOWS else [])
    + ["surya"]
    + (["chandra"] if ENABLE_CHANDRA else [])
)
_OCR_ENGINE_LABELS = (
    ["Tesseract", "Ollama Vision"]
    + (["Windows OCR"] if _IS_WINDOWS else [])
    + ["Surya"]
    + (["Chandra"] if ENABLE_CHANDRA else [])
)
_ENGINE_TO_IDX = {k: i for i, k in enumerate(_OCR_ENGINE_KEYS)}
_IDX_TO_ENGINE = {i: k for i, k in enumerate(_OCR_ENGINE_KEYS)}

# Modelli Gemini disponibili
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]


class SettingsPanel(wx.Panel):
    def __init__(self, parent, main_frame):
        super().__init__(parent)
        self.main_frame = main_frame
        self.config = load_config()

        self._build_ui()
        self._load_values()

    def _build_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # ---- Selettore sezione ----
        self.rb_settings_section = wx.RadioBox(
            self,
            label="Mostra impostazioni",
            choices=["Acquisizione", "Correzione"],
            majorDimension=2,
            style=wx.RA_SPECIFY_COLS,
        )
        sizer.Add(self.rb_settings_section, 0, wx.EXPAND | wx.ALL, 5)

        # ---- Sezione OCR ----
        self._ocr_box = wx.StaticBox(self, label="OCR")
        self._ocr_sizer = wx.StaticBoxSizer(self._ocr_box, wx.VERTICAL)

        # Motore OCR (prima di tutto, così NVDA lo incontra per primo)
        self.rb_ocr_engine = wx.RadioBox(
            self,
            label="Motore OCR",
            choices=_OCR_ENGINE_LABELS,
            majorDimension=len(_OCR_ENGINE_LABELS),
            style=wx.RA_SPECIFY_COLS,
        )
        self._ocr_sizer.Add(self.rb_ocr_engine, 0, wx.EXPAND | wx.ALL, 5)

        # Path Tesseract
        self.row_tesseract_path = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_tesseract_path = wx.StaticText(self, label="Path Tesseract:")
        self.row_tesseract_path.Add(self.lbl_tesseract_path, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_tesseract_path = wx.TextCtrl(self, size=(400, -1))
        self.row_tesseract_path.Add(self.txt_tesseract_path, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_browse_tesseract = wx.Button(self, label="Sfoglia...")
        self.row_tesseract_path.Add(self.btn_browse_tesseract, 0)
        self._ocr_sizer.Add(self.row_tesseract_path, 0, wx.EXPAND | wx.ALL, 5)

        # Lingua predefinita
        self.row_lang = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_ocr_lang = wx.StaticText(self, label="Lingua predefinita OCR:")
        self.row_lang.Add(self.lbl_ocr_lang, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_ocr_lang = wx.ComboBox(self, choices=[name for _, name in OCR_LANGUAGES], style=wx.CB_DROPDOWN)
        self.row_lang.Add(self.cmb_ocr_lang, 0, wx.RIGHT, 5)
        self.btn_refresh_langs = wx.Button(self, label="Aggiorna lingue")
        self.row_lang.Add(self.btn_refresh_langs, 0)
        self._ocr_sizer.Add(self.row_lang, 0, wx.EXPAND | wx.ALL, 5)

        # Modello VLM
        self.row_vlm = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vlm_model = wx.StaticText(self, label="Modello VLM:")
        self.row_vlm.Add(self.lbl_vlm_model, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_vlm_model = wx.ComboBox(self, choices=[], style=wx.CB_DROPDOWN, size=(300, -1))
        self.row_vlm.Add(self.cmb_vlm_model, 1, wx.EXPAND)
        self._ocr_sizer.Add(self.row_vlm, 0, wx.EXPAND | wx.ALL, 5)

        # Pulsanti modelli VLM
        self.row_vlm_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_vlm_local = wx.Button(self, label="Modelli locali")
        self.row_vlm_btns.Add(self.btn_vlm_local, 0, wx.RIGHT, 5)
        self.btn_vlm_library = wx.Button(self, label="Modelli scaricabili")
        self.row_vlm_btns.Add(self.btn_vlm_library, 0, wx.RIGHT, 5)
        self.btn_vlm_cloud = wx.Button(self, label="Modelli cloud")
        self.row_vlm_btns.Add(self.btn_vlm_cloud, 0)
        self._ocr_sizer.Add(self.row_vlm_btns, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        # Cloud Ollama + API key (duplicati nella sezione Acquisizione per uso VLM)
        self.chk_vlm_cloud = wx.CheckBox(self, label="Usa cloud Ollama (per modelli non scaricati localmente)")
        self._ocr_sizer.Add(self.chk_vlm_cloud, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        row_vlm_api = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vlm_api_key = wx.StaticText(self, label="API key Ollama (cloud):")
        row_vlm_api.Add(self.lbl_vlm_api_key, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_vlm_api_key = wx.TextCtrl(self, size=(300, -1), style=wx.TE_PASSWORD)
        row_vlm_api.Add(self.txt_vlm_api_key, 1, wx.EXPAND)
        self._ocr_sizer.Add(row_vlm_api, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        # Lingua Windows OCR
        self.row_winocr = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_winocr_lang = wx.StaticText(self, label="Lingua Windows OCR:")
        self.row_winocr.Add(self.lbl_winocr_lang, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_winocr_lang = wx.ComboBox(self, choices=[], style=wx.CB_READONLY, size=(300, -1))
        self.row_winocr.Add(self.cmb_winocr_lang, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_refresh_winocr = wx.Button(self, label="Aggiorna lingue")
        self.row_winocr.Add(self.btn_refresh_winocr, 0)
        self._ocr_sizer.Add(self.row_winocr, 0, wx.EXPAND | wx.ALL, 5)

        # Surya: percorso Python esterno + nota
        self.row_surya_python = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_surya_python = wx.StaticText(self, label="Python per Surya:")
        self.row_surya_python.Add(self.lbl_surya_python, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_surya_python = wx.TextCtrl(self, size=(380, -1))
        self.row_surya_python.Add(self.txt_surya_python, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_browse_surya_python = wx.Button(self, label="Sfoglia...")
        self.row_surya_python.Add(self.btn_browse_surya_python, 0)
        self._ocr_sizer.Add(self.row_surya_python, 0, wx.EXPAND | wx.ALL, 5)

        self.lbl_surya_note = wx.StaticText(
            self,
            label="Surya rileva automaticamente la lingua (90+ lingue supportate).\n"
                  "Al primo avvio scarica i modelli (~2 GB).\n"
                  "Supporta layout multi-colonna grazie alla reading order detection.",
        )
        self._ocr_sizer.Add(self.lbl_surya_note, 0, wx.LEFT | wx.BOTTOM, 5)

        # Chandra: percorso Python esterno + nota
        self.row_chandra_python = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_chandra_python = wx.StaticText(self, label="Python per Chandra:")
        self.row_chandra_python.Add(self.lbl_chandra_python, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_chandra_python = wx.TextCtrl(self, size=(380, -1))
        self.row_chandra_python.Add(self.txt_chandra_python, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_browse_chandra_python = wx.Button(self, label="Sfoglia...")
        self.row_chandra_python.Add(self.btn_browse_chandra_python, 0)
        self._ocr_sizer.Add(self.row_chandra_python, 0, wx.EXPAND | wx.ALL, 5)

        # Chandra: metodo (vllm / hf)
        self.row_chandra_method = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_chandra_method = wx.StaticText(self, label="Metodo Chandra:")
        self.row_chandra_method.Add(self.lbl_chandra_method, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.rb_chandra_method = wx.RadioBox(
            self,
            label="",
            choices=["vLLM (server WSL, raccomandato)", "HuggingFace (subprocess Windows)"],
            majorDimension=2,
            style=wx.RA_SPECIFY_COLS,
        )
        self.row_chandra_method.Add(self.rb_chandra_method, 0)
        self._ocr_sizer.Add(self.row_chandra_method, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        # URL server vLLM
        self.row_chandra_vllm_url = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_chandra_vllm_url = wx.StaticText(self, label="URL server vLLM:")
        self.row_chandra_vllm_url.Add(self.lbl_chandra_vllm_url, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_chandra_vllm_url = wx.TextCtrl(self, size=(280, -1))
        self.row_chandra_vllm_url.Add(self.txt_chandra_vllm_url, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_detect_wsl_ip = wx.Button(self, label="Rileva IP WSL")
        self.row_chandra_vllm_url.Add(self.btn_detect_wsl_ip, 0)
        self._ocr_sizer.Add(self.row_chandra_vllm_url, 0, wx.EXPAND | wx.ALL, 5)

        # ---- Configurazione server vLLM ----
        # Quantizzazione
        self.rb_vllm_quant = wx.RadioBox(
            self,
            label="Quantizzazione",
            choices=["float16", "fp8  (Blackwell / Ada)", "AWQ / GPTQ"],
            majorDimension=3,
            style=wx.RA_SPECIFY_COLS,
        )
        self._ocr_sizer.Add(self.rb_vllm_quant, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        # Token massimi per pagina
        row_vllm_tok = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_tokens = wx.StaticText(self, label="Token massimi per pagina:")
        row_vllm_tok.Add(self.lbl_vllm_tokens, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_vllm_tokens = wx.SpinCtrl(self, min=512, max=8192, initial=3072)
        self.spin_vllm_tokens.SetIncrement(256)
        row_vllm_tok.Add(self.spin_vllm_tokens, 0)
        self._ocr_sizer.Add(row_vllm_tok, 0, wx.ALL, 5)

        # Utilizzo GPU %
        row_vllm_gpu = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_gpu = wx.StaticText(self, label="Utilizzo GPU (%):")
        row_vllm_gpu.Add(self.lbl_vllm_gpu, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_vllm_gpu = wx.SpinCtrl(self, min=50, max=100, initial=88)
        self.spin_vllm_gpu.SetIncrement(5)
        row_vllm_gpu.Add(self.spin_vllm_gpu, 0)
        self._ocr_sizer.Add(row_vllm_gpu, 0, wx.ALL, 5)

        # Modello HuggingFace
        row_vllm_model = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_hf_model = wx.StaticText(self, label="Modello HuggingFace:")
        row_vllm_model.Add(self.lbl_vllm_hf_model, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_vllm_hf_model = wx.TextCtrl(self, size=(320, -1))
        row_vllm_model.Add(self.txt_vllm_hf_model, 1, wx.EXPAND)
        self._ocr_sizer.Add(row_vllm_model, 0, wx.EXPAND | wx.ALL, 5)

        # Distro WSL
        row_vllm_distro = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_distro = wx.StaticText(self, label="Distro WSL:")
        row_vllm_distro.Add(self.lbl_vllm_distro, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_vllm_distro = wx.ComboBox(self, choices=[], style=wx.CB_DROPDOWN, size=(220, -1))
        row_vllm_distro.Add(self.cmb_vllm_distro, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_detect_distros = wx.Button(self, label="Rileva distro")
        row_vllm_distro.Add(self.btn_detect_distros, 0)
        self._ocr_sizer.Add(row_vllm_distro, 0, wx.EXPAND | wx.ALL, 5)

        # Modalità eager
        self.chk_vllm_eager = wx.CheckBox(
            self, label="Modalità eager (consigliata con VRAM ≤ 8 GB)"
        )
        self._ocr_sizer.Add(self.chk_vllm_eager, 0, wx.ALL, 5)

        # Argomenti aggiuntivi
        row_vllm_extra = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_extra = wx.StaticText(self, label="Argomenti aggiuntivi:")
        row_vllm_extra.Add(self.lbl_vllm_extra, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_vllm_extra = wx.TextCtrl(self, size=(380, -1))
        row_vllm_extra.Add(self.txt_vllm_extra, 1, wx.EXPAND)
        self._ocr_sizer.Add(row_vllm_extra, 0, wx.EXPAND | wx.ALL, 5)

        # Avvia / Ferma server + stato
        row_vllm_srv = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start_vllm = wx.Button(self, label="Avvia server")
        row_vllm_srv.Add(self.btn_start_vllm, 0, wx.RIGHT, 5)
        self.btn_stop_vllm = wx.Button(self, label="Ferma server")
        self.btn_stop_vllm.Enable(False)
        row_vllm_srv.Add(self.btn_stop_vllm, 0, wx.RIGHT, 5)
        self.btn_vllm_log = wx.Button(self, label="Mostra log")
        row_vllm_srv.Add(self.btn_vllm_log, 0, wx.RIGHT, 10)
        self.lbl_vllm_status = wx.StaticText(self, label="Server non avviato.")
        row_vllm_srv.Add(self.lbl_vllm_status, 0, wx.ALIGN_CENTER_VERTICAL)
        self._ocr_sizer.Add(row_vllm_srv, 0, wx.ALL, 5)

        self.lbl_chandra_note = wx.StaticText(
            self,
            label="Chandra 2 (Datalab): modello VLM state-of-the-art per documenti complessi.\n"
                  "Supporta 90+ lingue, tabelle, manoscritti e layout multi-colonna.\n"
                  "vLLM: avvia il server con 'bash /root/vllm/start_chandra.sh' in WSL.\n"
                  "HF Windows: pip install chandra-ocr[hf] nel venv indicato sotto.",
        )
        self._ocr_sizer.Add(self.lbl_chandra_note, 0, wx.LEFT | wx.BOTTOM, 5)

        # Opzione post-processing: unisci parole spezzate da trattino
        self.chk_join_hyphenated = wx.CheckBox(
            self, label="Unisci parole spezzate da trattino a fine riga"
        )
        self._ocr_sizer.Add(self.chk_join_hyphenated, 0, wx.ALL, 5)

        sizer.Add(self._ocr_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # ---- Sezione LLM ----
        self._llm_box = wx.StaticBox(self, label="Motore correzione LLM")
        self._llm_sizer = wx.StaticBoxSizer(self._llm_box, wx.VERTICAL)

        self.rb_provider = wx.RadioBox(
            self,
            label="Motore",
            choices=["Ollama (locale o cloud)", "Gemini (cloud)"],
            majorDimension=2,
            style=wx.RA_SPECIFY_COLS,
        )
        self._llm_sizer.Add(self.rb_provider, 0, wx.EXPAND | wx.ALL, 5)

        # Ollama
        self.ollama_panel = wx.Panel(self)
        ollama_panel = self.ollama_panel
        ol_sizer = wx.BoxSizer(wx.VERTICAL)

        row3 = wx.BoxSizer(wx.HORIZONTAL)
        row3.Add(wx.StaticText(ollama_panel, label="URL server Ollama:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_ollama_url = wx.TextCtrl(ollama_panel, size=(300, -1))
        row3.Add(self.txt_ollama_url, 1, wx.EXPAND)
        ol_sizer.Add(row3, 0, wx.EXPAND | wx.ALL, 3)

        row4 = wx.BoxSizer(wx.HORIZONTAL)
        row4.Add(wx.StaticText(ollama_panel, label="Modello Ollama:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_ollama_model = wx.ComboBox(ollama_panel, choices=[], style=wx.CB_DROPDOWN)
        row4.Add(self.cmb_ollama_model, 1, wx.EXPAND)
        ol_sizer.Add(row4, 0, wx.EXPAND | wx.ALL, 3)

        row4b = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refresh_ollama = wx.Button(ollama_panel, label="Modelli locali")
        row4b.Add(self.btn_refresh_ollama, 0, wx.RIGHT, 5)
        self.btn_library_ollama = wx.Button(ollama_panel, label="Modelli scaricabili")
        row4b.Add(self.btn_library_ollama, 0, wx.RIGHT, 5)
        self.btn_remote_ollama = wx.Button(ollama_panel, label="Modelli cloud")
        row4b.Add(self.btn_remote_ollama, 0)
        ol_sizer.Add(row4b, 0, wx.EXPAND | wx.ALL, 3)

        self.chk_ollama_cloud = wx.CheckBox(ollama_panel, label="Usa cloud Ollama (per modelli non scaricati localmente)")
        ol_sizer.Add(self.chk_ollama_cloud, 0, wx.ALL, 3)

        row_api = wx.BoxSizer(wx.HORIZONTAL)
        row_api.Add(wx.StaticText(ollama_panel, label="API key Ollama (cloud):"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_ollama_api_key = wx.TextCtrl(ollama_panel, size=(300, -1), style=wx.TE_PASSWORD)
        row_api.Add(self.txt_ollama_api_key, 1, wx.EXPAND)
        ol_sizer.Add(row_api, 0, wx.EXPAND | wx.ALL, 3)

        ollama_panel.SetSizer(ol_sizer)
        self._llm_sizer.Add(ollama_panel, 0, wx.EXPAND | wx.LEFT, 10)

        # Gemini
        self.gemini_panel = wx.Panel(self)
        gemini_panel = self.gemini_panel
        ge_sizer = wx.BoxSizer(wx.VERTICAL)

        row5 = wx.BoxSizer(wx.HORIZONTAL)
        row5.Add(wx.StaticText(gemini_panel, label="API key Gemini:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_gemini_key = wx.TextCtrl(gemini_panel, size=(350, -1), style=wx.TE_PASSWORD)
        row5.Add(self.txt_gemini_key, 1, wx.EXPAND)
        ge_sizer.Add(row5, 0, wx.EXPAND | wx.ALL, 3)

        row6 = wx.BoxSizer(wx.HORIZONTAL)
        row6.Add(wx.StaticText(gemini_panel, label="Modello Gemini:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_gemini_model = wx.ComboBox(gemini_panel, choices=GEMINI_MODELS, style=wx.CB_DROPDOWN)
        row6.Add(self.cmb_gemini_model, 1, wx.EXPAND)
        ge_sizer.Add(row6, 0, wx.EXPAND | wx.ALL, 3)

        gemini_panel.SetSizer(ge_sizer)
        self._llm_sizer.Add(gemini_panel, 0, wx.EXPAND | wx.LEFT, 10)

        sizer.Add(self._llm_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # ---- Sezione Chunking ----
        self._chunk_box = wx.StaticBox(self, label="Chunking")
        self._chunk_sizer = wx.StaticBoxSizer(self._chunk_box, wx.VERTICAL)

        row7 = wx.BoxSizer(wx.HORIZONTAL)
        row7.Add(wx.StaticText(self, label="Dimensione chunk (token):"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_chunk_size = wx.SpinCtrl(self, min=500, max=8000, initial=2000)
        row7.Add(self.spin_chunk_size, 0)
        self._chunk_sizer.Add(row7, 0, wx.EXPAND | wx.ALL, 5)

        row8 = wx.BoxSizer(wx.HORIZONTAL)
        row8.Add(wx.StaticText(self, label="Overlap (token):"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_overlap = wx.SpinCtrl(self, min=0, max=1000, initial=200)
        row8.Add(self.spin_overlap, 0)
        self._chunk_sizer.Add(row8, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(self._chunk_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # ---- Pulsante Salva ----
        self.btn_save = wx.Button(self, label="Salva impostazioni")
        sizer.Add(self.btn_save, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 10)

        self.SetSizer(sizer)

        # ---- Bind ----
        self.btn_browse_tesseract.Bind(wx.EVT_BUTTON, self._on_browse_tesseract)
        self.btn_browse_surya_python.Bind(wx.EVT_BUTTON, self._on_browse_surya_python)
        self.btn_browse_chandra_python.Bind(wx.EVT_BUTTON, self._on_browse_chandra_python)
        self.rb_chandra_method.Bind(wx.EVT_RADIOBOX, self._on_ocr_engine_changed)
        self.btn_detect_wsl_ip.Bind(wx.EVT_BUTTON, self._on_detect_wsl_ip)
        self.btn_detect_distros.Bind(wx.EVT_BUTTON, self._on_detect_distros)
        self.btn_start_vllm.Bind(wx.EVT_BUTTON, self._on_start_vllm)
        self.btn_stop_vllm.Bind(wx.EVT_BUTTON, self._on_stop_vllm)
        self.btn_vllm_log.Bind(wx.EVT_BUTTON, self._on_show_vllm_log)
        self.btn_refresh_langs.Bind(wx.EVT_BUTTON, self._on_refresh_langs)
        self.btn_refresh_ollama.Bind(wx.EVT_BUTTON, self._on_refresh_ollama)
        self.btn_library_ollama.Bind(wx.EVT_BUTTON, self._on_library_ollama)
        self.btn_remote_ollama.Bind(wx.EVT_BUTTON, self._on_remote_ollama)
        self.btn_save.Bind(wx.EVT_BUTTON, self._on_save)
        # Sincronizzazione bidirezionale cloud/api-key tra sezione Acquisizione e Correzione
        self.chk_vlm_cloud.Bind(wx.EVT_CHECKBOX, self._on_vlm_cloud_toggled)
        self.chk_ollama_cloud.Bind(wx.EVT_CHECKBOX, self._on_ollama_cloud_toggled)
        self.txt_vlm_api_key.Bind(wx.EVT_TEXT, self._on_vlm_api_key_changed)
        self.txt_ollama_api_key.Bind(wx.EVT_TEXT, self._on_ollama_api_key_changed)
        self.rb_ocr_engine.Bind(wx.EVT_RADIOBOX, self._on_ocr_engine_changed)
        self.btn_refresh_winocr.Bind(wx.EVT_BUTTON, self._on_refresh_winocr)
        self.btn_vlm_local.Bind(wx.EVT_BUTTON, self._on_vlm_local)
        self.btn_vlm_library.Bind(wx.EVT_BUTTON, self._on_vlm_library)
        self.btn_vlm_cloud.Bind(wx.EVT_BUTTON, self._on_vlm_cloud)
        self.rb_provider.Bind(wx.EVT_RADIOBOX, self._on_provider_changed)
        self.rb_settings_section.Bind(wx.EVT_RADIOBOX, lambda _: self._update_section_visibility())

    def _on_provider_changed(self, _event):
        """Mostra/nasconde i controlli in base al provider LLM selezionato."""
        self._update_provider_visibility()

    def _update_provider_visibility(self):
        """Aggiorna la visibilità dei pannelli Ollama/Gemini."""
        is_ollama = self.rb_provider.GetSelection() == 0
        self.ollama_panel.Show(is_ollama)
        self.gemini_panel.Show(not is_ollama)
        self.Layout()

    def _update_section_visibility(self):
        """Mostra/nasconde le sezioni Acquisizione/Correzione in base alla selezione."""
        is_acq = self.rb_settings_section.GetSelection() == 0
        self._ocr_sizer.ShowItems(is_acq)
        self._llm_sizer.ShowItems(not is_acq)
        self._chunk_sizer.ShowItems(not is_acq)
        if is_acq:
            self._update_ocr_engine_visibility()
        else:
            self._update_provider_visibility()
        self.Layout()

    def _on_ocr_engine_changed(self, _event):
        """Mostra/nasconde i controlli in base al motore OCR selezionato."""
        self._update_ocr_engine_visibility()

    def _update_ocr_engine_visibility(self):
        """Aggiorna la visibilità dei controlli OCR in base al motore selezionato."""
        sel = self.rb_ocr_engine.GetSelection()
        is_tesseract = sel == _ENGINE_TO_IDX.get("tesseract", -1)
        is_vlm = sel == _ENGINE_TO_IDX.get("vlm", -1)
        is_windows = sel == _ENGINE_TO_IDX.get("windows", -1)
        is_surya = sel == _ENGINE_TO_IDX.get("surya", -1)
        is_chandra = ENABLE_CHANDRA and sel == _ENGINE_TO_IDX.get("chandra", -1)
        # Controlli Tesseract
        self.lbl_tesseract_path.Show(is_tesseract)
        self.txt_tesseract_path.Show(is_tesseract)
        self.btn_browse_tesseract.Show(is_tesseract)
        self.lbl_ocr_lang.Show(is_tesseract)
        self.cmb_ocr_lang.Show(is_tesseract)
        self.btn_refresh_langs.Show(is_tesseract)
        # Controlli VLM
        self.lbl_vlm_model.Show(is_vlm)
        self.cmb_vlm_model.Show(is_vlm)
        self.btn_vlm_local.Show(is_vlm)
        self.btn_vlm_library.Show(is_vlm)
        self.btn_vlm_cloud.Show(is_vlm)
        self.chk_vlm_cloud.Show(is_vlm)
        self.lbl_vlm_api_key.Show(is_vlm)
        self.txt_vlm_api_key.Show(is_vlm)
        # Controlli Windows OCR
        self.lbl_winocr_lang.Show(is_windows)
        self.cmb_winocr_lang.Show(is_windows)
        self.btn_refresh_winocr.Show(is_windows)
        # Controlli Surya
        self.lbl_surya_python.Show(is_surya)
        self.txt_surya_python.Show(is_surya)
        self.btn_browse_surya_python.Show(is_surya)
        self.lbl_surya_note.Show(is_surya)
        # Controlli Chandra
        self.lbl_chandra_method.Show(is_chandra)
        self.rb_chandra_method.Show(is_chandra)
        self.lbl_chandra_note.Show(is_chandra)
        chandra_is_vllm = is_chandra and self.rb_chandra_method.GetSelection() == 0
        chandra_is_hf = is_chandra and self.rb_chandra_method.GetSelection() == 1
        self.lbl_chandra_vllm_url.Show(chandra_is_vllm)
        self.txt_chandra_vllm_url.Show(chandra_is_vllm)
        self.btn_detect_wsl_ip.Show(chandra_is_vllm)
        self.lbl_chandra_python.Show(chandra_is_hf)
        self.txt_chandra_python.Show(chandra_is_hf)
        self.btn_browse_chandra_python.Show(chandra_is_hf)
        # Configurazione server vLLM
        for ctrl in (
            self.rb_vllm_quant,
            self.lbl_vllm_tokens, self.spin_vllm_tokens,
            self.lbl_vllm_gpu, self.spin_vllm_gpu,
            self.lbl_vllm_hf_model, self.txt_vllm_hf_model,
            self.lbl_vllm_distro, self.cmb_vllm_distro, self.btn_detect_distros,
            self.chk_vllm_eager,
            self.lbl_vllm_extra, self.txt_vllm_extra,
            self.btn_start_vllm, self.btn_stop_vllm, self.btn_vllm_log, self.lbl_vllm_status,
        ):
            ctrl.Show(chandra_is_vllm)
        self.Layout()

    def _load_winocr_langs_bg(self):
        """Carica silenziosamente le lingue Windows OCR al primo avvio."""
        try:
            from app.engine.windows_ocr_engine import get_available_languages
            langs = get_available_languages()
            wx.CallAfter(self._update_winocr_langs, langs)
        except Exception:
            pass  # Silenzioso: winrt potrebbe non essere installato

    def _on_refresh_winocr(self, _event):
        """Carica le lingue OCR disponibili in Windows."""
        self.main_frame.set_status("Caricamento lingue Windows OCR...")
        self._speak("Caricamento lingue Windows OCR.")

        def _fetch():
            try:
                from app.engine.windows_ocr_engine import get_available_languages
                langs = get_available_languages()  # [(tag, display_name), ...]
                wx.CallAfter(self._update_winocr_langs, langs)
            except ImportError as e:
                wx.CallAfter(wx.MessageBox, str(e), "Errore", wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, "Pacchetti winrt non installati.")
            except Exception as e:
                wx.CallAfter(wx.MessageBox, f"Errore: {e}", "Errore", wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, "Errore caricamento lingue Windows OCR.")

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_winocr_langs(self, langs: list[tuple[str, str]]):
        """Aggiorna la combobox lingue Windows OCR."""
        if not langs:
            wx.MessageBox(
                "Nessuna lingua OCR installata.\n"
                "Installa un pacchetto lingua da:\n"
                "Impostazioni → Ora e lingua → Lingua → Aggiungi una lingua.",
                "Attenzione", wx.OK | wx.ICON_WARNING,
            )
            self.main_frame.set_status("Nessuna lingua Windows OCR installata.")
            return

        # Mostra "Nome lingua (tag)"
        choices = [f"{name} ({tag})" for tag, name in langs]
        self._winocr_lang_tags = [tag for tag, _ in langs]
        self.cmb_winocr_lang.Set(choices)

        # Seleziona la lingua già configurata se presente
        saved_tag = self.config.get("windows_ocr_lang", "it-IT")
        if saved_tag in self._winocr_lang_tags:
            idx = self._winocr_lang_tags.index(saved_tag)
            self.cmb_winocr_lang.SetSelection(idx)
        else:
            self.cmb_winocr_lang.SetSelection(0)

        self.main_frame.set_status(f"Trovate {len(langs)} lingue Windows OCR.")
        self._speak(f"Trovate {len(langs)} lingue Windows OCR.")

    def _get_winocr_tag(self) -> str:
        """Restituisce il tag BCP-47 della lingua Windows OCR selezionata."""
        idx = self.cmb_winocr_lang.GetSelection()
        if hasattr(self, "_winocr_lang_tags") and 0 <= idx < len(self._winocr_lang_tags):
            return self._winocr_lang_tags[idx]
        return self.config.get("windows_ocr_lang", "it-IT")

    def _update_vlm_models(self, models, label="modelli VLM"):
        self.cmb_vlm_model.Set(models)
        if models:
            self.cmb_vlm_model.SetValue(models[0])
        self.main_frame.set_status(f"Trovati {len(models)} {label}.")
        self._speak(f"Trovati {len(models)} {label}.")
        self.cmb_vlm_model.SetFocus()

    def _on_vlm_local(self, _event):
        """Carica i modelli locali Ollama per il VLM."""
        url = self.txt_ollama_url.GetValue().rstrip("/")
        self.main_frame.set_status("Caricamento modelli locali per VLM...")
        self._speak("Caricamento modelli locali per VLM.")

        def _fetch():
            try:
                resp = requests.get(f"{url}/api/tags", timeout=10)
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                wx.CallAfter(self._update_vlm_models, models, "modelli locali")
            except Exception as e:
                wx.CallAfter(wx.MessageBox, f"Errore connessione Ollama: {e}", "Errore", wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, "Errore connessione Ollama.")

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_vlm_library(self, _event):
        """Carica i modelli scaricabili dalla libreria Ollama per il VLM."""
        self.main_frame.set_status("Caricamento modelli scaricabili per VLM...")
        self._speak("Caricamento modelli scaricabili per VLM.")

        def _fetch():
            try:
                resp = requests.get("https://ollama.com/library", timeout=15)
                resp.raise_for_status()
                html = resp.text
                names = re.findall(r'href="/library/([^"]+)"', html)
                seen = set()
                unique = []
                for n in names:
                    if n not in seen:
                        seen.add(n)
                        unique.append(n)
                cloud_models = set()
                for m in re.findall(r'href="/library/([^"]+)"[^>]*>.*?</a>.*?>\s*cloud\s*<', html, re.DOTALL | re.IGNORECASE):
                    cloud_models.add(m)
                models = [n for n in unique if n not in cloud_models]
                if models:
                    wx.CallAfter(self._update_vlm_models, models, "modelli scaricabili")
                else:
                    wx.CallAfter(wx.MessageBox, "Nessun modello scaricabile trovato.", "Attenzione", wx.OK | wx.ICON_WARNING)
                    wx.CallAfter(self.main_frame.set_status, "Nessun modello scaricabile trovato.")
            except Exception as e:
                wx.CallAfter(wx.MessageBox, f"Errore nel recupero dei modelli scaricabili:\n{e}", "Errore", wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, "Errore recupero modelli scaricabili.")

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_vlm_cloud(self, _event):
        """Carica i modelli cloud Ollama per il VLM."""
        self.main_frame.set_status("Caricamento modelli cloud per VLM...")
        self._speak("Caricamento modelli cloud per VLM.")

        def _fetch():
            try:
                resp = requests.get("https://ollama.com/api/tags", timeout=15)
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                if models:
                    wx.CallAfter(self._update_vlm_models, models, "modelli cloud")
                else:
                    wx.CallAfter(wx.MessageBox, "Nessun modello trovato nel cloud Ollama.", "Attenzione", wx.OK | wx.ICON_WARNING)
                    wx.CallAfter(self.main_frame.set_status, "Nessun modello trovato.")
            except Exception as e:
                wx.CallAfter(wx.MessageBox, f"Errore nel recupero dei modelli cloud Ollama:\n{e}", "Errore", wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, "Errore recupero modelli cloud.")

        threading.Thread(target=_fetch, daemon=True).start()

    def _load_values(self):
        """Popola i controlli dalla configurazione caricata."""
        self.txt_tesseract_path.SetValue(self.config.get("tesseract_path", ""))
        saved_lang = self.config.get("ocr_lang", "ita")
        self.cmb_ocr_lang.SetValue(LANG_CODE_TO_NAME.get(saved_lang, saved_lang))

        ocr_engine = self.config.get("ocr_engine", "tesseract")
        sel = _ENGINE_TO_IDX.get(ocr_engine, 0)
        self.rb_ocr_engine.SetSelection(sel)
        self.cmb_vlm_model.SetValue(self.config.get("vlm_model", ""))
        self.txt_surya_python.SetValue(self.config.get("surya_python", ""))
        self.txt_chandra_python.SetValue(self.config.get("chandra_python", ""))
        chandra_method = self.config.get("chandra_method", "vllm")
        self.rb_chandra_method.SetSelection(0 if chandra_method == "vllm" else 1)
        self.txt_chandra_vllm_url.SetValue(self.config.get("chandra_vllm_url", "http://localhost:8000"))
        # Configurazione server vLLM
        _quant_map = {"float16": 0, "fp8": 1, "awq": 2}
        self.rb_vllm_quant.SetSelection(
            _quant_map.get(self.config.get("vllm_quantization", "fp8"), 1)
        )
        self.spin_vllm_tokens.SetValue(self.config.get("vllm_max_tokens", 3072))
        self.spin_vllm_gpu.SetValue(self.config.get("vllm_gpu_memory", 88))
        self.txt_vllm_hf_model.SetValue(
            self.config.get("vllm_hf_model", "datalab-to/chandra-ocr-2")
        )
        self.cmb_vllm_distro.SetValue(self.config.get("vllm_wsl_distro", ""))
        self.chk_vllm_eager.SetValue(self.config.get("vllm_enforce_eager", True))
        self.txt_vllm_extra.SetValue(self.config.get("vllm_extra_args", "--max-num-seqs 1"))
        self.chk_join_hyphenated.SetValue(self.config.get("join_hyphenated", False))
        # Carica lingue Windows OCR in background e aggiorna la combobox
        self._winocr_lang_tags = []
        threading.Thread(target=self._load_winocr_langs_bg, daemon=True).start()
        provider = self.config.get("llm_provider", "ollama")
        self.rb_provider.SetSelection(0 if provider == "ollama" else 1)

        self.txt_ollama_url.SetValue(self.config.get("ollama_url", "http://localhost:11434"))

        self.cmb_ollama_model.SetValue(self.config.get("ollama_model", ""))
        self.chk_ollama_cloud.SetValue(self.config.get("ollama_cloud", False))
        self.txt_ollama_api_key.SetValue(self.config.get("ollama_api_key", ""))
        self.chk_vlm_cloud.SetValue(self.config.get("ollama_cloud", False))
        self.txt_vlm_api_key.SetValue(self.config.get("ollama_api_key", ""))
        self.txt_gemini_key.SetValue(self.config.get("gemini_api_key", ""))
        self.cmb_gemini_model.SetValue(self.config.get("gemini_model", "gemini-2.0-flash"))

        self.spin_chunk_size.SetValue(self.config.get("chunk_size", 2000))
        self.spin_overlap.SetValue(self.config.get("chunk_overlap", 200))

        self._update_section_visibility()

    @staticmethod
    def _clean_model_name(name: str) -> str:
        """Rimuove eventuali suffissi di visualizzazione dal nome del modello."""
        return re.sub(r"\s*\(cloud\)$", "", name)

    def _speak(self, text: str):
        """Annuncia testo (eventi di background). Vedi app/speech.py."""
        announce(text)

    def _on_vlm_cloud_toggled(self, _event):
        """Sincronizza chk_ollama_cloud quando cambia chk_vlm_cloud."""
        self.chk_ollama_cloud.SetValue(self.chk_vlm_cloud.IsChecked())

    def _on_ollama_cloud_toggled(self, _event):
        """Sincronizza chk_vlm_cloud quando cambia chk_ollama_cloud."""
        self.chk_vlm_cloud.SetValue(self.chk_ollama_cloud.IsChecked())

    def _on_vlm_api_key_changed(self, _event):
        """Sincronizza txt_ollama_api_key quando cambia txt_vlm_api_key."""
        val = self.txt_vlm_api_key.GetValue()
        if self.txt_ollama_api_key.GetValue() != val:
            self.txt_ollama_api_key.ChangeValue(val)

    def _on_ollama_api_key_changed(self, _event):
        """Sincronizza txt_vlm_api_key quando cambia txt_ollama_api_key."""
        val = self.txt_ollama_api_key.GetValue()
        if self.txt_vlm_api_key.GetValue() != val:
            self.txt_vlm_api_key.ChangeValue(val)

    def _on_browse_tesseract(self, _event):
        dlg = wx.FileDialog(
            self,
            "Seleziona tesseract.exe",
            wildcard="Eseguibili (*.exe)|*.exe",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_tesseract_path.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_browse_surya_python(self, _event):
        dlg = wx.FileDialog(
            self,
            "Seleziona python.exe del venv con Surya/PyTorch",
            wildcard="Eseguibili (*.exe)|*.exe",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_surya_python.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_detect_wsl_ip(self, _event):
        """Rileva automaticamente l'IP di WSL e aggiorna l'URL del server vLLM."""
        import subprocess
        self._speak("Rilevamento IP WSL in corso.")
        try:
            # Esegue hostname -I dentro WSL per ottenere l'IP corrente
            result = subprocess.run(
                ["wsl.exe", "-e", "hostname", "-I"],
                capture_output=True, text=True, timeout=10,
            )
            ip = result.stdout.strip().split()[0] if result.stdout.strip() else ""
            if ip:
                url = f"http://{ip}:8000"
                self.txt_chandra_vllm_url.SetValue(url)
                self.main_frame.set_status(f"IP WSL rilevato: {ip}")
                self._speak(f"IP WSL: {ip}")
            else:
                wx.MessageBox(
                    "Impossibile rilevare l'IP di WSL.\n"
                    "Inserisci manualmente l'URL del server vLLM.",
                    "Attenzione", wx.OK | wx.ICON_WARNING,
                )
        except Exception as e:
            wx.MessageBox(
                f"Errore nel rilevamento IP WSL:\n{e}",
                "Errore", wx.OK | wx.ICON_ERROR,
            )

    def _on_detect_distros(self, _event):
        """Rileva le distro WSL disponibili e popola la combobox."""
        from app.engine import vllm_server
        self._speak("Rilevamento distro WSL in corso.")
        distros = vllm_server.list_wsl_distros()
        if distros:
            self.cmb_vllm_distro.Set(distros)
            saved = self.config.get("vllm_wsl_distro", "")
            if saved in distros:
                self.cmb_vllm_distro.SetValue(saved)
            else:
                self.cmb_vllm_distro.SetValue(distros[0])
            self.main_frame.set_status(f"Trovate {len(distros)} distro WSL.")
            self._speak(f"Trovate {len(distros)} distro WSL.")
        else:
            wx.MessageBox(
                "Nessuna distro WSL trovata.\n"
                "Verifica che WSL2 sia installato e attivo.",
                "Attenzione", wx.OK | wx.ICON_WARNING,
            )

    def _on_start_vllm(self, _event):
        """Avvia il server vLLM in WSL e attende che sia pronto."""
        from app.engine import vllm_server

        config = self.get_config()
        url = config.get("chandra_vllm_url", "http://localhost:8000")

        self.btn_start_vllm.Enable(False)
        self.btn_stop_vllm.Enable(False)
        self.lbl_vllm_status.SetLabel("Avvio in corso...")
        self.main_frame.set_status("Avvio server vLLM...")
        self._speak("Avvio server vLLM in corso.")

        def _run():
            try:
                vllm_server.start(config)

                def _tick(elapsed):
                    mins, secs = divmod(elapsed, 60)
                    label = f"Caricamento modello... {mins}m {secs:02d}s"
                    wx.CallAfter(self.lbl_vllm_status.SetLabel, label)

                ready = vllm_server.wait_ready(url, timeout=300, on_tick=_tick)
                if ready:
                    wx.CallAfter(self._vllm_ready)
                else:
                    msg = (
                        "Timeout: server non risponde."
                        if vllm_server.is_alive()
                        else "Il processo si è fermato inaspettatamente."
                    )
                    wx.CallAfter(self._vllm_failed, msg)
            except Exception as e:
                wx.CallAfter(self._vllm_failed, str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _vllm_ready(self):
        self.btn_start_vllm.Enable(False)
        self.btn_stop_vllm.Enable(True)
        self.lbl_vllm_status.SetLabel("Server pronto.")
        self.main_frame.set_status("Server vLLM pronto.")
        self._speak("Server vLLM pronto.")

    def _vllm_failed(self, msg: str):
        from app.engine import vllm_server
        self.btn_start_vllm.Enable(True)
        self.btn_stop_vllm.Enable(False)
        self.lbl_vllm_status.SetLabel(f"Errore: {msg}")
        self.main_frame.set_status(f"Errore server vLLM: {msg}")
        self._speak(f"Errore avvio server vLLM: {msg}")
        # Mostra automaticamente il log per facilitare la diagnosi
        log = vllm_server.get_log()
        detail = f"{msg}\n\n--- Log del processo ---\n{log}" if log else msg
        dlg = wx.MessageDialog(self, detail, "Errore avvio server vLLM",
                               wx.OK | wx.ICON_ERROR)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_show_vllm_log(self, _event):
        """Mostra le ultime righe di output del processo vLLM."""
        from app.engine import vllm_server
        log = vllm_server.get_log()
        text = log if log else "(nessun output disponibile)"
        dlg = wx.Dialog(self, title="Log server vLLM", size=(700, 500))
        sizer = wx.BoxSizer(wx.VERTICAL)
        txt = wx.TextCtrl(dlg, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        txt.SetValue(text)
        # Scorri all'ultima riga
        txt.SetInsertionPointEnd()
        sizer.Add(txt, 1, wx.EXPAND | wx.ALL, 8)
        btn_ok = wx.Button(dlg, wx.ID_OK, "Chiudi")
        sizer.Add(btn_ok, 0, wx.ALIGN_CENTER | wx.BOTTOM, 8)
        dlg.SetSizer(sizer)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_stop_vllm(self, _event):
        """Ferma il server vLLM."""
        from app.engine import vllm_server
        vllm_server.stop()
        self.btn_start_vllm.Enable(True)
        self.btn_stop_vllm.Enable(False)
        self.lbl_vllm_status.SetLabel("Server fermato.")
        self.main_frame.set_status("Server vLLM fermato.")
        self._speak("Server vLLM fermato.")

    def _on_browse_chandra_python(self, _event):
        dlg = wx.FileDialog(
            self,
            "Seleziona python.exe del venv con Chandra installato",
            wildcard="Eseguibili (*.exe)|*.exe",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_chandra_python.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_refresh_langs(self, _event):
        """Interroga Tesseract per le lingue disponibili."""
        from app.engine.tesseract_setup import get_tesseract_cmd
        import subprocess

        cmd = get_tesseract_cmd(self.txt_tesseract_path.GetValue())
        if not cmd:
            wx.MessageBox("Tesseract non trovato.", "Errore", wx.OK | wx.ICON_ERROR)
            return
        try:
            result = subprocess.run(
                [cmd, "--list-langs"],
                capture_output=True, text=True, timeout=10,
            )
            langs = [l.strip() for l in result.stdout.strip().split("\n")[1:] if l.strip()]
            if langs:
                # Mostra il nome esteso dove disponibile, altrimenti il codice
                display = [LANG_CODE_TO_NAME.get(l, l) for l in langs]
                self.cmb_ocr_lang.Set(display)
                self.cmb_ocr_lang.SetValue(display[0] if display else "")
                self.main_frame.set_status(f"Trovate {len(langs)} lingue.")
        except Exception as e:
            wx.MessageBox(f"Errore: {e}", "Errore", wx.OK | wx.ICON_ERROR)

    def _on_refresh_ollama(self, _event):
        """Interroga Ollama per i modelli disponibili."""
        url = self.txt_ollama_url.GetValue().rstrip("/")
        self.main_frame.set_status("Caricamento modelli Ollama...")

        def _fetch():
            try:
                resp = requests.get(f"{url}/api/tags", timeout=10)
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                wx.CallAfter(self._update_ollama_models, models)
            except Exception as e:
                wx.CallAfter(wx.MessageBox, f"Errore connessione Ollama: {e}", "Errore", wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, "Errore connessione Ollama.")

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_ollama_models(self, models, label="modelli Ollama"):
        self.cmb_ollama_model.Set(models)
        if models:
            self.cmb_ollama_model.SetValue(models[0])
        self.main_frame.set_status(f"Trovati {len(models)} {label}.")
        self._speak(f"Trovati {len(models)} {label}.")
        self.cmb_ollama_model.SetFocus()

    def _on_library_ollama(self, _event):
        """Recupera i modelli scaricabili dalla libreria Ollama (esclusi cloud-only)."""
        self.main_frame.set_status("Caricamento modelli scaricabili Ollama...")
        self._speak("Caricamento modelli scaricabili Ollama.")

        def _fetch():
            try:
                resp = requests.get("https://ollama.com/library", timeout=15)
                resp.raise_for_status()
                html = resp.text
                # Estrai nomi modello dagli href /library/{nome}
                names = re.findall(r'href="/library/([^"]+)"', html)
                # Rimuovi duplicati mantenendo l'ordine
                seen = set()
                unique = []
                for n in names:
                    if n not in seen:
                        seen.add(n)
                        unique.append(n)
                # Filtra modelli cloud-only: cerca pattern dove il link è seguito da badge cloud
                cloud_models = set()
                for m in re.findall(r'href="/library/([^"]+)"[^>]*>.*?</a>.*?>\s*cloud\s*<', html, re.DOTALL | re.IGNORECASE):
                    cloud_models.add(m)
                models = [n for n in unique if n not in cloud_models]
                if models:
                    wx.CallAfter(self._update_ollama_models, models, "modelli scaricabili")
                else:
                    wx.CallAfter(wx.MessageBox,
                                 "Nessun modello scaricabile trovato.",
                                 "Attenzione", wx.OK | wx.ICON_WARNING)
                    wx.CallAfter(self.main_frame.set_status, "Nessun modello scaricabile trovato.")
            except Exception as e:
                wx.CallAfter(wx.MessageBox,
                             f"Errore nel recupero dei modelli scaricabili:\n{e}",
                             "Errore", wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, "Errore recupero modelli scaricabili.")

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_remote_ollama(self, _event):
        """Recupera la lista dei modelli disponibili dal cloud Ollama."""
        self.main_frame.set_status("Caricamento modelli cloud Ollama...")
        self._speak("Caricamento modelli cloud Ollama.")

        def _fetch():
            try:
                resp = requests.get(
                    "https://ollama.com/api/tags",
                    timeout=15,
                )
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                if models:
                    wx.CallAfter(self._update_ollama_models, models, "modelli cloud")
                else:
                    wx.CallAfter(wx.MessageBox,
                                 "Nessun modello trovato nel cloud Ollama.",
                                 "Attenzione", wx.OK | wx.ICON_WARNING)
                    wx.CallAfter(self.main_frame.set_status, "Nessun modello trovato.")
            except Exception as e:
                wx.CallAfter(wx.MessageBox,
                             f"Errore nel recupero dei modelli cloud Ollama:\n{e}",
                             "Errore", wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, "Errore recupero modelli cloud.")

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_save(self, _event):
        """Salva le impostazioni correnti in config.json."""
        self.config["tesseract_path"] = self.txt_tesseract_path.GetValue()
        raw_lang = self.cmb_ocr_lang.GetValue()
        self.config["ocr_lang"] = LANG_NAME_TO_CODE.get(raw_lang, raw_lang)
        self.config["ocr_engine"] = _IDX_TO_ENGINE.get(self.rb_ocr_engine.GetSelection(), "tesseract")
        self.config["vlm_model"] = self.cmb_vlm_model.GetValue()
        self.config["surya_python"] = self.txt_surya_python.GetValue()
        self.config["chandra_python"] = self.txt_chandra_python.GetValue()
        self.config["chandra_method"] = "vllm" if self.rb_chandra_method.GetSelection() == 0 else "hf"
        self.config["chandra_vllm_url"] = self.txt_chandra_vllm_url.GetValue()
        _quant_list = ["float16", "fp8", "awq"]
        self.config["vllm_quantization"] = _quant_list[self.rb_vllm_quant.GetSelection()]
        self.config["vllm_max_tokens"] = self.spin_vllm_tokens.GetValue()
        self.config["vllm_gpu_memory"] = self.spin_vllm_gpu.GetValue()
        self.config["vllm_hf_model"] = self.txt_vllm_hf_model.GetValue()
        self.config["vllm_wsl_distro"] = self.cmb_vllm_distro.GetValue()
        self.config["vllm_enforce_eager"] = self.chk_vllm_eager.IsChecked()
        self.config["vllm_extra_args"] = self.txt_vllm_extra.GetValue()
        self.config["join_hyphenated"] = self.chk_join_hyphenated.IsChecked()
        self.config["windows_ocr_lang"] = self._get_winocr_tag()
        self.config["llm_provider"] = "ollama" if self.rb_provider.GetSelection() == 0 else "gemini"
        self.config["ollama_url"] = self.txt_ollama_url.GetValue()
        raw_ollama = self.cmb_ollama_model.GetValue()
        self.config["ollama_model"] = self._clean_model_name(raw_ollama)
        self.config["ollama_cloud"] = self.chk_ollama_cloud.IsChecked()
        self.config["ollama_api_key"] = self.txt_ollama_api_key.GetValue()
        self.config["gemini_api_key"] = self.txt_gemini_key.GetValue()
        self.config["gemini_model"] = self.cmb_gemini_model.GetValue()
        self.config["chunk_size"] = self.spin_chunk_size.GetValue()
        self.config["chunk_overlap"] = self.spin_overlap.GetValue()

        save_config(self.config)
        self.main_frame.set_status("Impostazioni salvate.")
        try:
            import accessible_output2.outputs.auto as ao
            output = ao.Auto()
            output.speak("Impostazioni salvate.")
        except Exception:
            pass

    def get_config(self) -> dict:
        """Restituisce la configurazione corrente (aggiornata dai controlli)."""
        raw_model = self.cmb_ollama_model.GetValue()
        return {
            "tesseract_path": self.txt_tesseract_path.GetValue(),
            "ocr_lang": LANG_NAME_TO_CODE.get(self.cmb_ocr_lang.GetValue(), self.cmb_ocr_lang.GetValue()),
            "ocr_engine": _IDX_TO_ENGINE.get(self.rb_ocr_engine.GetSelection(), "tesseract"),
            "vlm_model": self.cmb_vlm_model.GetValue(),
            "surya_python": self.txt_surya_python.GetValue(),
            "chandra_python": self.txt_chandra_python.GetValue(),
            "chandra_method": "vllm" if self.rb_chandra_method.GetSelection() == 0 else "hf",
            "chandra_vllm_url": self.txt_chandra_vllm_url.GetValue(),
            "vllm_quantization": ["float16", "fp8", "awq"][self.rb_vllm_quant.GetSelection()],
            "vllm_max_tokens": self.spin_vllm_tokens.GetValue(),
            "vllm_gpu_memory": self.spin_vllm_gpu.GetValue(),
            "vllm_hf_model": self.txt_vllm_hf_model.GetValue(),
            "vllm_wsl_distro": self.cmb_vllm_distro.GetValue(),
            "vllm_enforce_eager": self.chk_vllm_eager.IsChecked(),
            "vllm_extra_args": self.txt_vllm_extra.GetValue(),
            "join_hyphenated": self.chk_join_hyphenated.IsChecked(),
            "windows_ocr_lang": self._get_winocr_tag(),
            "llm_provider": "ollama" if self.rb_provider.GetSelection() == 0 else "gemini",
            "ollama_url": self.txt_ollama_url.GetValue(),
            "ollama_model": self._clean_model_name(raw_model),
            "ollama_cloud": self.chk_ollama_cloud.IsChecked(),
            "ollama_api_key": self.txt_ollama_api_key.GetValue(),
            "gemini_api_key": self.txt_gemini_key.GetValue(),
            "gemini_model": self.cmb_gemini_model.GetValue(),
            "chunk_size": self.spin_chunk_size.GetValue(),
            "chunk_overlap": self.spin_overlap.GetValue(),
        }
