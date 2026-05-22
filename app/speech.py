"""Annunci vocali agli screen reader, cross-platform.

Due funzioni con semantica diversa:

  announce(text)       -> annuncia sempre (eventi non legati al focus:
                          completamento OCR, errori in background, progressi).

  announce_focus(text) -> annuncia solo su Windows (eventi legati al focus
                          come cambio tab o toggle menu: VoiceOver su Mac
                          li legge gia' da se' tramite l'API di accessibilita'
                          Cocoa, duplicarli sarebbe rumore).

Implementazione: su Mac usa il comando `say`, su Windows usa
`accessible_output2` (NVDA / JAWS / SAPI). Su altre piattaforme: silenzio.

Su Mac `announce()` puo' essere disabilitato runtime con
`set_voice_announcements_enabled(False)`: utile quando l'app e' in primo
piano e VoiceOver basta a coprire gli aggiornamenti di stato. Il flag non
ha effetto su Windows, dove gli annunci passano comunque allo screen reader
e non al sintetizzatore di sistema.
"""

import subprocess
import sys


_IS_MAC = sys.platform == "darwin"
_IS_WINDOWS = sys.platform == "win32"

# Flag runtime: controlla solo gli annunci con la voce di sistema su Mac.
_voice_announcements_enabled = True


def set_voice_announcements_enabled(enabled: bool) -> None:
    """Abilita/disabilita gli annunci con la voce di sistema su Mac."""
    global _voice_announcements_enabled
    _voice_announcements_enabled = bool(enabled)


def _speak_mac(text: str) -> None:
    try:
        subprocess.Popen(["say", text])
    except Exception:
        pass


def _speak_windows(text: str) -> None:
    try:
        import accessible_output2.outputs.auto as ao
        ao.Auto().speak(text)
    except Exception:
        pass


def announce(text: str) -> None:
    """Annuncia un evento NON legato al focus.

    Da usare per: completamento di operazioni, errori asincroni, progressi
    durante elaborazioni lunghe. Lo screen reader non li rileverebbe da solo.
    """
    if not text:
        return
    if _IS_MAC:
        if _voice_announcements_enabled:
            _speak_mac(text)
    elif _IS_WINDOWS:
        _speak_windows(text)


def announce_focus(text: str) -> None:
    """Annuncia un evento LEGATO al focus (no-op su Mac).

    Da usare per cambio tab, toggle di voci di menu, etichette di controlli:
    su Mac VoiceOver li annuncia gia' di suo via accessibilita' Cocoa,
    quindi qui non facciamo nulla. Su Windows spingiamo l'annuncio perche'
    NVDA/JAWS spesso non lo coprono.
    """
    if not text:
        return
    if _IS_WINDOWS:
        _speak_windows(text)
