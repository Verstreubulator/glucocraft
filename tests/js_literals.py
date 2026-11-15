# License: AGPL-3.0-only (LICENSE).
"""The string literals of a JavaScript file and the words in them that a person would see or hear.

literals(source) reads a card file as JavaScript does: comments, strings ('...', "..."), template literals (`...`, with
their ${...} expressions read as code, nested templates included) and regular expressions (/.../flags, told from a
division by what comes before them). Each literal comes with its line and the code just before it, so that a test can
tell a text from a key, an attribute name, a CSS rule or a log message.

visible_words(literal) returns what a literal would show: the text between its HTML tags and the values of the
attributes that are read out (aria-label, title, alt, ...), or for a literal without markup its text when that reads as
words (two words or more, without code punctuation); "" when nothing in it reads as a text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

READ_ATTRS = ("aria-label", "aria-description", "aria-valuetext", "aria-roledescription", "title", "alt", "placeholder")
# after these, a "/" starts a regular expression (otherwise it divides)
_REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^") | {""}
_REGEX_WORDS = {
    "return",
    "typeof",
    "case",
    "in",
    "of",
    "delete",
    "void",
    "throw",
    "new",
    "else",
    "do",
    "yield",
    "await",
}


@dataclass
class Literal:
    """One string or template literal of a JavaScript file, with where it stands."""

    text: str  # the literal's characters (a template literal: its static parts, each ${...} as "\x00")
    line: int  # the line it starts on
    before: str  # the code just before it (up to 200 characters, comments left out)
    template: bool


def literals(src: str) -> list[Literal]:
    """Return every string and template literal of a JavaScript source (template parts with their placeholders)."""
    out: list[Literal] = []
    code: list[str] = []  # the code read so far, without comments and literals (for the context and the "/" rule)
    i, n = 0, len(src)

    def prev_token() -> str:
        s = "".join(code[-40:]).rstrip()
        if not s:
            return ""
        m = re.search(r"[A-Za-z_$][\w$]*$", s)
        return m.group(0) if m else s[-1]

    def read_template(j: int) -> tuple[int, str]:
        """Read a template literal from just after its backtick; return (index after it, its text)."""
        text = []
        while j < n:
            c = src[j]
            if c == "\\":
                text.append(src[j : j + 2])
                j += 2
            elif c == "`":
                return j + 1, "".join(text)
            elif c == "$" and src[j + 1 : j + 2] == "{":
                text.append("\x00")
                j = read_code(j + 2, "}")
            else:
                text.append(c)
                j += 1
        return j, "".join(text)

    def read_code(j: int, close: str) -> int:
        """Read code up to its closing brace (literals inside it collected); return the index after the brace."""
        nonlocal i
        depth = 0
        while j < n:
            c = src[j]
            if c in "\"'`/":
                k = scan(j)
                if k is not None:
                    j = k
                    continue
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0:
                    return j + 1
                depth -= 1
            code.append(c)
            j += 1
        return j

    def scan(j: int) -> int | None:
        """Read a comment or a literal at j (collecting literals); return the index after it, None if neither."""
        c, nxt = src[j], src[j + 1 : j + 2]
        if c == "/" and nxt == "/":
            end = src.find("\n", j)
            return n if end < 0 else end
        if c == "/" and nxt == "*":
            end = src.find("*/", j + 2)
            return n if end < 0 else end + 2
        line = src.count("\n", 0, j) + 1
        before = "".join(code[-200:])
        if c in "\"'":
            k, text = j + 1, []
            while k < n and src[k] != c and src[k] != "\n":
                text.append(src[k : k + 2] if src[k] == "\\" else src[k])
                k += 2 if src[k] == "\\" else 1
            out.append(Literal("".join(text), line, before, False))
            code.append('""')
            return k + 1
        if c == "`":
            k, text = read_template(j + 1)
            out.insert(len(out), Literal(text, line, before, True))
            code.append('""')
            return k
        if c == "/":
            p = prev_token()
            if p in _REGEX_BEFORE or p in _REGEX_WORDS:
                k, in_class = j + 1, False
                while k < n and src[k] != "\n":
                    if src[k] == "\\":
                        k += 2
                        continue
                    if src[k] == "[":
                        in_class = True
                    elif src[k] == "]":
                        in_class = False
                    elif src[k] == "/" and not in_class:
                        break
                    k += 1
                k += 1
                while k < n and src[k].isalpha():
                    k += 1
                code.append("/r/")
                return k
        return None

    while i < n:
        k = scan(i) if src[i] in "\"'`/" else None
        if k is None:
            code.append(src[i])
            i += 1
        else:
            i = k
    return out


TAG = re.compile(r"<(/?)([A-Za-z][\w-]*)((?:[^<>\"']|\"[^\"]*\"|'[^']*')*)>")
ATTR = re.compile(r"\b(" + "|".join(READ_ATTRS) + r")\s*=\s*(\"[^\"]*\"|'[^']*')")
CODE_PUNCTUATION = re.compile(r"[{}();=<>\[\]\\|^$]|=>|&&|\|\|")


def _words(text: str) -> str:
    """The words of a text: without placeholders, entities, numbers and symbols, and with single spaces."""
    text = re.sub(r"\x00|&[#\w]+;", " ", text)
    return " ".join(re.findall(r"[^\W\d_]{2,}(?:['’][^\W\d_]+)*", text))


def visible_words(lit: Literal) -> str:
    """Return the words a literal shows (see the module's docstring); "" for none."""
    text = lit.text
    if TAG.search(text) or re.search(r"(?:^|\x00)[^<>]*>|<[^<>]*$", text):  # markup (maybe split by ${...})
        if re.match(r"\s*<(?:style|svg|path|defs|linearGradient|stop|g)\b", text) and ">" not in TAG.sub("", text):
            return ""
        shown = [m.group(2)[1:-1] for m in ATTR.finditer(text)]
        body = re.sub(r"<style\b.*?</style>", " ", text, flags=re.DOTALL)
        body = TAG.sub("\x00", body)
        body = re.sub(r"^[^<>\x00]*?(?:\b[\w-]+=\"[^\"]*\"\s*)+/?>", " ", body)  # an opening tag split by ${...}
        body = re.sub(r"<[^<>]*$", " ", body)
        shown.append(body)
        return _words(" ".join(shown))
    if CODE_PUNCTUATION.search(text.replace("\x00", "")) or not re.search(r"\s", text.strip()):
        return ""
    words = _words(text)
    return words if len(words.split()) >= 2 else ""
