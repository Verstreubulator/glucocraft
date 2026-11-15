# License: AGPL-3.0-only (LICENSE).
r"""Generate the test language "pseudo" from the English catalogues: every text accented, about 35 % longer and between
the markers « and », so that a text which is not in a catalogue, or which no longer fits, is visible at a glance.

  "Glucose"                -> "«Glüçöšé»"
  "No current data"        -> "«Ñöö çüürréñt dáátá»"
  "<b>Data source.</b> It is" -> "<b>«Dáátáá šöüürçé.</b> ÍÍt íš»"   (placeholders, plural keys and markup kept)

Written, from i18n/en/ of the integration and from the panels' i18n/en.yaml:
  custom_components/glucocraft/i18n/pseudo/<area>.json   every area of English (backend, report, frontend,
                                                         documentation), the same keys
  <panels>/i18n/pseudo.yaml                              the panel texts, the same keys; then
                                                         <panels>/tools/make_panel_texts.py pseudo writes
                                                         <panels>/i18n/generated/pseudo/

Rules of a pseudo text:
  - the letters a c e i n o s u y z (and capitals) are replaced by accented letters that Roboto (the cards and panels)
    and Helvetica's WinAnsi set (the PDF) both have, so that a missing glyph shows a font problem, not a pseudo one;
  - about 35 % longer, by doubling vowels spread over the text (as a longer language would be);
  - kept exactly: placeholders {name}, the link tokens {cite:...}, {ref:...}, {ref_year:...}, {sec:...}, Jinja
    ({{ }}, {% %}), HTML tags and entities, <code> and <pre> blocks, Markdown code spans, link targets and structure
    (list prefixes, table pipes), web addresses, entity ids, and names printed on hardware (upper case with an underscore);
  - wrapped in « » (Roboto and Helvetica have both; they have no ⟦ ⟧), placed inside the markup: after leading tags
    and list prefixes, before trailing tags, per table cell; a text without a letter (a separator such as "; ") is
    left as it is.

Use (developers only; "pseudo" is never offered to users):
  python tools/i18n/make_pseudo.py              write the files above (the panels' folder: --panels, else the
                                                environment variable GLUCOCRAFT_PANELS, else esphome/ in the repository)
  python tools/i18n/make_pseudo.py --clean      remove every generated pseudo file again
  python tools/i18n/make_pseudo.py --out DIR    write the same catalogues into DIR only (DIR/<area>.json and
                                                DIR/panels/pseudo.yaml), for a check that changes neither the
                                                repository nor the panels' folder (make_panel_texts.py is not run)
then choose it with a developer override: the environment variable GLUCOCRAFT_LANGUAGE=pseudo (the backend),
localStorage.glucocraft_language = "pseudo" in the browser (the cards), the substitution "language: pseudo" (a panel
build).

The generated files are not in the repository (.gitignore): they are a function of English, so a committed copy could
only fall out of date, and a file that is not in the repository is never in a release. The tests make the texts in
memory (pseudo_catalogue()); this command writes them for a manual run of the cards, the backend or a panel build.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
I18N = REPO / "custom_components" / "glucocraft" / "i18n"
SOURCE, LANGUAGE = "en", "pseudo"
OPEN, CLOSE = "«", "»"
GROWTH = 0.35  # the share by which a text grows (markers included)

# accented letters in both Roboto and WinAnsi (Helvetica in the PDF): Latin-1, plus š and ž
ACCENTS = str.maketrans("aceinosuyzACEINOSUYZ", "áçéíñöšüýžÁÇÉÍÑÖŠÜÝŽ")
VOWELS = frozenset("áéíöüýÁÉÍÖÜÝ")  # the doubled letters (after accenting)
ENTITY_DOMAINS = "sensor|binary_sensor|input_number|input_select|input_boolean|switch|button|number|select|notify|event"

# the parts of a text that stay exactly as written, tried in this order
KEEP = re.compile(
    "|".join(
        (
            r"<(code|pre)\b[^>]*>.*?</\1>",  # code samples (entity ids, YAML)
            r"`[^`\n]*`",  # Markdown code spans
            r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}",  # Jinja
            r"\{(?:cite|ref|ref_year|sec):[^{}]*\}",  # the Documentation's link tokens
            r"\{[a-z_][a-z0-9_]*\}",  # placeholders
            r"</?[A-Za-z][\w-]*(?:\s[^<>]*)?/?>",  # HTML tags
            r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z]+);",  # HTML entities
            r"\]\([^()\s]*\)",  # Markdown link targets
            r"https?://[^\s<>()\]]+",  # web addresses
            r"\b[\w-]+(?:\.[\w-]+)*\.(?:org|com|net|io|html)\b(?:/[\w./-]*)?",  # bare addresses (gnu.org/licenses/...)
            rf"\b(?:{ENTITY_DOMAINS})\.[a-z0-9_]+",  # entity ids
            r"\b[A-Z0-9]+_[A-Z0-9_]+\b",  # names printed on hardware (KEY_A)
        )
    ),
    re.DOTALL,
)
TAG = re.compile(r"</?[A-Za-z][\w-]*(?:\s[^<>]*)?/?>")  # an HTML tag (the markers stay inside it)
LEAD = re.compile(r"^(\s*(?:[-*+] |\d+\. |#{1,6} |> )*)")  # a Markdown line's list, heading or quote prefix
BLOCK_LINE = re.compile(r"\n\s*(?:[-*+] |\d+\. |#{1,6} |> |\|)")  # a text whose lines are Markdown blocks
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_RULE = re.compile(r"^[\s|:-]+$")
CELL_SPLIT = re.compile(r"\|(?![^{]*\})")  # a table pipe, not one inside a {token|text}


def _segments(text: str) -> list[tuple[bool, str]]:
    """Split a text into (kept, part) pairs: kept parts are written as they are, the others are pseudo-localised."""
    out, pos = [], 0
    for m in KEEP.finditer(text):
        if m.start() > pos:
            out.append((False, text[pos : m.start()]))
        out.append((True, m.group(0)))
        pos = m.end()
    if pos < len(text):
        out.append((False, text[pos:]))
    return out


def _unit(text: str) -> str:
    """Return one unit of text (a whole text, a Markdown line or a table cell) accented, longer and between markers."""
    segs = [(kept, part if kept else part.translate(ACCENTS)) for kept, part in _segments(text)]
    if not any(not kept and any(c.isalpha() for c in part) for kept, part in segs):
        return text  # no letter of its own: a separator, a lone placeholder
    visible = sum(len(part) for kept, part in segs if not kept)
    extra = max(0, round(GROWTH * visible) - 2)
    vowels = [(i, j) for i, (kept, part) in enumerate(segs) if not kept for j, c in enumerate(part) if c in VOWELS]
    doubled: dict[tuple[int, int], int] = {}
    if vowels:
        for n in range(extra):  # spread the extra letters evenly over the vowels
            spot = vowels[(n * len(vowels)) // extra if extra <= len(vowels) else n % len(vowels)]
            doubled[spot] = doubled.get(spot, 0) + 1
    out = [
        part if kept else "".join(c * (1 + doubled.get((i, j), 0)) for j, c in enumerate(part))
        for i, (kept, part) in enumerate(segs)
    ]
    # the markers go around the content (words, placeholders, tokens): inside leading and trailing tags and spaces
    content = [i for i, (kept, part) in enumerate(segs) if part.strip() and not (kept and TAG.fullmatch(part))]
    first, last = content[0], content[-1]
    body = out[last].rstrip()
    out[last] = body + ("" if vowels else "~" * extra) + CLOSE + out[last][len(body) :]
    body = out[first].lstrip()
    out[first] = out[first][: len(out[first]) - len(body)] + OPEN + body
    return "".join(out)


def _line(line: str) -> str:
    """Return one Markdown line pseudo-localised: a table row cell by cell, any other line after its prefix."""
    if TABLE_ROW.match(line):
        if TABLE_RULE.match(line):
            return line
        return "|".join(_unit(cell) if cell.strip() else cell for cell in CELL_SPLIT.split(line))
    lead = LEAD.match(line).group(1)
    return lead + _unit(line[len(lead) :])


def pseudo_text(text: str) -> str:
    """Return the pseudo text of an English text."""
    if not any(c.isalpha() for kept, part in _segments(text) if not kept for c in part):
        return text
    multiline_kept = any(kept and "\n" in part for kept, part in _segments(text))
    if (BLOCK_LINE.search(text) or TABLE_ROW.match(text.split("\n", 1)[0])) and not multiline_kept:
        return "\n".join(_line(line) for line in text.split("\n"))  # Markdown blocks: each line its own text
    lead = LEAD.match(text).group(1) if not text.startswith("<") else ""
    return lead + _unit(text[len(lead) :])


def pseudo_value(value: object) -> object:
    """Return a catalogue value pseudo-localised: a text, a text in parts (each part), or a nested object."""
    if isinstance(value, str):
        return pseudo_text(value)
    if isinstance(value, list):
        return [pseudo_value(v) for v in value]
    if isinstance(value, dict):
        return {k: pseudo_value(v) for k, v in value.items()}
    return value


def pseudo_catalogue(catalogue: dict) -> dict:
    """Return a whole catalogue pseudo-localised: the same keys in the same order, every text converted."""
    return {key: pseudo_value(value) for key, value in catalogue.items()}


# ------------------------------------------------------------------------------------------------ the files
def panels_folder(given: str | None) -> Path | None:
    """Return the panels' folder (the one with i18n/en.yaml), or None when there is none."""
    for candidate in (given, os.environ.get("GLUCOCRAFT_PANELS"), REPO / "esphome"):
        if candidate and (Path(candidate) / "i18n" / f"{SOURCE}.yaml").is_file():
            return Path(candidate)
    return None


def _panel_yaml(texts: dict[str, str]) -> str:
    lines = [
        "# Generated by tools/i18n/make_pseudo.py (GlucoCraft repository) from i18n/en.yaml: the test language",
        "# pseudo, never used for a user. Do not edit; do not commit (.gitignore). Build with the substitution",
        "# language: pseudo after python tools/make_panel_texts.py pseudo.",
    ]
    lines += [f"{key}: {json.dumps(text, ensure_ascii=False)}" for key, text in texts.items()]
    return "\n".join(lines) + "\n"


def write(panels: Path | None, out_dir: Path | None = None) -> list[Path]:
    """Write every pseudo catalogue (into out_dir alone when given); return the files written."""
    written = []
    target = out_dir if out_dir is not None else I18N / LANGUAGE
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted((I18N / SOURCE).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out = target / path.name
        out.write_text(json.dumps(pseudo_catalogue(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(out)
    if panels is not None:
        import yaml  # only the panels' catalogue needs PyYAML

        source = yaml.safe_load((panels / "i18n" / f"{SOURCE}.yaml").read_text(encoding="utf-8"))
        out = (out_dir / "panels" if out_dir is not None else panels / "i18n") / f"{LANGUAGE}.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(_panel_yaml(pseudo_catalogue(source)))
        written.append(out)
        if out_dir is not None:
            return written
        run = subprocess.run(
            [sys.executable, str(panels / "tools" / "make_panel_texts.py"), LANGUAGE], check=False, cwd=panels
        )
        if run.returncode:
            raise SystemExit(f"make_panel_texts.py {LANGUAGE} failed ({run.returncode})")
    return written


def clean(panels: Path | None) -> list[Path]:
    """Remove every generated pseudo file; return what was removed."""
    gone = [p for p in (I18N / LANGUAGE,) if p.exists()]
    if panels is not None:
        gone += [
            p for p in (panels / "i18n" / f"{LANGUAGE}.yaml", panels / "i18n" / "generated" / LANGUAGE) if p.exists()
        ]
    for p in gone:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    return gone


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate (or remove) the test language pseudo from English.")
    parser.add_argument("--panels", help="the panels' folder (with i18n/en.yaml)")
    parser.add_argument("--no-panels", action="store_true", help="only the integration's catalogues")
    parser.add_argument("--clean", action="store_true", help="remove the generated pseudo files")
    parser.add_argument(
        "--out", help="write the catalogues into this folder only (no file in the repository or panels)"
    )
    args = parser.parse_args(argv)
    panels = None if args.no_panels else panels_folder(args.panels)
    out_dir = Path(args.out) if args.out else None
    for path in clean(panels) if args.clean else write(panels, out_dir):
        print("removed" if args.clean else "written", path)
    if panels is None and not args.no_panels:
        print("no panels' folder found (--panels or GLUCOCRAFT_PANELS): the panel texts were left out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
