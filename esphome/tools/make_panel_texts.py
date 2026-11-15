# License: AGPL-3.0-only (LICENSE).
r"""Generate the GlucoCraft panels' texts from the text catalogues.

Every text a panel shows is in a catalogue, i18n/<language>.yaml (en.yaml is the source language and the fallback for
every missing key). A panel's language is a build choice: its configuration sets the substitution "language: en" and
takes, from i18n/generated/<language>/,

  - texts.yaml, an ESPHome package (packages: texts: !include i18n/generated/${language}/texts.yaml) of substitutions:
      t_<name>          the static label texts ("${t_startup_line}"), each a catalogue text;
      glyphs_*          the glyph list of each font class: the characters the catalogue uses, plus the digits and
                        symbols the layouts need;
  - gc_text.h, a C++ header (esphome: includes:, and #include "gc_text.h" in the headers) with every text as a
    constant gct::<name>, and the day and month names as arrays.

<name> is the key without "panel." with the dots as underscores: panel.status.warm_up -> t_status_warm_up, gct::status_warm_up.
A text with placeholders ({minutes}) is filled at run time by gcu::fill() (gc_units.h); in a static label the only
placeholder is {panel_version}, which becomes the substitution ${panel_version}.

The word wrap here (Roboto's metrics, greedy, as LVGL wraps) is the one worst_case_fit.py measures with;
worst_case_fit.py <language> checks every text on every layout.

usage: python tools/make_panel_texts.py [LANGUAGE ...]      (no language: every catalogue in i18n/)
       python tools/make_panel_texts.py --check [LANGUAGE ...]   (exit 1 when a generated file is not up to date)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from functools import cache
from pathlib import Path

import yaml
from PIL import ImageFont

TOOLS = Path(__file__).resolve().parent
PANELS = TOOLS.parent
CATALOGUES = PANELS / "i18n"
GENERATED = CATALOGUES / "generated"
SOURCE = "en"  # the source language: every key, and the fallback for a key missing from a translation
FONT = PANELS / ".esphome" / "font" / "Roboto@400@False@v1.ttf"  # ESPHome's copy of gfonts://Roboto (after a compile)

KEY = re.compile(r"panel(\.[a-z0-9_]+)+")
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")
YAML_PLACEHOLDERS = {"panel_version"}  # placeholders a static label can hold: ESPHome substitutions of the same name

# the arrays of gc_text.h (the order is C's: tm_wday 0 = Sunday, tm_mon 0 = January)
WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
ARRAYS = {
    "weekday": [f"panel.date.weekday.{d}" for d in WEEKDAYS],
    "weekday_short": [f"panel.date.weekday_short.{d}" for d in WEEKDAYS],
    "month": [f"panel.date.month.{m}" for m in MONTHS],
    "month_short": [f"panel.date.month_short.{m}" for m in MONTHS],
}

# ------------------------------------------------------------------ fonts
# Each font class: the characters every layout needs whatever the language (digits, symbols, and for the text fonts
# the letters of names and texts that come from GlucoCraft rather than from this catalogue: the source name, the
# weekly descriptions), and which catalogue texts add their characters (None = every text).
DIGITS = "0123456789"
LCD_SYMBOLS = " !\"#%&'()*+,-./:;<=>?@[]_°"  # "_": a name from Home Assistant can hold it
LATIN = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ"
    "ĄąĆćČčĎďĘęĚěĞğİıŁłŃńŇňŐőŒœŘřŚśŞşŠšŤťŮůŰűŹźŻżŽž"
)  # Latin-1 and the common Central European letters
FILE_SYMBOLS = "≤"  # the weekly description lines of GlucoCraft's file ("Consensus ≤36%", panel_feed.py)
FONT_CLASSES = {
    # LCD: the text fonts up to 26 px (names and GlucoCraft's texts)
    "glyphs_text": (DIGITS + LATIN[:52] + LCD_SYMBOLS + LATIN[52:] + FILE_SYMBOLS, None),
    # LCD: the larger fonts (32, 40, 56 px): the panel's own texts and numbers
    "glyphs_heading": (DIGITS + LCD_SYMBOLS, None),
    # LCD: the glucose value (a limit reading shows LO / HI) and the 5-minute delta
    "glyphs_value": ("0123456789-+.,", ["panel.value.lo", "panel.value.hi"]),
    "glyphs_delta": ("0123456789+-.,", []),
    # LCD: the clock (the 12-hour clock's words)
    "glyphs_clock": ("0123456789:-+ ", ["panel.time.am", "panel.time.pm"]),
}

# the C++ keywords: no text key may be one (its gct:: name must compile)
CPP_KEYWORDS = set(
    """alignas alignof and and_eq asm auto bitand bitor bool break case catch char char8_t char16_t char32_t class
compl concept const consteval constexpr constinit const_cast continue co_await co_return co_yield decltype
default delete do double dynamic_cast else enum explicit export extern false float for friend goto if inline int
long mutable namespace new noexcept not not_eq nullptr operator or or_eq private protected public register
reinterpret_cast requires return short signed sizeof static static_assert static_cast struct switch template
this thread_local throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t
while xor xor_eq""".split()  # noqa: SIM905 - a word list reads better as text
)


class CatalogueError(Exception):
    """A catalogue breaks a rule of the format."""


# The first line of every generated file: the license line that every GlucoCraft source file carries
LICENSE_LINE = "License: AGPL-3.0-only (LICENSE)."


def check(condition: bool, what: str) -> None:
    """Stop with an error when a check fails (unlike assert, also under python -O)."""
    if not condition:
        raise CatalogueError(what)


class _UniqueKeyLoader(yaml.SafeLoader):
    """A YAML loader that refuses a key given twice (PyYAML would keep the last one silently)."""


def _mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    keys = [loader.construct_object(k) for k, _ in node.value]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise CatalogueError(f"{node.start_mark.name}: key given twice: {', '.join(dup)}")
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def languages() -> list[str]:
    """Return every language with a catalogue in i18n/, the source language first."""
    found = sorted(p.stem for p in CATALOGUES.glob("*.yaml"))
    check(SOURCE in found, f"the source catalogue i18n/{SOURCE}.yaml is missing")
    return [SOURCE] + [lang for lang in found if lang != SOURCE]


@cache
def read_catalogue(lang: str) -> dict[str, str]:
    """Return one catalogue file as it is (its own keys only)."""
    path = CATALOGUES / f"{lang}.yaml"
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    check(isinstance(data, dict), f"{path.name}: not a mapping of keys to texts")
    for key, text in data.items():
        check(
            isinstance(key, str) and KEY.fullmatch(key) is not None,
            f"{path.name}: {key!r} is not a dotted, lower-case panel.* key",
        )
        check(isinstance(text, str), f"{path.name}: {key}: the text is not a string")
        check("$" not in text, f"{path.name}: {key}: '$' would be read as an ESPHome substitution")
    return data


def placeholders(text: str) -> set[str]:
    """Return the names of the {placeholders} of a text."""
    return set(PLACEHOLDER.findall(text))


def load(lang: str) -> dict[str, str]:
    """Return every text of a language: its catalogue, then the catalogue of its base language (pt for pt-BR), then
    English for each key either is missing. A key English does not have, or a placeholder English does not have (or
    lacks), is an error."""
    source = read_catalogue(SOURCE)
    chain = [lang] + ([lang.split("-")[0]] if "-" in lang else [])
    texts = dict(source)
    for code in reversed([c for c in chain if c != SOURCE and (CATALOGUES / f"{c}.yaml").exists()]):
        for key, text in read_catalogue(code).items():
            check(key in source, f"{code}.yaml: {key} is not a key of {SOURCE}.yaml")
            check(
                placeholders(text) == placeholders(source[key]),
                f"{code}.yaml: {key}: the placeholders {sorted(placeholders(text))} are not English's"
                f" {sorted(placeholders(source[key]))}",
            )
            texts[key] = text
    return texts


def name(key: str) -> str:
    """Return the name of a key in the generated files: panel.status.warm_up -> status_warm_up."""
    return key.removeprefix("panel.").replace(".", "_")


# ------------------------------------------------------------------ wrapping
@cache
def font(size: int) -> ImageFont.FreeTypeFont:
    """Return Roboto at size px."""
    check(FONT.exists(), f"{FONT} is missing: compile any panel once, so that ESPHome caches gfonts://Roboto")
    return ImageFont.truetype(str(FONT), size)


def wrap(text: str, size: int, room: float) -> list[str]:
    """Wrap text greedily at spaces into lines of at most room px, as LVGL does."""
    f = font(size)
    lines, cur = [], ""
    for word in text.split(" "):
        t = word if not cur else cur + " " + word
        if f.getlength(t) <= room or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    return [*lines, cur]


# ------------------------------------------------------------------ glyphs
def shown(text: str) -> str:
    """Return the characters a text shows: without its placeholders and line breaks."""
    return PLACEHOLDER.sub("", text).replace("\n", "")


def glyphs(texts: dict[str, str]) -> dict[str, str]:
    """Return each font class's glyph list: its fixed characters, then the catalogue's other characters in order."""
    every = "".join(shown(t) for t in texts.values())
    out = {}
    for cls, (base, keys) in FONT_CLASSES.items():
        used = every if keys is None else "".join(shown(texts[k]) for k in keys)
        extra = sorted(set(used) - set(base))
        out[cls] = base + "".join(extra)
    return out


# ------------------------------------------------------------------ output
def c_string(text: str) -> str:
    """Return text as a C string literal (UTF-8 kept as it is)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def yaml_string(text: str) -> str:
    """Return text as a YAML double-quoted scalar."""
    return json.dumps(text, ensure_ascii=False)


def origin(lang: str) -> str:
    """Return where a language's texts come from, for the generated files' first line."""
    return f"i18n/{lang}.yaml" + ("" if lang == SOURCE else f" (a missing key: i18n/{SOURCE}.yaml)")


def header_text(lang: str, texts: dict[str, str]) -> str:
    """Return gc_text.h for a language."""
    lines = [
        f"// {LICENSE_LINE}",
        "// =============================================================================",
        f'// gc_text.h - the GlucoCraft panel texts, language "{lang}"',
        "// =============================================================================",
        f"// Generated by tools/make_panel_texts.py from {origin(lang)}. Do not edit: change the catalogue and",
        "// regenerate. A text with {placeholders} is filled by gcu::fill() (gc_units.h).",
        "// =============================================================================",
        "#pragma once",
        "",
        "namespace gct {",
        "",
    ]
    for key, text in texts.items():
        lines.append(f"inline constexpr const char *{name(key)} = {c_string(text)};")
    lines.append("")
    lines.append("// the day and month names in C's order (tm_wday 0 = Sunday, tm_mon 0 = January)")
    for array, keys in ARRAYS.items():
        items = ", ".join(name(k) for k in keys)
        lines.append(f"inline constexpr const char *{array}[{len(keys)}] = {{{items}}};")
    lines += ["", "}  // namespace gct", ""]
    return "\n".join(lines)


def package_text(lang: str, texts: dict[str, str]) -> str:
    """Return texts.yaml (the ESPHome package of substitutions) for a language."""
    lines = [
        f"# {LICENSE_LINE}",
        f"# Generated by tools/make_panel_texts.py from {origin(lang)}. Do not edit: change the catalogue and",
        "# regenerate. The panels include it as a package (packages: texts:).",
        "substitutions:",
        "  # the static label texts (texts with run-time placeholders are in gc_text.h only)",
    ]
    for key, text in texts.items():
        if placeholders(text) <= YAML_PLACEHOLDERS:
            value = PLACEHOLDER.sub(lambda m: "${" + m.group(1) + "}", text)
            lines.append(f"  t_{name(key)}: {yaml_string(value)}")
    lines.append("  # the glyph lists of the fonts: fixed characters, then the other characters of this catalogue")
    for cls, chars in glyphs(texts).items():
        lines.append(f"  {cls}: {yaml_string(chars)}")
    return "\n".join(lines) + "\n"


def check_names(texts: dict[str, str]) -> None:
    """Every key's name must be unique, a C identifier and no C++ keyword."""
    seen: dict[str, str] = {}
    for key in texts:
        n = name(key)
        check(n not in seen, f"{key} and {seen.get(n)} have the same name {n}")
        check(n not in CPP_KEYWORDS and n not in ARRAYS and not n[0].isdigit(), f"{key}: {n} cannot be a C++ name")
        seen[n] = key


def outputs(lang: str) -> dict[Path, str]:
    """Return the generated files of a language: path -> content."""
    texts = load(lang)
    check_names(texts)
    folder = GENERATED / lang
    return {folder / "texts.yaml": package_text(lang, texts), folder / "gc_text.h": header_text(lang, texts)}


def shown_path(path: Path) -> Path:
    """Return a path for a message: relative to the panels' folder when it is in it."""
    return path.relative_to(PANELS) if path.is_relative_to(PANELS) else path


def main(argv: list[str] | None = None) -> int:
    """Write (or with --check compare) the generated files of the languages given, or of every catalogue."""
    parser = argparse.ArgumentParser(description="Generate the panel texts from the catalogues in i18n/.")
    parser.add_argument("languages", nargs="*", help="language codes (default: every catalogue in i18n/)")
    parser.add_argument("--check", action="store_true", help="only check that the generated files are up to date")
    args = parser.parse_args(argv)
    stale = []
    for lang in args.languages or languages():
        check((CATALOGUES / f"{lang}.yaml").exists(), f"i18n/{lang}.yaml does not exist")
        for path, content in outputs(lang).items():
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if args.check:
                if current != content:
                    stale.append(path)
            elif current != content:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(content)
                print("written", shown_path(path))
    for path in stale:
        print("not up to date:", shown_path(path), "(run python tools/make_panel_texts.py)")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
