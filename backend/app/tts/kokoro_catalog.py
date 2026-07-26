"""Kokoro's voice catalogue, language codes and setup guidance.

Kokoro has no voice-listing API: its voices are tensor files inside the model
repo, so there is nothing to query. The catalogue below mirrors the published
``VOICES.md`` of ``hexgrad/Kokoro-82M`` and is therefore static — which is what
lets the Audio tab show every voice, with its grade, before anything has been
downloaded or even installed.

Grades are the model author's own listening grades (A best, F worst) and the
training-data column is theirs too. They are reproduced rather than reinvented
because they predict quality far better than the voice name does: ``am_adam``
sounds worse than its name suggests, and the catalogue is the only place a user
would ever learn that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Every Kokoro voice id is ``<language><gender>_name``, e.g. ``af_heart``.
#: Mirrored in the frontend as KOKORO_VOICE_PATTERN.
VOICE_ID_PATTERN = re.compile(r"^[abefhijpz][fm]_")

#: The model repo every voice and weight comes from.
REPO_ID = "hexgrad/Kokoro-82M"

#: Kokoro's native output rate. Fixed by the model, not a preference.
SAMPLE_RATE = 24_000

#: Kokoro accepts roughly this speed range before it stops sounding human.
MIN_SPEED = 0.5
MAX_SPEED = 2.0


@dataclass(frozen=True)
class KokoroLanguage:
    code: str
    label: str
    #: Locale tag used for filtering in the UI, matching Edge's format.
    locale: str
    #: Extra pip install needed beyond the base package, if any.
    extra_install: str = ""
    #: Word-level subtitle timings only exist for English (see provider).
    word_timings: bool = False


LANGUAGES: dict[str, KokoroLanguage] = {
    "a": KokoroLanguage("a", "Amerikan İngilizcesi", "en-US", word_timings=True),
    "b": KokoroLanguage("b", "Britanya İngilizcesi", "en-GB", word_timings=True),
    "e": KokoroLanguage("e", "İspanyolca", "es"),
    "f": KokoroLanguage("f", "Fransızca", "fr-FR"),
    "h": KokoroLanguage("h", "Hintçe", "hi"),
    "i": KokoroLanguage("i", "İtalyanca", "it"),
    "j": KokoroLanguage("j", "Japonca", "ja", extra_install="misaki[ja]"),
    "p": KokoroLanguage("p", "Brezilya Portekizcesi", "pt-BR"),
    "z": KokoroLanguage("z", "Mandarin Çincesi", "zh", extra_install="misaki[zh]"),
}


@dataclass(frozen=True)
class KokoroVoice:
    id: str
    label: str
    gender: str
    #: Single-letter language code; always ``id[0]``.
    lang_code: str
    #: The model author's listening grade, e.g. "A", "C+", "F+".
    grade: str
    #: How much audio the voice was trained on, in the author's own wording.
    training: str
    note: str = ""

    @property
    def language(self) -> KokoroLanguage:
        return LANGUAGES[self.lang_code]


#: Ordered best-grade-first within each language; the UI keeps this order.
VOICES: tuple[KokoroVoice, ...] = (
    # --- American English ---------------------------------------------------
    KokoroVoice("af_heart", "Heart", "Female", "a", "A", "—", "En yüksek notlu ses."),
    KokoroVoice("af_bella", "Bella", "Female", "a", "A-", "10+ saat", "Sıcak ve dolgun."),
    KokoroVoice("af_nicole", "Nicole", "Female", "a", "B-", "10+ saat", "Fısıltıya yakın, yumuşak."),
    KokoroVoice("af_aoede", "Aoede", "Female", "a", "C+", "1+ saat"),
    KokoroVoice("af_kore", "Kore", "Female", "a", "C+", "1+ saat"),
    KokoroVoice("af_sarah", "Sarah", "Female", "a", "C+", "1+ saat"),
    KokoroVoice("af_alloy", "Alloy", "Female", "a", "C", "10+ dakika"),
    KokoroVoice("af_nova", "Nova", "Female", "a", "C", "10+ dakika"),
    KokoroVoice("af_sky", "Sky", "Female", "a", "C-", "1+ dakika"),
    KokoroVoice("af_jessica", "Jessica", "Female", "a", "D", "10+ dakika"),
    KokoroVoice("af_river", "River", "Female", "a", "D", "10+ dakika"),
    KokoroVoice("am_fenrir", "Fenrir", "Male", "a", "C+", "1+ saat", "Belgesel anlatımı için uygun."),
    KokoroVoice("am_michael", "Michael", "Male", "a", "C+", "1+ saat", "Belgesel anlatımı için uygun."),
    KokoroVoice("am_puck", "Puck", "Male", "a", "C+", "1+ saat", "Belgesel anlatımı için uygun."),
    KokoroVoice("am_echo", "Echo", "Male", "a", "D", "10+ dakika"),
    KokoroVoice("am_eric", "Eric", "Male", "a", "D", "10+ dakika"),
    KokoroVoice("am_liam", "Liam", "Male", "a", "D", "10+ dakika"),
    KokoroVoice("am_onyx", "Onyx", "Male", "a", "D", "10+ dakika"),
    KokoroVoice("am_santa", "Santa", "Male", "a", "D-", "1+ dakika"),
    KokoroVoice("am_adam", "Adam", "Male", "a", "F+", "1+ saat", "Notu düşük; önerilmez."),
    # --- British English ----------------------------------------------------
    KokoroVoice("bf_emma", "Emma", "Female", "b", "B-", "10+ saat", "İngiliz aksanının en iyisi."),
    KokoroVoice("bf_isabella", "Isabella", "Female", "b", "C", "10+ dakika"),
    KokoroVoice("bf_alice", "Alice", "Female", "b", "D", "10+ dakika"),
    KokoroVoice("bf_lily", "Lily", "Female", "b", "D", "10+ dakika"),
    KokoroVoice("bm_fable", "Fable", "Male", "b", "C", "10+ dakika", "Belgesel anlatımı için uygun."),
    KokoroVoice("bm_george", "George", "Male", "b", "C", "10+ dakika", "Belgesel anlatımı için uygun."),
    KokoroVoice("bm_lewis", "Lewis", "Male", "b", "D+", "1+ saat"),
    KokoroVoice("bm_daniel", "Daniel", "Male", "b", "D", "10+ dakika"),
    # --- Other languages ----------------------------------------------------
    KokoroVoice("ff_siwis", "Siwis", "Female", "f", "B-", "10 saatten az"),
    KokoroVoice("ef_dora", "Dora", "Female", "e", "—", "—"),
    KokoroVoice("em_alex", "Alex", "Male", "e", "—", "—"),
    KokoroVoice("em_santa", "Santa", "Male", "e", "—", "—"),
    KokoroVoice("hf_alpha", "Alpha", "Female", "h", "C", "10+ dakika"),
    KokoroVoice("hf_beta", "Beta", "Female", "h", "C", "10+ dakika"),
    KokoroVoice("hm_omega", "Omega", "Male", "h", "C", "10+ dakika"),
    KokoroVoice("hm_psi", "Psi", "Male", "h", "C", "10+ dakika"),
    KokoroVoice("if_sara", "Sara", "Female", "i", "C", "10+ dakika"),
    KokoroVoice("im_nicola", "Nicola", "Male", "i", "C", "10+ dakika"),
    KokoroVoice("jf_alpha", "Alpha", "Female", "j", "C+", "1+ saat"),
    KokoroVoice("jf_gongitsune", "Gongitsune", "Female", "j", "C", "10+ dakika"),
    KokoroVoice("jf_tebukuro", "Tebukuro", "Female", "j", "C", "10+ dakika"),
    KokoroVoice("jf_nezumi", "Nezumi", "Female", "j", "C-", "1+ dakika"),
    KokoroVoice("jm_kumo", "Kumo", "Male", "j", "C-", "1+ dakika"),
    KokoroVoice("pf_dora", "Dora", "Female", "p", "—", "—"),
    KokoroVoice("pm_alex", "Alex", "Male", "p", "—", "—"),
    KokoroVoice("pm_santa", "Santa", "Male", "p", "—", "—"),
    KokoroVoice("zf_xiaobei", "Xiaobei", "Female", "z", "D", "10+ dakika"),
    KokoroVoice("zf_xiaoni", "Xiaoni", "Female", "z", "D", "10+ dakika"),
    KokoroVoice("zf_xiaoxiao", "Xiaoxiao", "Female", "z", "D", "10+ dakika"),
    KokoroVoice("zf_xiaoyi", "Xiaoyi", "Female", "z", "D", "10+ dakika"),
    KokoroVoice("zm_yunjian", "Yunjian", "Male", "z", "D", "10+ dakika"),
    KokoroVoice("zm_yunxi", "Yunxi", "Male", "z", "D", "10+ dakika"),
    KokoroVoice("zm_yunxia", "Yunxia", "Male", "z", "D", "10+ dakika"),
    KokoroVoice("zm_yunyang", "Yunyang", "Male", "z", "D", "10+ dakika"),
)

_BY_ID: dict[str, KokoroVoice] = {voice.id: voice for voice in VOICES}

#: The app default, and what it falls back to when a project switches to Kokoro
#: with a foreign voice id still selected (an Edge name, say). Grade A-, warm
#: and even-paced for narration, and English so subtitles keep their word-level
#: timings.
DEFAULT_VOICE = "af_bella"

#: Surfaced in the UI as a shortlist. Every one is English and graded C+ or
#: better, so a user who just wants a narrator never has to read the grades.
RECOMMENDED: tuple[str, ...] = (
    "af_heart",
    "af_bella",
    "am_michael",
    "am_puck",
    "am_fenrir",
    "bf_emma",
    "bm_george",
)


def find(voice_id: str) -> KokoroVoice | None:
    return _BY_ID.get(voice_id.strip())


def is_known(voice_id: str) -> bool:
    return voice_id.strip() in _BY_ID


def lang_code_for(voice_id: str) -> str:
    """The pipeline language code a voice belongs to.

    Kokoro encodes it in the first character of the id, and the pipeline must
    be built with the matching code — an American voice driven by a British
    pipeline mispronounces its way through the whole script.

    The prefix is only trusted for ids actually shaped like Kokoro voices. Read
    naively, an Edge name such as ``en-US-AndrewNeural`` starts with ``e`` and
    would quietly build a *Spanish* pipeline rather than falling back.
    """
    voice = find(voice_id)
    if voice is not None:
        return voice.lang_code
    if VOICE_ID_PATTERN.match(voice_id.strip()):
        return voice_id.strip()[0].lower()
    return "a"


def english_voice_ids() -> tuple[str, ...]:
    return tuple(v.id for v in VOICES if v.lang_code in {"a", "b"})
