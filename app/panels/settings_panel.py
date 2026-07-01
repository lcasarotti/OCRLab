"""Tab Settings: OCR, LLM and chunking configuration."""

import re
import sys
import threading

import wx
import requests

from app.config import (
    load_config, save_config, OCR_LANGUAGES, LANG_NAME_TO_CODE,
    ENABLE_CHANDRA, get_lang_name_map,
)
from app.i18n import _
from app.speech import announce

_IS_WINDOWS = sys.platform == "win32"
_IS_MACOS = sys.platform == "darwin"

_OCR_ENGINE_KEYS = (
    ["tesseract", "vlm"]
    + (["windows"] if _IS_WINDOWS else [])
    + (["apple_vision"] if _IS_MACOS else [])
    + ["surya", "surya20"]
    + (["chandra"] if ENABLE_CHANDRA else [])
)
_OCR_ENGINE_LABELS = (
    ["Tesseract", "Ollama Vision"]
    + (["Windows OCR"] if _IS_WINDOWS else [])
    + (["Apple Vision (macOS)"] if _IS_MACOS else [])
    + ["Surya 0.1", "Surya 0.2 (requires Docker / llama.cpp)"]
    + (["Chandra"] if ENABLE_CHANDRA else [])
)
_ENGINE_TO_IDX = {k: i for i, k in enumerate(_OCR_ENGINE_KEYS)}
_IDX_TO_ENGINE = {i: k for i, k in enumerate(_OCR_ENGINE_KEYS)}

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

        self.rb_settings_section = wx.RadioBox(
            self,
            label=_("Show settings"),
            choices=[_("Acquisition"), _("Correction")],
            majorDimension=2,
            style=wx.RA_SPECIFY_COLS,
        )
        sizer.Add(self.rb_settings_section, 0, wx.EXPAND | wx.ALL, 5)

        # ---- OCR section ----
        self._ocr_box = wx.StaticBox(self, label="OCR")
        self._ocr_sizer = wx.StaticBoxSizer(self._ocr_box, wx.VERTICAL)

        _ocr_labels = (
            ["Tesseract", "Ollama Vision"]
            + (["Windows OCR"] if _IS_WINDOWS else [])
            + (["Apple Vision (macOS)"] if _IS_MACOS else [])
            + ["Surya 0.1", _("Surya 0.2 (requires Docker / llama.cpp)")]
            + (["Chandra"] if ENABLE_CHANDRA else [])
        )
        self.rb_ocr_engine = wx.RadioBox(
            self,
            label=_("OCR Engine"),
            choices=_ocr_labels,
            majorDimension=len(_ocr_labels),
            style=wx.RA_SPECIFY_COLS,
        )
        self._ocr_sizer.Add(self.rb_ocr_engine, 0, wx.EXPAND | wx.ALL, 5)

        self.row_tesseract_path = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_tesseract_path = wx.StaticText(self, label=_("Tesseract path:"))
        self.row_tesseract_path.Add(self.lbl_tesseract_path, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_tesseract_path = wx.TextCtrl(self, size=(400, -1))
        self.row_tesseract_path.Add(self.txt_tesseract_path, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_browse_tesseract = wx.Button(self, label=_("Browse..."))
        self.row_tesseract_path.Add(self.btn_browse_tesseract, 0)
        self._ocr_sizer.Add(self.row_tesseract_path, 0, wx.EXPAND | wx.ALL, 5)

        self.row_lang = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_ocr_lang = wx.StaticText(self, label=_("Default OCR language:"))
        self.row_lang.Add(self.lbl_ocr_lang, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_ocr_lang = wx.ComboBox(
            self,
            choices=[_(name) for _code, name in OCR_LANGUAGES],
            style=wx.CB_DROPDOWN,
        )
        self.row_lang.Add(self.cmb_ocr_lang, 0, wx.RIGHT, 5)
        self.btn_refresh_langs = wx.Button(self, label=_("Update languages"))
        self.row_lang.Add(self.btn_refresh_langs, 0)
        self._ocr_sizer.Add(self.row_lang, 0, wx.EXPAND | wx.ALL, 5)

        self.row_vlm = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vlm_model = wx.StaticText(self, label=_("VLM Model:"))
        self.row_vlm.Add(self.lbl_vlm_model, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_vlm_model = wx.ComboBox(self, choices=[], style=wx.CB_DROPDOWN, size=(300, -1))
        self.row_vlm.Add(self.cmb_vlm_model, 1, wx.EXPAND)
        self._ocr_sizer.Add(self.row_vlm, 0, wx.EXPAND | wx.ALL, 5)

        self.row_vlm_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_vlm_local = wx.Button(self, label=_("Local models"))
        self.row_vlm_btns.Add(self.btn_vlm_local, 0, wx.RIGHT, 5)
        self.btn_vlm_library = wx.Button(self, label=_("Downloadable models"))
        self.row_vlm_btns.Add(self.btn_vlm_library, 0, wx.RIGHT, 5)
        self.btn_vlm_cloud = wx.Button(self, label=_("Cloud models"))
        self.row_vlm_btns.Add(self.btn_vlm_cloud, 0)
        self._ocr_sizer.Add(self.row_vlm_btns, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        self.chk_vlm_cloud = wx.CheckBox(
            self,
            label=_("Use Ollama cloud (for models not downloaded locally)"),
        )
        self._ocr_sizer.Add(self.chk_vlm_cloud, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        row_vlm_api = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vlm_api_key = wx.StaticText(self, label=_("Ollama API key (cloud):"))
        row_vlm_api.Add(self.lbl_vlm_api_key, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_vlm_api_key = wx.TextCtrl(self, size=(300, -1), style=wx.TE_PASSWORD)
        row_vlm_api.Add(self.txt_vlm_api_key, 1, wx.EXPAND)
        self._ocr_sizer.Add(row_vlm_api, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        self.row_winocr = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_winocr_lang = wx.StaticText(self, label=_("Windows OCR language:"))
        self.row_winocr.Add(self.lbl_winocr_lang, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_winocr_lang = wx.ComboBox(self, choices=[], style=wx.CB_READONLY, size=(300, -1))
        self.row_winocr.Add(self.cmb_winocr_lang, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_refresh_winocr = wx.Button(self, label=_("Update languages"))
        self.row_winocr.Add(self.btn_refresh_winocr, 0)
        self._ocr_sizer.Add(self.row_winocr, 0, wx.EXPAND | wx.ALL, 5)

        self.row_surya_python = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_surya_python = wx.StaticText(self, label=_("Python for Surya:"))
        self.row_surya_python.Add(self.lbl_surya_python, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_surya_python = wx.TextCtrl(self, size=(380, -1))
        self.row_surya_python.Add(self.txt_surya_python, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_browse_surya_python = wx.Button(self, label=_("Browse..."))
        self.row_surya_python.Add(self.btn_browse_surya_python, 0)
        self._ocr_sizer.Add(self.row_surya_python, 0, wx.EXPAND | wx.ALL, 5)

        self.lbl_surya_note = wx.StaticText(
            self,
            label=_("Surya auto-detects language (90+ supported).\n"
                    "Models (~2 GB) are downloaded on first run.\n"
                    "Supports multi-column layout via reading order detection."),
        )
        self._ocr_sizer.Add(self.lbl_surya_note, 0, wx.LEFT | wx.BOTTOM, 5)

        self.row_surya20_python = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_surya20_python = wx.StaticText(self, label=_("Python for Surya 0.2:"))
        self.row_surya20_python.Add(self.lbl_surya20_python, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_surya20_python = wx.TextCtrl(self, size=(380, -1))
        self.row_surya20_python.Add(self.txt_surya20_python, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_browse_surya20_python = wx.Button(self, label=_("Browse..."))
        self.row_surya20_python.Add(self.btn_browse_surya20_python, 0)
        self._ocr_sizer.Add(self.row_surya20_python, 0, wx.EXPAND | wx.ALL, 5)

        self.lbl_surya20_note = wx.StaticText(
            self,
            label=_("Surya 0.2 requires Docker Desktop (NVIDIA GPU) or llama.cpp (CPU / Apple Silicon).\n"
                    "The inference backend must be installed separately before running OCR."),
        )
        self._ocr_sizer.Add(self.lbl_surya20_note, 0, wx.LEFT | wx.BOTTOM, 5)

        self.row_surya20_batch = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_surya20_batch = wx.StaticText(
            self, label=_("Pages per batch (Surya 0.2):"))
        self.row_surya20_batch.Add(
            self.lbl_surya20_batch, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_surya20_batch = wx.SpinCtrl(self, min=1, max=64, initial=4, size=(70, -1))
        self.spin_surya20_batch.SetToolTip(
            _("Number of PDF pages processed together in one GPU call. "
              "Higher is faster on large documents but uses more memory."))
        self.row_surya20_batch.Add(self.spin_surya20_batch, 0)
        self._ocr_sizer.Add(self.row_surya20_batch, 0, wx.ALL, 5)

        self.row_surya20_parallel = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_surya20_parallel = wx.StaticText(
            self, label=_("Parallel server slots (Surya 0.2):"))
        self.row_surya20_parallel.Add(
            self.lbl_surya20_parallel, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_surya20_parallel = wx.SpinCtrl(self, min=1, max=64, initial=8, size=(70, -1))
        self.spin_surya20_parallel.SetToolTip(
            _("Concurrent requests handled by the inference server "
              "(SURYA_INFERENCE_PARALLEL). Best set equal to the pages-per-batch "
              "value. More slots need more VRAM; restart the Surya 0.2 server to apply."))
        self.row_surya20_parallel.Add(self.spin_surya20_parallel, 0)
        self._ocr_sizer.Add(self.row_surya20_parallel, 0, wx.ALL, 5)

        self.row_surya20_server = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_surya20_start = wx.Button(self, label=_("Start server"))
        self.row_surya20_server.Add(self.btn_surya20_start, 0, wx.RIGHT, 5)
        self.btn_surya20_stop = wx.Button(self, label=_("Stop server"))
        self.btn_surya20_stop.Enable(False)
        self.row_surya20_server.Add(self.btn_surya20_stop, 0, wx.RIGHT, 10)
        self.lbl_surya20_server_status = wx.StaticText(self, label=_("Server not started."))
        self.row_surya20_server.Add(self.lbl_surya20_server_status, 0, wx.ALIGN_CENTER_VERTICAL)
        self._ocr_sizer.Add(self.row_surya20_server, 0, wx.ALL, 5)

        if _IS_MACOS:
            self.lbl_apple_vision_note = wx.StaticText(
                self,
                label=_("Apple Vision uses the macOS Vision framework (free, local).\n"
                        "Language uses the same combo above.\n"
                        "Requires Xcode Command Line Tools: xcode-select --install"),
            )
            self._ocr_sizer.Add(self.lbl_apple_vision_note, 0, wx.LEFT | wx.BOTTOM, 5)

            self.chk_apple_vision_lang_correction = wx.CheckBox(
                self,
                label=_("Vision language correction "
                        "(disable if Vision truncates last words of line)"),
            )
            self._ocr_sizer.Add(self.chk_apple_vision_lang_correction, 0, wx.ALL, 5)

        self.row_chandra_python = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_chandra_python = wx.StaticText(self, label=_("Python for Chandra:"))
        self.row_chandra_python.Add(self.lbl_chandra_python, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_chandra_python = wx.TextCtrl(self, size=(380, -1))
        self.row_chandra_python.Add(self.txt_chandra_python, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_browse_chandra_python = wx.Button(self, label=_("Browse..."))
        self.row_chandra_python.Add(self.btn_browse_chandra_python, 0)
        self._ocr_sizer.Add(self.row_chandra_python, 0, wx.EXPAND | wx.ALL, 5)

        self.row_chandra_method = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_chandra_method = wx.StaticText(self, label=_("Chandra method:"))
        self.row_chandra_method.Add(self.lbl_chandra_method, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.rb_chandra_method = wx.RadioBox(
            self,
            label="",
            choices=[
                _("vLLM (WSL server, recommended)"),
                _("HuggingFace (subprocess Windows)"),
            ],
            majorDimension=2,
            style=wx.RA_SPECIFY_COLS,
        )
        self.row_chandra_method.Add(self.rb_chandra_method, 0)
        self._ocr_sizer.Add(self.row_chandra_method, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        self.row_chandra_vllm_url = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_chandra_vllm_url = wx.StaticText(self, label=_("vLLM server URL:"))
        self.row_chandra_vllm_url.Add(self.lbl_chandra_vllm_url, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_chandra_vllm_url = wx.TextCtrl(self, size=(280, -1))
        self.row_chandra_vllm_url.Add(self.txt_chandra_vllm_url, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_detect_wsl_ip = wx.Button(self, label=_("Detect WSL IP"))
        self.row_chandra_vllm_url.Add(self.btn_detect_wsl_ip, 0)
        self._ocr_sizer.Add(self.row_chandra_vllm_url, 0, wx.EXPAND | wx.ALL, 5)

        self.rb_vllm_quant = wx.RadioBox(
            self,
            label=_("Quantization"),
            choices=["float16", "fp8  (Blackwell / Ada)", "AWQ / GPTQ"],
            majorDimension=3,
            style=wx.RA_SPECIFY_COLS,
        )
        self._ocr_sizer.Add(self.rb_vllm_quant, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        row_vllm_tok = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_tokens = wx.StaticText(self, label=_("Max tokens per page:"))
        row_vllm_tok.Add(self.lbl_vllm_tokens, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_vllm_tokens = wx.SpinCtrl(self, min=512, max=8192, initial=3072)
        self.spin_vllm_tokens.SetIncrement(256)
        row_vllm_tok.Add(self.spin_vllm_tokens, 0)
        self._ocr_sizer.Add(row_vllm_tok, 0, wx.ALL, 5)

        row_vllm_gpu = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_gpu = wx.StaticText(self, label=_("GPU usage (%):"))
        row_vllm_gpu.Add(self.lbl_vllm_gpu, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_vllm_gpu = wx.SpinCtrl(self, min=50, max=100, initial=88)
        self.spin_vllm_gpu.SetIncrement(5)
        row_vllm_gpu.Add(self.spin_vllm_gpu, 0)
        self._ocr_sizer.Add(row_vllm_gpu, 0, wx.ALL, 5)

        row_vllm_model = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_hf_model = wx.StaticText(self, label=_("HuggingFace model:"))
        row_vllm_model.Add(self.lbl_vllm_hf_model, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_vllm_hf_model = wx.TextCtrl(self, size=(320, -1))
        row_vllm_model.Add(self.txt_vllm_hf_model, 1, wx.EXPAND)
        self._ocr_sizer.Add(row_vllm_model, 0, wx.EXPAND | wx.ALL, 5)

        row_vllm_distro = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_distro = wx.StaticText(self, label=_("WSL distribution:"))
        row_vllm_distro.Add(self.lbl_vllm_distro, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_vllm_distro = wx.ComboBox(self, choices=[], style=wx.CB_DROPDOWN, size=(220, -1))
        row_vllm_distro.Add(self.cmb_vllm_distro, 1, wx.EXPAND | wx.RIGHT, 5)
        self.btn_detect_distros = wx.Button(self, label=_("Detect distribution"))
        row_vllm_distro.Add(self.btn_detect_distros, 0)
        self._ocr_sizer.Add(row_vllm_distro, 0, wx.EXPAND | wx.ALL, 5)

        self.chk_vllm_eager = wx.CheckBox(
            self, label=_("Eager mode (recommended with VRAM ≤ 8 GB)")
        )
        self._ocr_sizer.Add(self.chk_vllm_eager, 0, wx.ALL, 5)

        row_vllm_extra = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_vllm_extra = wx.StaticText(self, label=_("Additional arguments:"))
        row_vllm_extra.Add(self.lbl_vllm_extra, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_vllm_extra = wx.TextCtrl(self, size=(380, -1))
        row_vllm_extra.Add(self.txt_vllm_extra, 1, wx.EXPAND)
        self._ocr_sizer.Add(row_vllm_extra, 0, wx.EXPAND | wx.ALL, 5)

        row_vllm_srv = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start_vllm = wx.Button(self, label=_("Start server"))
        row_vllm_srv.Add(self.btn_start_vllm, 0, wx.RIGHT, 5)
        self.btn_stop_vllm = wx.Button(self, label=_("Stop server"))
        self.btn_stop_vllm.Enable(False)
        row_vllm_srv.Add(self.btn_stop_vllm, 0, wx.RIGHT, 5)
        self.btn_vllm_log = wx.Button(self, label=_("Show log"))
        row_vllm_srv.Add(self.btn_vllm_log, 0, wx.RIGHT, 10)
        self.lbl_vllm_status = wx.StaticText(self, label=_("Server not started."))
        row_vllm_srv.Add(self.lbl_vllm_status, 0, wx.ALIGN_CENTER_VERTICAL)
        self._ocr_sizer.Add(row_vllm_srv, 0, wx.ALL, 5)

        self.lbl_chandra_note = wx.StaticText(
            self,
            label=_("Chandra 2 (Datalab): state-of-the-art VLM model for complex documents.\n"
                    "Supports 90+ languages, tables, manuscripts and multi-column layout.\n"
                    "vLLM: start the server with 'bash /root/vllm/start_chandra.sh' in WSL.\n"
                    "HF Windows: pip install chandra-ocr[hf] in the indicated venv."),
        )
        self._ocr_sizer.Add(self.lbl_chandra_note, 0, wx.LEFT | wx.BOTTOM, 5)

        self.chk_join_hyphenated = wx.CheckBox(
            self, label=_("Join hyphenated words at line end")
        )
        self._ocr_sizer.Add(self.chk_join_hyphenated, 0, wx.ALL, 5)

        sizer.Add(self._ocr_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # ---- LLM section ----
        self._llm_box = wx.StaticBox(self, label=_("LLM correction engine"))
        self._llm_sizer = wx.StaticBoxSizer(self._llm_box, wx.VERTICAL)

        self.rb_provider = wx.RadioBox(
            self,
            label=_("Engine"),
            choices=[_("Ollama (local or cloud)"), _("Gemini (cloud)")],
            majorDimension=2,
            style=wx.RA_SPECIFY_COLS,
        )
        self._llm_sizer.Add(self.rb_provider, 0, wx.EXPAND | wx.ALL, 5)

        # Ollama
        self.ollama_panel = wx.Panel(self)
        ollama_panel = self.ollama_panel
        ol_sizer = wx.BoxSizer(wx.VERTICAL)

        row3 = wx.BoxSizer(wx.HORIZONTAL)
        row3.Add(wx.StaticText(ollama_panel, label=_("Ollama server URL:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_ollama_url = wx.TextCtrl(ollama_panel, size=(300, -1))
        row3.Add(self.txt_ollama_url, 1, wx.EXPAND)
        ol_sizer.Add(row3, 0, wx.EXPAND | wx.ALL, 3)

        row4 = wx.BoxSizer(wx.HORIZONTAL)
        row4.Add(wx.StaticText(ollama_panel, label=_("Ollama model:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_ollama_model = wx.ComboBox(ollama_panel, choices=[], style=wx.CB_DROPDOWN)
        row4.Add(self.cmb_ollama_model, 1, wx.EXPAND)
        ol_sizer.Add(row4, 0, wx.EXPAND | wx.ALL, 3)

        row4b = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refresh_ollama = wx.Button(ollama_panel, label=_("Local models"))
        row4b.Add(self.btn_refresh_ollama, 0, wx.RIGHT, 5)
        self.btn_library_ollama = wx.Button(ollama_panel, label=_("Downloadable models"))
        row4b.Add(self.btn_library_ollama, 0, wx.RIGHT, 5)
        self.btn_remote_ollama = wx.Button(ollama_panel, label=_("Cloud models"))
        row4b.Add(self.btn_remote_ollama, 0)
        ol_sizer.Add(row4b, 0, wx.EXPAND | wx.ALL, 3)

        self.chk_ollama_cloud = wx.CheckBox(
            ollama_panel,
            label=_("Use Ollama cloud (for models not downloaded locally)"),
        )
        ol_sizer.Add(self.chk_ollama_cloud, 0, wx.ALL, 3)

        row_api = wx.BoxSizer(wx.HORIZONTAL)
        row_api.Add(wx.StaticText(ollama_panel, label=_("Ollama API key (cloud):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
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
        row5.Add(wx.StaticText(gemini_panel, label=_("Gemini API key:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.txt_gemini_key = wx.TextCtrl(gemini_panel, size=(350, -1), style=wx.TE_PASSWORD)
        row5.Add(self.txt_gemini_key, 1, wx.EXPAND)
        ge_sizer.Add(row5, 0, wx.EXPAND | wx.ALL, 3)

        row6 = wx.BoxSizer(wx.HORIZONTAL)
        row6.Add(wx.StaticText(gemini_panel, label=_("Gemini model:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.cmb_gemini_model = wx.ComboBox(gemini_panel, choices=GEMINI_MODELS, style=wx.CB_DROPDOWN)
        row6.Add(self.cmb_gemini_model, 1, wx.EXPAND)
        ge_sizer.Add(row6, 0, wx.EXPAND | wx.ALL, 3)

        row6b = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refresh_gemini = wx.Button(gemini_panel, label=_("Available models"))
        row6b.Add(self.btn_refresh_gemini, 0)
        ge_sizer.Add(row6b, 0, wx.ALL, 3)

        gemini_panel.SetSizer(ge_sizer)
        self._llm_sizer.Add(gemini_panel, 0, wx.EXPAND | wx.LEFT, 10)

        sizer.Add(self._llm_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # ---- Chunking section ----
        self._chunk_box = wx.StaticBox(self, label="Chunking")
        self._chunk_sizer = wx.StaticBoxSizer(self._chunk_box, wx.VERTICAL)

        row7 = wx.BoxSizer(wx.HORIZONTAL)
        row7.Add(wx.StaticText(self, label=_("Chunk size (tokens):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_chunk_size = wx.SpinCtrl(self, min=500, max=8000, initial=2000)
        row7.Add(self.spin_chunk_size, 0)
        self._chunk_sizer.Add(row7, 0, wx.EXPAND | wx.ALL, 5)

        row8 = wx.BoxSizer(wx.HORIZONTAL)
        row8.Add(wx.StaticText(self, label=_("Overlap (tokens):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.spin_overlap = wx.SpinCtrl(self, min=0, max=1000, initial=200)
        row8.Add(self.spin_overlap, 0)
        self._chunk_sizer.Add(row8, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(self._chunk_sizer, 0, wx.EXPAND | wx.ALL, 5)

        self.btn_save = wx.Button(self, label=_("Save settings"))
        sizer.Add(self.btn_save, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 10)

        self.SetSizer(sizer)

        # ---- Bind ----
        self.btn_browse_tesseract.Bind(wx.EVT_BUTTON, self._on_browse_tesseract)
        self.btn_browse_surya_python.Bind(wx.EVT_BUTTON, self._on_browse_surya_python)
        self.btn_browse_surya20_python.Bind(wx.EVT_BUTTON, self._on_browse_surya20_python)
        self.btn_surya20_start.Bind(wx.EVT_BUTTON, self._on_surya20_start)
        self.btn_surya20_stop.Bind(wx.EVT_BUTTON, self._on_surya20_stop)
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
        self.btn_refresh_gemini.Bind(wx.EVT_BUTTON, self._on_refresh_gemini)
        self.btn_save.Bind(wx.EVT_BUTTON, self._on_save)
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
        self._update_provider_visibility()

    def _update_provider_visibility(self):
        is_ollama = self.rb_provider.GetSelection() == 0
        self.ollama_panel.Show(is_ollama)
        self.gemini_panel.Show(not is_ollama)
        self.Layout()

    def _update_section_visibility(self):
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
        self._update_ocr_engine_visibility()

    def _update_ocr_engine_visibility(self):
        sel = self.rb_ocr_engine.GetSelection()
        is_tesseract = sel == _ENGINE_TO_IDX.get("tesseract", -1)
        is_vlm = sel == _ENGINE_TO_IDX.get("vlm", -1)
        is_windows = sel == _ENGINE_TO_IDX.get("windows", -1)
        is_apple_vision = sel == _ENGINE_TO_IDX.get("apple_vision", -1)
        is_surya = sel == _ENGINE_TO_IDX.get("surya", -1)
        is_surya20 = sel == _ENGINE_TO_IDX.get("surya20", -1)
        is_chandra = ENABLE_CHANDRA and sel == _ENGINE_TO_IDX.get("chandra", -1)
        self.lbl_tesseract_path.Show(is_tesseract)
        self.txt_tesseract_path.Show(is_tesseract)
        self.btn_browse_tesseract.Show(is_tesseract)
        self.lbl_ocr_lang.Show(is_tesseract or is_apple_vision)
        self.cmb_ocr_lang.Show(is_tesseract or is_apple_vision)
        self.btn_refresh_langs.Show(is_tesseract)
        if _IS_MACOS:
            self.lbl_apple_vision_note.Show(is_apple_vision)
            self.chk_apple_vision_lang_correction.Show(is_apple_vision)
        self.lbl_vlm_model.Show(is_vlm)
        self.cmb_vlm_model.Show(is_vlm)
        self.btn_vlm_local.Show(is_vlm)
        self.btn_vlm_library.Show(is_vlm)
        self.btn_vlm_cloud.Show(is_vlm)
        self.chk_vlm_cloud.Show(is_vlm)
        self.lbl_vlm_api_key.Show(is_vlm)
        self.txt_vlm_api_key.Show(is_vlm)
        self.lbl_winocr_lang.Show(is_windows)
        self.cmb_winocr_lang.Show(is_windows)
        self.btn_refresh_winocr.Show(is_windows)
        self.lbl_surya_python.Show(is_surya)
        self.txt_surya_python.Show(is_surya)
        self.btn_browse_surya_python.Show(is_surya)
        self.lbl_surya_note.Show(is_surya)
        self.lbl_surya20_python.Show(is_surya20)
        self.txt_surya20_python.Show(is_surya20)
        self.btn_browse_surya20_python.Show(is_surya20)
        self.lbl_surya20_note.Show(is_surya20)
        self.lbl_surya20_batch.Show(is_surya20)
        self.spin_surya20_batch.Show(is_surya20)
        self.lbl_surya20_parallel.Show(is_surya20)
        self.spin_surya20_parallel.Show(is_surya20)
        self.btn_surya20_start.Show(is_surya20)
        self.btn_surya20_stop.Show(is_surya20)
        self.lbl_surya20_server_status.Show(is_surya20)
        if is_surya20:
            self._update_surya20_server_ui()
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
        try:
            from app.engine.windows_ocr_engine import get_available_languages
            langs = get_available_languages()
            wx.CallAfter(self._update_winocr_langs, langs)
        except Exception:
            pass

    def _on_refresh_winocr(self, _event):
        self.main_frame.set_status(_("Loading Windows OCR languages..."))
        self._speak(_("Loading Windows OCR languages."))

        def _fetch():
            try:
                from app.engine.windows_ocr_engine import get_available_languages
                langs = get_available_languages()
                wx.CallAfter(self._update_winocr_langs, langs)
            except ImportError as e:
                wx.CallAfter(wx.MessageBox, str(e), _("Error"), wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, _("winrt packages not installed."))
            except Exception as e:
                wx.CallAfter(wx.MessageBox, _("Error: {e}").format(e=e), _("Error"), wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, _("Error loading Windows OCR languages."))

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_winocr_langs(self, langs: list[tuple[str, str]]):
        if not langs:
            wx.MessageBox(
                _("No OCR language installed.\n"
                  "Install a language package from:\n"
                  "Settings → Time & language → Language → Add a language."),
                _("Warning"), wx.OK | wx.ICON_WARNING,
            )
            self.main_frame.set_status(_("No Windows OCR language installed."))
            return

        choices = [f"{name} ({tag})" for tag, name in langs]
        self._winocr_lang_tags = [tag for tag, _ in langs]
        self.cmb_winocr_lang.Set(choices)

        saved_tag = self.config.get("windows_ocr_lang", "it-IT")
        if saved_tag in self._winocr_lang_tags:
            idx = self._winocr_lang_tags.index(saved_tag)
            self.cmb_winocr_lang.SetSelection(idx)
        else:
            self.cmb_winocr_lang.SetSelection(0)

        n = len(langs)
        self.main_frame.set_status(_("Found {n} Windows OCR languages.").format(n=n))
        self._speak(_("Found {n} Windows OCR languages.").format(n=n))

    def _get_winocr_tag(self) -> str:
        idx = self.cmb_winocr_lang.GetSelection()
        if hasattr(self, "_winocr_lang_tags") and 0 <= idx < len(self._winocr_lang_tags):
            return self._winocr_lang_tags[idx]
        return self.config.get("windows_ocr_lang", "it-IT")

    def _update_vlm_models(self, models, label=""):
        self.cmb_vlm_model.Set(models)
        if models:
            self.cmb_vlm_model.SetValue(models[0])
        n = len(models)
        msg = _("Found {n} {label}.").format(n=n, label=label) if label else f"{n} models"
        self.main_frame.set_status(msg)
        self._speak(msg)
        self.cmb_vlm_model.SetFocus()

    def _on_vlm_local(self, _event):
        url = self.txt_ollama_url.GetValue().rstrip("/")
        self.main_frame.set_status(_("Loading local models for VLM..."))
        self._speak(_("Loading local models for VLM."))

        def _fetch():
            try:
                resp = requests.get(f"{url}/api/tags", timeout=10)
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                wx.CallAfter(self._update_vlm_models, models, _("local models"))
            except Exception as e:
                wx.CallAfter(wx.MessageBox, _("Ollama connection error: {e}").format(e=e), _("Error"), wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, _("Ollama connection error."))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_vlm_library(self, _event):
        self.main_frame.set_status(_("Loading downloadable models for VLM..."))
        self._speak(_("Loading downloadable models for VLM."))

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
                    wx.CallAfter(self._update_vlm_models, models, _("downloadable models"))
                else:
                    wx.CallAfter(wx.MessageBox, _("No downloadable model found."), _("Warning"), wx.OK | wx.ICON_WARNING)
                    wx.CallAfter(self.main_frame.set_status, _("No downloadable model found."))
            except Exception as e:
                wx.CallAfter(wx.MessageBox, _("Error fetching downloadable models:\n{e}").format(e=e), _("Error"), wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, _("Downloadable model retrieval error."))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_vlm_cloud(self, _event):
        self.main_frame.set_status(_("Loading cloud models for VLM..."))
        self._speak(_("Loading cloud models for VLM."))

        def _fetch():
            try:
                resp = requests.get("https://ollama.com/api/tags", timeout=15)
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                if models:
                    wx.CallAfter(self._update_vlm_models, models, _("cloud models"))
                else:
                    wx.CallAfter(wx.MessageBox, _("No model found in Ollama cloud."), _("Warning"), wx.OK | wx.ICON_WARNING)
                    wx.CallAfter(self.main_frame.set_status, _("No model found."))
            except Exception as e:
                wx.CallAfter(wx.MessageBox, _("Error fetching cloud models:\n{e}").format(e=e), _("Error"), wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, _("Cloud model retrieval error."))

        threading.Thread(target=_fetch, daemon=True).start()

    def _load_values(self):
        self.txt_tesseract_path.SetValue(self.config.get("tesseract_path", ""))
        saved_lang = self.config.get("ocr_lang", "ita")
        lang_map = get_lang_name_map()
        self.cmb_ocr_lang.SetValue(_(lang_map.get(saved_lang, saved_lang)))

        ocr_engine = self.config.get("ocr_engine", "tesseract")
        sel = _ENGINE_TO_IDX.get(ocr_engine, 0)
        self.rb_ocr_engine.SetSelection(sel)
        self.cmb_vlm_model.SetValue(self.config.get("vlm_model", ""))
        self.txt_surya_python.SetValue(self.config.get("surya_python", ""))
        self.txt_surya20_python.SetValue(self.config.get("surya20_python", ""))
        self.spin_surya20_batch.SetValue(int(self.config.get("surya20_batch_size", 4)))
        self.spin_surya20_parallel.SetValue(int(self.config.get("surya20_parallel", 8)))
        self.txt_chandra_python.SetValue(self.config.get("chandra_python", ""))
        chandra_method = self.config.get("chandra_method", "vllm")
        self.rb_chandra_method.SetSelection(0 if chandra_method == "vllm" else 1)
        self.txt_chandra_vllm_url.SetValue(self.config.get("chandra_vllm_url", "http://localhost:8000"))
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
        if _IS_MACOS:
            self.chk_apple_vision_lang_correction.SetValue(
                self.config.get("apple_vision_language_correction", True)
            )
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
        return re.sub(r"\s*\(cloud\)$", "", name)

    def _speak(self, text: str):
        announce(text)

    def _on_vlm_cloud_toggled(self, _event):
        self.chk_ollama_cloud.SetValue(self.chk_vlm_cloud.IsChecked())

    def _on_ollama_cloud_toggled(self, _event):
        self.chk_vlm_cloud.SetValue(self.chk_ollama_cloud.IsChecked())

    def _on_vlm_api_key_changed(self, _event):
        val = self.txt_vlm_api_key.GetValue()
        if self.txt_ollama_api_key.GetValue() != val:
            self.txt_ollama_api_key.ChangeValue(val)

    def _on_ollama_api_key_changed(self, _event):
        val = self.txt_ollama_api_key.GetValue()
        if self.txt_vlm_api_key.GetValue() != val:
            self.txt_vlm_api_key.ChangeValue(val)

    def _on_browse_tesseract(self, _event):
        if _IS_WINDOWS:
            title = _("Select tesseract.exe")
            wildcard = _("Executables (*.exe)|*.exe")
        else:
            title = _("Select tesseract executable")
            wildcard = _("All files (*)|*")
        dlg = wx.FileDialog(
            self, title, wildcard=wildcard, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_tesseract_path.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_browse_surya_python(self, _event):
        if _IS_WINDOWS:
            title = _("Select python.exe of the venv with Surya/PyTorch")
            wildcard = _("Executables (*.exe)|*.exe")
        else:
            title = _("Select Python executable of the venv with Surya/PyTorch")
            wildcard = _("All files (*)|*")
        dlg = wx.FileDialog(
            self, title, wildcard=wildcard, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_surya_python.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_browse_surya20_python(self, _event):
        if _IS_WINDOWS:
            title = _("Select python.exe of the venv with Surya 0.2")
            wildcard = _("Executables (*.exe)|*.exe")
        else:
            title = _("Select Python executable of the venv with Surya 0.2")
            wildcard = _("All files (*)|*")
        dlg = wx.FileDialog(
            self, title, wildcard=wildcard, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_surya20_python.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _update_surya20_server_ui(self):
        from app.engine.surya20_engine import Surya20Engine
        running = Surya20Engine.is_daemon_running()
        self.btn_surya20_start.Enable(not running)
        self.btn_surya20_stop.Enable(running)
        label = _("Server ready.") if running else _("Server not started.")
        self.lbl_surya20_server_status.SetLabel(label)

    def _on_surya20_start(self, _event):
        from app.engine.surya20_engine import Surya20Engine
        python_exe = self.txt_surya20_python.GetValue()
        self.btn_surya20_start.Enable(False)
        self.btn_surya20_stop.Enable(False)
        self.lbl_surya20_server_status.SetLabel(_("Starting..."))
        self.main_frame.set_status(_("Starting Surya 0.2 server..."))
        self._speak(_("Starting Surya 0.2 server."))

        def _run():
            try:
                Surya20Engine.start_daemon(python_exe=python_exe)
                wx.CallAfter(self._surya20_server_ready)
            except Exception as e:
                wx.CallAfter(self._surya20_server_failed, str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _surya20_server_ready(self):
        self.btn_surya20_start.Enable(False)
        self.btn_surya20_stop.Enable(True)
        self.lbl_surya20_server_status.SetLabel(_("Server ready."))
        self.main_frame.set_status(_("Surya 0.2 server ready."))
        self._speak(_("Surya 0.2 server ready."))

    def _surya20_server_failed(self, msg: str):
        self.btn_surya20_start.Enable(True)
        self.btn_surya20_stop.Enable(False)
        self.lbl_surya20_server_status.SetLabel(_("Error: {error}").format(error=msg[:60]))
        self.main_frame.set_status(_("Surya 0.2 server error: {msg}").format(msg=msg))
        self._speak(_("Surya 0.2 server startup error."))
        wx.MessageBox(msg, _("Surya 0.2 server error"), wx.OK | wx.ICON_ERROR)

    def _on_surya20_stop(self, _event):
        from app.engine.surya20_engine import Surya20Engine
        Surya20Engine.stop_daemon()
        self.btn_surya20_start.Enable(True)
        self.btn_surya20_stop.Enable(False)
        self.lbl_surya20_server_status.SetLabel(_("Server stopped."))
        self.main_frame.set_status(_("Surya 0.2 server stopped."))
        self._speak(_("Surya 0.2 server stopped."))

    def _on_detect_wsl_ip(self, _event):
        import subprocess
        self._speak(_("Detecting WSL IP."))
        try:
            result = subprocess.run(
                ["wsl.exe", "-e", "hostname", "-I"],
                capture_output=True, text=True, timeout=10,
            )
            ip = result.stdout.strip().split()[0] if result.stdout.strip() else ""
            if ip:
                url = f"http://{ip}:8000"
                self.txt_chandra_vllm_url.SetValue(url)
                self.main_frame.set_status(_("WSL IP detected: {ip}").format(ip=ip))
                self._speak(_("WSL IP: {ip}").format(ip=ip))
            else:
                wx.MessageBox(
                    _("Cannot detect WSL IP.\nEnter the vLLM server URL manually."),
                    _("Warning"), wx.OK | wx.ICON_WARNING,
                )
        except Exception as e:
            wx.MessageBox(
                _("Error detecting WSL IP:\n{e}").format(e=e),
                _("Error"), wx.OK | wx.ICON_ERROR,
            )

    def _on_detect_distros(self, _event):
        from app.engine import vllm_server
        self._speak(_("Detecting WSL distributions."))
        distros = vllm_server.list_wsl_distros()
        if distros:
            self.cmb_vllm_distro.Set(distros)
            saved = self.config.get("vllm_wsl_distro", "")
            if saved in distros:
                self.cmb_vllm_distro.SetValue(saved)
            else:
                self.cmb_vllm_distro.SetValue(distros[0])
            n = len(distros)
            self.main_frame.set_status(_("Found {n} WSL distributions.").format(n=n))
            self._speak(_("Found {n} WSL distributions.").format(n=n))
        else:
            wx.MessageBox(
                _("No WSL distribution found.\nVerify that WSL2 is installed and active."),
                _("Warning"), wx.OK | wx.ICON_WARNING,
            )

    def _on_start_vllm(self, _event):
        from app.engine import vllm_server

        config = self.get_config()
        url = config.get("chandra_vllm_url", "http://localhost:8000")

        self.btn_start_vllm.Enable(False)
        self.btn_stop_vllm.Enable(False)
        self.lbl_vllm_status.SetLabel(_("Starting..."))
        self.main_frame.set_status(_("Starting vLLM server..."))
        self._speak(_("Starting vLLM server."))

        def _run():
            try:
                vllm_server.start(config)

                def _tick(elapsed):
                    mins, secs = divmod(elapsed, 60)
                    label = _("Loading model... {m}m {s:02d}s").format(m=mins, s=secs)
                    wx.CallAfter(self.lbl_vllm_status.SetLabel, label)

                ready = vllm_server.wait_ready(url, timeout=300, on_tick=_tick)
                if ready:
                    wx.CallAfter(self._vllm_ready)
                else:
                    msg = (
                        _("Timeout: server not responding.")
                        if vllm_server.is_alive()
                        else _("The process stopped unexpectedly.")
                    )
                    wx.CallAfter(self._vllm_failed, msg)
            except Exception as e:
                wx.CallAfter(self._vllm_failed, str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _vllm_ready(self):
        self.btn_start_vllm.Enable(False)
        self.btn_stop_vllm.Enable(True)
        self.lbl_vllm_status.SetLabel(_("Server ready."))
        self.main_frame.set_status(_("vLLM server ready."))
        self._speak(_("vLLM server ready."))

    def _vllm_failed(self, msg: str):
        from app.engine import vllm_server
        self.btn_start_vllm.Enable(True)
        self.btn_stop_vllm.Enable(False)
        self.lbl_vllm_status.SetLabel(_("Error: {error}").format(error=msg))
        self.main_frame.set_status(_("Starting vLLM server startup error: {msg}").format(msg=msg))
        self._speak(_("Starting vLLM server startup error: {msg}").format(msg=msg))
        log = vllm_server.get_log()
        detail = f"{msg}\n\n--- Log ---\n{log}" if log else msg
        dlg = wx.MessageDialog(self, detail, _("vLLM server log"), wx.OK | wx.ICON_ERROR)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_show_vllm_log(self, _event):
        from app.engine import vllm_server
        log = vllm_server.get_log()
        text = log if log else _("(no output available)")
        dlg = wx.Dialog(self, title=_("vLLM server log"), size=(700, 500))
        sizer = wx.BoxSizer(wx.VERTICAL)
        txt = wx.TextCtrl(dlg, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        txt.SetValue(text)
        txt.SetInsertionPointEnd()
        sizer.Add(txt, 1, wx.EXPAND | wx.ALL, 8)
        btn_ok = wx.Button(dlg, wx.ID_OK, _("Close"))
        sizer.Add(btn_ok, 0, wx.ALIGN_CENTER | wx.BOTTOM, 8)
        dlg.SetSizer(sizer)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_stop_vllm(self, _event):
        from app.engine import vllm_server
        vllm_server.stop()
        self.btn_start_vllm.Enable(True)
        self.btn_stop_vllm.Enable(False)
        self.lbl_vllm_status.SetLabel(_("Server stopped."))
        self.main_frame.set_status(_("vLLM server stopped."))
        self._speak(_("vLLM server stopped."))

    def _on_browse_chandra_python(self, _event):
        if _IS_WINDOWS:
            title = _("Select python.exe of the venv with Chandra installed")
            wildcard = _("Executables (*.exe)|*.exe")
        else:
            title = _("Select Python executable of the venv with Chandra installed")
            wildcard = _("All files (*)|*")
        dlg = wx.FileDialog(
            self, title, wildcard=wildcard, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_chandra_python.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_refresh_langs(self, _event):
        from app.engine.tesseract_setup import get_tesseract_cmd
        import subprocess

        cmd = get_tesseract_cmd(self.txt_tesseract_path.GetValue())
        if not cmd:
            wx.MessageBox(_("Tesseract not found."), _("Error"), wx.OK | wx.ICON_ERROR)
            return
        try:
            result = subprocess.run(
                [cmd, "--list-langs"],
                capture_output=True, text=True, timeout=10,
            )
            langs = [l.strip() for l in result.stdout.strip().split("\n")[1:] if l.strip()]
            if langs:
                lang_map = get_lang_name_map()
                display = [lang_map.get(l, l) for l in langs]
                self.cmb_ocr_lang.Set(display)
                self.cmb_ocr_lang.SetValue(display[0] if display else "")
                self.main_frame.set_status(_("Found {n} languages.").format(n=len(langs)))
        except Exception as e:
            wx.MessageBox(_("Error: {e}").format(e=e), _("Error"), wx.OK | wx.ICON_ERROR)

    def _on_refresh_ollama(self, _event):
        url = self.txt_ollama_url.GetValue().rstrip("/")
        self.main_frame.set_status(_("Loading Ollama models..."))

        def _fetch():
            try:
                resp = requests.get(f"{url}/api/tags", timeout=10)
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                wx.CallAfter(self._update_ollama_models, models)
            except Exception as e:
                wx.CallAfter(wx.MessageBox, _("Ollama connection error: {e}").format(e=e), _("Error"), wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, _("Ollama connection error."))

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_ollama_models(self, models, label=""):
        self.cmb_ollama_model.Set(models)
        if models:
            self.cmb_ollama_model.SetValue(models[0])
        n = len(models)
        msg = _("Found {n} {label}.").format(n=n, label=label) if label else f"{n} models"
        self.main_frame.set_status(msg)
        self._speak(msg)
        self.cmb_ollama_model.SetFocus()

    def _on_library_ollama(self, _event):
        self.main_frame.set_status(_("Loading downloadable Ollama models..."))
        self._speak(_("Loading downloadable Ollama models."))

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
                    wx.CallAfter(self._update_ollama_models, models, _("Ollama downloadable models"))
                else:
                    wx.CallAfter(wx.MessageBox, _("No downloadable model found."), _("Warning"), wx.OK | wx.ICON_WARNING)
                    wx.CallAfter(self.main_frame.set_status, _("No downloadable model found."))
            except Exception as e:
                wx.CallAfter(wx.MessageBox, _("Error fetching downloadable models:\n{e}").format(e=e), _("Error"), wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, _("Downloadable model retrieval error."))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_remote_ollama(self, _event):
        self.main_frame.set_status(_("Loading cloud Ollama models..."))
        self._speak(_("Loading cloud Ollama models."))

        def _fetch():
            try:
                resp = requests.get("https://ollama.com/api/tags", timeout=15)
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                if models:
                    wx.CallAfter(self._update_ollama_models, models, _("Ollama cloud models"))
                else:
                    wx.CallAfter(wx.MessageBox, _("No model found in Ollama cloud."), _("Warning"), wx.OK | wx.ICON_WARNING)
                    wx.CallAfter(self.main_frame.set_status, _("No model found."))
            except Exception as e:
                wx.CallAfter(wx.MessageBox, _("Error fetching cloud models:\n{e}").format(e=e), _("Error"), wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.main_frame.set_status, _("Cloud model retrieval error."))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_refresh_gemini(self, _event):
        api_key = self.txt_gemini_key.GetValue().strip()
        if not api_key:
            wx.MessageBox(
                _("Please enter the Gemini API key first."),
                _("Missing key"), wx.OK | wx.ICON_WARNING,
            )
            return
        self.main_frame.set_status(_("Loading Gemini models..."))
        self._speak(_("Loading Gemini models."))

        def _fetch():
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                models = [
                    m["name"].replace("models/", "")
                    for m in resp.json().get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                ]
                models.sort()
                if models:
                    wx.CallAfter(self._update_gemini_models, models)
                else:
                    wx.CallAfter(
                        wx.MessageBox,
                        _("No Gemini model found."),
                        _("Warning"), wx.OK | wx.ICON_WARNING,
                    )
                    wx.CallAfter(self.main_frame.set_status, _("No Gemini model found."))
            except Exception as e:
                wx.CallAfter(
                    wx.MessageBox,
                    _("Error fetching Gemini models:\n{e}").format(e=e),
                    _("Error"), wx.OK | wx.ICON_ERROR,
                )
                wx.CallAfter(self.main_frame.set_status, _("Gemini model retrieval error."))

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_gemini_models(self, models):
        self.cmb_gemini_model.Set(models)
        if models:
            self.cmb_gemini_model.SetValue(models[0])
        n = len(models)
        msg = _("Found {n} Gemini models.").format(n=n)
        self.main_frame.set_status(msg)
        self._speak(msg)
        self.cmb_gemini_model.SetFocus()

    def _on_save(self, _event):
        self.config["tesseract_path"] = self.txt_tesseract_path.GetValue()
        raw_lang = self.cmb_ocr_lang.GetValue()
        self.config["ocr_lang"] = LANG_NAME_TO_CODE.get(raw_lang, raw_lang)
        self.config["ocr_engine"] = _IDX_TO_ENGINE.get(self.rb_ocr_engine.GetSelection(), "tesseract")
        self.config["vlm_model"] = self.cmb_vlm_model.GetValue()
        self.config["surya_python"] = self.txt_surya_python.GetValue()
        self.config["surya20_python"] = self.txt_surya20_python.GetValue()
        self.config["surya20_batch_size"] = self.spin_surya20_batch.GetValue()
        self.config["surya20_parallel"] = self.spin_surya20_parallel.GetValue()
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
        if _IS_MACOS:
            self.config["apple_vision_language_correction"] = (
                self.chk_apple_vision_lang_correction.IsChecked()
            )
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
        self.main_frame.set_status(_("Settings saved."))
        announce(_("Settings saved."))

    def get_config(self) -> dict:
        raw_model = self.cmb_ollama_model.GetValue()
        return {
            "tesseract_path": self.txt_tesseract_path.GetValue(),
            "ocr_lang": LANG_NAME_TO_CODE.get(self.cmb_ocr_lang.GetValue(), self.cmb_ocr_lang.GetValue()),
            "ocr_engine": _IDX_TO_ENGINE.get(self.rb_ocr_engine.GetSelection(), "tesseract"),
            "vlm_model": self.cmb_vlm_model.GetValue(),
            "surya_python": self.txt_surya_python.GetValue(),
            "surya20_python": self.txt_surya20_python.GetValue(),
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
            "apple_vision_language_correction": (
                self.chk_apple_vision_lang_correction.IsChecked() if _IS_MACOS else False
            ),
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
