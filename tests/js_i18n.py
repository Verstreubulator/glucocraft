# License: AGPL-3.0-only (LICENSE).
"""The language catalogues for the tests that run the cards' JavaScript in node.

The cards take every text from glucocraft-i18n.js, which fetches the catalogues next to itself in a browser. In node
there is no server: i18n_prelude() returns the module's source followed by add() calls with the catalogues read from
custom_components/glucocraft/i18n/<language>/, so a card file evaluated after it finds its texts loaded, without a fetch.
The prelude ends with the cards' shared module (glucocraft-shared.js), so that a card file finds it loaded as well.
"""

from __future__ import annotations

import json
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
WWW = COMPONENT / "www"
I18N = COMPONENT / "i18n"
FRONTEND_AREAS = ("frontend", "report", "documentation")


def catalogue(area: str, language: str = "en") -> dict:
    """Return one catalogue as a dict of flat dotted keys (an empty dict when the file does not exist)."""
    path = I18N / language / f"{area}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    flat: dict = {}

    def walk(obj: dict, prefix: str) -> None:
        for key, value in obj.items():
            full = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(value, full)
            else:
                flat[full] = value

    walk(data, "")
    return flat


def text(key: str, area: str = "frontend", language: str = "en", **params: object) -> str:
    """Return the text of a key as the cards show it: list values joined, the named placeholders filled."""
    value = catalogue(area, language)[key]
    value = "".join(value) if isinstance(value, list) else value
    for name, val in params.items():
        value = value.replace("{" + name + "}", str(val))
    return value


def shared_prelude() -> str:
    """Return glucocraft-shared.js, the cards' shared rules (it defines globalThis.glucocraftShared, so that a card file
    evaluated after it finds the module loaded, without an import)."""
    return (WWW / "glucocraft-shared.js").read_text(encoding="utf-8") + "\n"


def i18n_prelude(language: str = "en", areas: tuple[str, ...] = FRONTEND_AREAS) -> str:
    """Return glucocraft-i18n.js and the catalogues of the areas, English first (the fallback), then the language, and
    the cards' shared module (shared_prelude)."""
    src = (WWW / "glucocraft-i18n.js").read_text(encoding="utf-8")
    adds = "".join(
        f"globalThis.glucocraftI18n.add({json.dumps(lang)}, {json.dumps(area)}, "
        f"{json.dumps(catalogue(area, lang), ensure_ascii=False)});\n"
        for lang in dict.fromkeys(("en", language))
        for area in areas
    )
    return src + "\n" + adds + shared_prelude()


def documentation_section(section_id: str) -> str:
    """Return the English body of one Documentation section (its id as in the address, e.g. "read-first")."""
    return text(f"docs.section.{section_id.replace('-', '_')}.body", area="documentation")


def documentation_text() -> str:
    """Return every English text of the Documentation (titles, bodies, shared texts), one per line."""
    return "\n".join("".join(v) if isinstance(v, list) else v for v in catalogue("documentation").values())


def catalogue_text(prefix: str, area: str = "frontend") -> str:
    """Return the English texts whose keys start with prefix (e.g. "ui."), one per line, for phrase checks."""
    return "\n".join(
        "".join(v) if isinstance(v, list) else v for k, v in catalogue(area).items() if k.startswith(prefix)
    )
