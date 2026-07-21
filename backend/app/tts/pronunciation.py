"""Pronunciation dictionary application.

Scientific names are the reason this exists: every TTS engine mangles
"Raphus cucullatus". The dictionary maps the written form to a phonetic
respelling that the engine reads correctly.

Substitution is plain text, not SSML. SSML support varies by provider and a
malformed tag is worse than a mispronounced word.
"""

from __future__ import annotations

import re


def apply_pronunciation(text: str, dictionary: dict[str, str]) -> str:
    """Replace each dictionary key in ``text`` with its respelling.

    Longer keys are applied first so "Raphus cucullatus" wins over a separate
    entry for "Raphus". Matching is case-insensitive at word boundaries, and the
    replacement is inserted literally.
    """
    if not dictionary or not text:
        return text

    result = text
    for term in sorted(dictionary, key=len, reverse=True):
        replacement = dictionary[term]
        if not term.strip():
            continue
        # \b does not work next to punctuation-adjacent multiword phrases in all
        # cases, so anchor on non-word boundaries explicitly.
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        result = pattern.sub(lambda _match, r=replacement: r, result)
    return result


def sanitize_for_tts(text: str) -> str:
    """Clean narration into something every engine reads safely.

    Removes markup that some engines interpret as SSML, normalizes quotes and
    dashes, and collapses whitespace. The visible subtitle text is derived from
    the *original*, so this never changes what the viewer reads.
    """
    if not text:
        return ""

    cleaned = text
    # Angle brackets would be parsed as SSML by several engines.
    cleaned = cleaned.replace("<", " ").replace(">", " ")
    # Ampersands break XML-based request bodies.
    cleaned = cleaned.replace("&", " and ")
    # Typographic characters that some engines spell out literally.
    replacements = {
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": ", ", "—": ", ", "…": "...", " ": " ",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
