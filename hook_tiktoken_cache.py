"""Runtime hook: imposta TIKTOKEN_CACHE_DIR per trovare i dati nel bundle."""
import os
import sys

if getattr(sys, "frozen", False):
    bundle_dir = sys._MEIPASS
    cache_dir = os.path.join(bundle_dir, "tiktoken_cache")
    if os.path.isdir(cache_dir):
        os.environ["TIKTOKEN_CACHE_DIR"] = cache_dir
