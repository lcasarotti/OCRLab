"""Gestione configurazione persistente (JSON)."""

import json
import os

CONFIG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Lingue predefinite mostrate nella combobox (ordine fisso)
OCR_LANGUAGES = [
    ("ita", "Italiano"),
    ("eng", "Inglese"),
    ("fra", "Francese"),
    ("deu", "Tedesco"),
    ("spa", "Spagnolo"),
]

# Mappa completa codice Tesseract → nome in italiano
# Usata per tradurre i codici restituiti da "tesseract --list-langs"
LANG_CODE_TO_NAME: dict[str, str] = {
    "afr": "Afrikaans",
    "amh": "Amarico",
    "ara": "Arabo",
    "asm": "Assamese",
    "aze": "Azero",
    "aze_cyrl": "Azero (cirillico)",
    "bel": "Bielorusso",
    "ben": "Bengalese",
    "bod": "Tibetano",
    "bos": "Bosniaco",
    "bre": "Bretone",
    "bul": "Bulgaro",
    "cat": "Catalano",
    "ceb": "Cebuano",
    "ces": "Ceco",
    "chi_sim": "Cinese semplificato",
    "chi_sim_vert": "Cinese semplificato (verticale)",
    "chi_tra": "Cinese tradizionale",
    "chi_tra_vert": "Cinese tradizionale (verticale)",
    "chr": "Cherokee",
    "cos": "Corso",
    "cym": "Gallese",
    "dan": "Danese",
    "deu": "Tedesco",
    "div": "Divehi",
    "dzo": "Dzongkha",
    "ell": "Greco",
    "eng": "Inglese",
    "enm": "Inglese medievale",
    "epo": "Esperanto",
    "equ": "Equazioni matematiche",
    "est": "Estone",
    "eus": "Basco",
    "fao": "Faroese",
    "fas": "Persiano",
    "fil": "Filipino",
    "fin": "Finlandese",
    "fra": "Francese",
    "frk": "Tedesco medievale",
    "frm": "Francese medievale",
    "fry": "Frisone occidentale",
    "gla": "Gaelico scozzese",
    "gle": "Irlandese",
    "glg": "Galiziano",
    "grc": "Greco antico",
    "guj": "Gujarati",
    "hat": "Haitiano",
    "heb": "Ebraico",
    "hin": "Hindi",
    "hrv": "Croato",
    "hun": "Ungherese",
    "hye": "Armeno",
    "iku": "Inuktitut",
    "ind": "Indonesiano",
    "isl": "Islandese",
    "ita": "Italiano",
    "ita_old": "Italiano antico",
    "jav": "Giavanese",
    "jpn": "Giapponese",
    "jpn_vert": "Giapponese (verticale)",
    "kan": "Kannada",
    "kat": "Georgiano",
    "kat_old": "Georgiano antico",
    "kaz": "Kazako",
    "khm": "Khmer",
    "kir": "Kirghiso",
    "kmr": "Curdo (Kurmanji)",
    "kor": "Coreano",
    "kor_vert": "Coreano (verticale)",
    "lao": "Lao",
    "lat": "Latino",
    "lav": "Lettone",
    "lit": "Lituano",
    "ltz": "Lussemburghese",
    "mal": "Malayalam",
    "mar": "Marathi",
    "mkd": "Macedone",
    "mlt": "Maltese",
    "mon": "Mongolo",
    "mri": "Maori",
    "msa": "Malese",
    "mya": "Birmano",
    "nep": "Nepalese",
    "nld": "Olandese",
    "nor": "Norvegese",
    "oci": "Occitano",
    "ori": "Oriya",
    "osd": "Rilevamento orientamento/script",
    "pan": "Punjabi",
    "pol": "Polacco",
    "por": "Portoghese",
    "pus": "Pashto",
    "que": "Quechua",
    "ron": "Rumeno",
    "rus": "Russo",
    "san": "Sanscrito",
    "sin": "Singalese",
    "slk": "Slovacco",
    "slv": "Sloveno",
    "snd": "Sindhi",
    "spa": "Spagnolo",
    "spa_old": "Spagnolo antico",
    "sqi": "Albanese",
    "srp": "Serbo",
    "srp_latn": "Serbo (latino)",
    "sun": "Sundanese",
    "swa": "Swahili",
    "swe": "Svedese",
    "syr": "Siriaco",
    "tam": "Tamil",
    "tat": "Tataro",
    "tel": "Telugu",
    "tgk": "Tagico",
    "tha": "Tailandese",
    "tir": "Tigrino",
    "ton": "Tongano",
    "tur": "Turco",
    "uig": "Uiguro",
    "ukr": "Ucraino",
    "urd": "Urdu",
    "uzb": "Uzbeco",
    "uzb_cyrl": "Uzbeco (cirillico)",
    "vie": "Vietnamita",
    "yid": "Yiddish",
    "yor": "Yoruba",
}

# Mappa inversa nome → codice (generata automaticamente dalla mappa completa)
LANG_NAME_TO_CODE: dict[str, str] = {name: code for code, name in LANG_CODE_TO_NAME.items()}


# Imposta a True per abilitare il motore Chandra nell'interfaccia
ENABLE_CHANDRA = False

DEFAULTS = {
    "tesseract_path": "",
    "ocr_lang": "ita",
    "llm_provider": "ollama",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "",
    "ollama_cloud": False,
    "ollama_api_key": "",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.0-flash",
    "chunk_size": 2000,
    "chunk_overlap": 200,
    "verbose_progress": True,
    "ocr_engine": "tesseract",
    "vlm_model": "",
    "windows_ocr_lang": "it-IT",
    "surya_python": "",
    "chandra_python": "",
    "chandra_method": "vllm",
    "chandra_vllm_url": "http://localhost:8000",
    "vllm_quantization": "fp8",
    "vllm_max_tokens": 3072,
    "vllm_gpu_memory": 84,
    "vllm_enforce_eager": True,
    "vllm_wsl_distro": "",
    "vllm_hf_model": "datalab-to/chandra-ocr-2",
    "vllm_extra_args": "--max-num-seqs 1",
    "join_hyphenated": False,
    "skip_tesseract_check": False,
    "streaming_text": False,
    "voice_announcements": True,
}


def load_config() -> dict:
    """Carica la configurazione da config.json, con fallback ai valori di default."""
    config = dict(DEFAULTS)
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
            config.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict) -> None:
    """Salva la configurazione in config.json."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
