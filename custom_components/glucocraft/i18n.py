# License: AGPL-3.0-only (LICENSE).
"""The language catalogues of the backend: every text the integration writes for people (notifications, the spoken
summary, panel notices, CSV headers, the CGM Report) comes from here, so that a language is added with files only.

Files: i18n/<language>/<area>.json, one JSON object of flat dotted keys per area ("notify.export.title": "..."). The
backend reads backend.json and report.json (the Report page reads report.json as well); the frontend's catalogues
live in the same folders and are served by frontend.py. Home Assistant's own texts (the settings screens, entity names
and states, Repairs) stay in strings.json and translations/<language>.json, as Home Assistant requires.

Rules of a catalogue text:
- Values go in through named placeholders, "{value} {unit}", never by joining fragments, so a language can order
  the words its own way. A literal brace is not used in any text.
- Plurals: one key per plural category, "<key>.one", "<key>.other" (CLDR categories: zero, one, two, few, many,
  other; a language writes the ones its rule uses), the same convention as the frontend's catalogues. tp(key, count)
  picks the category with PLURAL_RULES and fills the placeholders ("{count}" with the count unless the caller gives
  it as formatted text); a category the language lacks falls back to ".other".
- Numbers, dates, times and units are formatted by regional.py and units.py, never in the catalogue.
- Citations, proper names, entity ids, attribute keys and the canonical English disclaimer (const.py) are not in the
  catalogues.

Language: Home Assistant's configured language (hass.config.language), looked up as the exact code ("pt-BR"), then
its base ("pt"), then English; every key missing in a language is taken from English. The environment variable
GLUCOCRAFT_LANGUAGE overrides the language for development (the pseudo-localised test language "pseudo"); it is not a
setting and is never shown to users.

Loading: async_setup(hass) reads the files once in the executor (no file access in the event loop) and keeps the
merged catalogue; t() and tp() then only look keys up. Code that runs before any setup (the tests, a first settings
screen) loads English on first use.
"""

from __future__ import annotations

import json
import logging
import os
import string
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

I18N_DIR = Path(__file__).parent / "i18n"
FALLBACK = "en"
AREAS = ("backend", "report")  # the backend's catalogues (frontend.json and the documentation are the cards')
OVERRIDE_ENV = "GLUCOCRAFT_LANGUAGE"  # development only: GLUCOCRAFT_LANGUAGE=pseudo

# The plural category of a count, per language: the CLDR cardinal plural rules, from the operands n (the absolute
# value) and v (the number of visible decimals: "1.0" has v = 1, and is "other" in English). A language without an
# entry uses the English rule; a language whose rule differs adds it here, for example
# "fr": lambda n, v: "one" if n < 2 and v == 0 else "other", with the categories its catalogue then needs.
PLURAL_RULES: dict[str, Callable[[float, int], str]] = {
    "en": lambda n, v: "one" if n == 1 and v == 0 else "other",
}

# The active catalogue: the language in use and its texts, English filled in for every missing key
_state: dict[str, Any] = {"language": None, "requested": None, "texts": {}}
# The keys no catalogue has, each logged once (a text missing in the code's catalogue is asked for again and again)
_missing: set[str] = set()


def _read(language: str) -> dict[str, str]:
    """Return the backend catalogues of one language folder, merged (an absent or unreadable file gives nothing)."""
    out: dict[str, str] = {}
    for area in AREAS:
        path = I18N_DIR / language / f"{area}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            _LOGGER.warning(
                "GlucoCraft: the catalogue %s/%s.json is not readable (%s); English is used", language, area, err
            )
            continue
        out.update({str(k): str(v) for k, v in data.items() if isinstance(v, str)})
    return out


def resolve(language: str | None) -> str:
    """Return the catalogue folder for a language code: the exact code, its base language, or English."""
    code = (language or FALLBACK).strip().replace("_", "-").lower()
    folders = {p.name.lower(): p.name for p in I18N_DIR.iterdir() if p.is_dir()} if I18N_DIR.is_dir() else {}
    for candidate in (code, code.split("-")[0]):
        if candidate in folders:
            return folders[candidate]
    return FALLBACK


def load(language: str | None) -> str:
    """Load and activate the catalogue of a language (blocking file access: run it in the executor); return the
    folder used."""
    folder = resolve(language)
    texts = _read(FALLBACK)
    if folder != FALLBACK:
        texts.update(_read(folder))
    _state["language"], _state["requested"], _state["texts"] = folder, language, texts
    return folder


def configured_language(hass: Any) -> str:
    """Return the language to use: the development override, else Home Assistant's configured language."""
    return os.environ.get(OVERRIDE_ENV) or hass.config.language or FALLBACK


async def async_setup(hass: Any) -> str:
    """Load the catalogue of Home Assistant's language in the executor, once; return the folder in use."""
    language = configured_language(hass)
    if _state["language"] is not None and _state["requested"] == language:
        return _state["language"]
    return await hass.async_add_executor_job(load, language)


def language() -> str:
    """Return the language folder in use (English until a catalogue is loaded)."""
    return _state["language"] or FALLBACK


def _texts() -> dict[str, str]:
    """Return the active texts, loading English on first use when no catalogue was loaded yet."""
    if _state["language"] is None:
        load(FALLBACK)
    return _state["texts"]


class _Keep(dict):
    """Placeholder values for str.format_map: a placeholder without a value stays as written."""

    def __missing__(self, key: str) -> str:
        """Return the placeholder as written."""
        return "{" + key + "}"


def placeholders(text: str) -> set[str]:
    """Return the names of the placeholders in a catalogue text ("{value} {unit}" -> {"value", "unit"})."""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def t(key: str, **params: Any) -> str:
    """Return the text of `key` in the language in use, with its placeholders filled; the key itself when no catalogue
    has it (so that a missing key is visible, never an empty text)."""
    text = _texts().get(key)
    if text is None:
        if key not in _missing:
            _missing.add(key)
            _LOGGER.debug("GlucoCraft: no text for %s", key)
        return key
    return text.format_map(_Keep(params)) if params else text


def plural_category(count: float, decimals: int = 0, lang: str | None = None) -> str:
    """Return the CLDR plural category of a count shown with `decimals` decimals ("one", "other", ...)."""
    code = (lang or language()).split("-")[0]
    return PLURAL_RULES.get(code, PLURAL_RULES[FALLBACK])(abs(count), decimals)


def tp(key: str, count: float, decimals: int = 0, **params: Any) -> str:
    """Return the plural form of `key` for `count` shown with `decimals` decimals: the text of "<key>.<category>",
    else of "<key>.other", with its placeholders filled; "{count}" is the count itself unless `params` gives it."""
    texts = _texts()
    category = plural_category(count, decimals)
    full = f"{key}.{category}" if f"{key}.{category}" in texts else f"{key}.other"
    return t(full, **{"count": count, **params})
