# License: AGPL-3.0-only (LICENSE).
"""Check that the worst-case texts of a language fit on every panel layout.

Widths are measured with the panels' font (Roboto, as ESPHome caches it after a compile); each limit is the room the
text has on the screen. Covered: the landscape layouts of make_glucocraft_landscape.py and the square panel
(glucocraft-panel.yaml). The trend word of the TREND column (GlucoCraft's setting Trend shown as: word) is wrapped at
the column's width as LVGL wraps it: every line must fit, in at most two lines, between the column's label and the
source's name. Every text comes from the language's text catalogue (i18n/<language>.yaml, a missing key in
English), the fixed line breaks from make_panel_texts.py, which makes them; a text with values is measured with its
widest values ("Reading: 99 min ago", "Reading: 9999 h ago"). Without a current reading the glucose value's place
shows a stack: the words ("Sensor warming up", "Sensor expired", "No current data"), wrapped at the zone word's width
at its large size as LVGL wraps them (two lines at most), the last reading ("Last reading: 22.2 mmol/L" at its
widest) and when it was taken ("Yesterday at 12:59 PM (23 h 59 min ago)", the widest date with "999 d 23 h ago"),
on one line, or split before the bracket where the layout says so; every line must fit the width, and the stack
must fit between the value's top and the TREND and 5 MIN DELTA columns. A current limit reading shows the limit
screen in the same place (the zone, the sensor range, since when, the last measured reading), and after an hour
without Home Assistant or its data the screen below the header shows four lines: both are stacks of lines wrapped at
their width as LVGL wraps them (each wrapped line listed: a text that wraps is never shortened), within their height.
"Check Home Assistant" in the status bar's middle has its own size where the bar's is too large. The period graphs'
key and title are measured together (they share the top of the graph).

Each layout lists the original checks first and then the other texts of the catalogue ("all texts"); the graph axes'
labels (Roboto 14, whose glyphs come from the catalogue like every other font's) are placed only where they fit, so
they are not measured. The line under a weekly page's title comes from GlucoCraft's file, not from the catalogue:
at most 63 bytes (panel_feed.py), measured as 63 digits, which are wider than Roboto's average letter. A text too
wide is a finding to report, not a reason to change a layout.

usage: python tools/worst_case_fit.py [LANGUAGE]      (default: en)
"""

from __future__ import annotations

import sys

import make_panel_texts as mpt  # the text generator, next to this script (on the path when run)
from make_glucocraft_landscape import LAYOUTS, about_text_width

font = mpt.font
wrap = mpt.wrap


class FitError(Exception):
    """A configuration does not contain the texts this check expects."""


def check(condition: bool, what: str) -> None:
    """Stop with an error when a check fails (unlike assert, also under python -O)."""
    if not condition:
        raise FitError(what)


def px(font_id: str) -> int:
    """Return the pixel size of a font id such as roboto_24."""
    return int(font_id.split("_")[1])


def width(text: str, font_id: str) -> float:
    """Return the drawn width of text in the font font_id."""
    return font(px(font_id)).getlength(text)


def line_height(size: int) -> int:
    """Return LVGL's line height for these fonts: ascent + descent."""
    return sum(font(size).getmetrics())


def fill(text: str, **values: object) -> str:
    """Return a catalogue text with its placeholders filled (as gcu::fill does on the panel)."""
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


class Texts:
    """The texts of one language, and the sample texts the checks measure."""

    def __init__(self, lang: str) -> None:
        self.lang = lang
        t = self.t = mpt.load(lang)
        self.zone_words = [
            t[f"panel.word.zone.{z}"]
            for z in ("very_low", "low", "in_range", "tight_range", "high", "very_high", "sensor_limit")
        ]
        self.trend_words = [
            t[f"panel.word.trend.{w}"]
            for w in ("changing_slowly", "falling_quickly", "rising_quickly", "falling", "rising")
        ]
        self.ver = fill(t["panel.about.version"], panel_version="0.00.00")  # the widest version line
        self.copyright = t["panel.about.copyright"].split("\n")  # the copyright line's lines (the catalogue's breaks)
        self.name, self.line = t["panel.startup.name"], t["panel.startup.line"]
        self.startup_ver = fill(t["panel.startup.version"], panel_version="0.00.00")
        self.lo, self.hi = t["panel.value.lo"], t["panel.value.hi"]
        self.pm = fill(t["panel.time.pm"], time="12:59")
        self.am = fill(t["panel.time.am"], time="12:59")
        self.no_reading = [t[f"panel.no_reading.{k}"] for k in ("warming_up", "sensor_expired", "no_current_data")]
        self.last_readings = [
            fill(t["panel.no_reading.last_reading"], value=v, unit=u) for v, u in (("22.2", "mmol/L"), ("400", "mg/dL"))
        ] + [fill(t["panel.no_reading.last_reading_limit"], value=v) for v in (self.lo, self.hi)]
        self.dec = t["panel.weekly.direction.decreasing"]
        self.directions = [t[f"panel.weekly.direction.{d}"] for d in ("increasing", "decreasing", "stable")]
        self.file_line = "0" * 63  # the widest weekly description line of the file (63 bytes), see the docstring

    def __getitem__(self, key: str) -> str:
        return self.t["panel." + key]

    def widest(self, texts: list[str], font_id: str) -> str:
        """Return the widest of texts in a font."""
        return max(texts, key=lambda s: width(s, font_id))

    def reading_times(self, font_id: str) -> tuple[list[str], list[str], list[str]]:
        """Return the last reading's time at its widest: [when (age)] on one line, and split: [when], [(age)].

        The time as the widest clock ("12:59 PM" or "23:59"), the date as the widest short month with day 30; the
        age as the widest each form can have: today and yesterday under a day "23 h 59 min ago", yesterday from a day
        "1 d 23 h ago", an older date "999 d 23 h ago" (a reading kept for years).
        """
        t = self.t
        clock = self.widest([self.am, self.pm, "23:59"], font_id)
        date = self.month_day(font_id, short=True)
        today = fill(t["panel.no_reading.when_today"], time=clock)
        yesterday = fill(t["panel.no_reading.when_yesterday"], time=clock)
        older = fill(t["panel.no_reading.when_date"], date=date, time=clock)
        hours = fill(t["panel.no_reading.ago_hours"], hours=23, minutes=59)
        days1 = fill(t["panel.no_reading.ago_days"], days=1, hours=23)
        days = fill(t["panel.no_reading.ago_days"], days=999, hours=23)
        minutes = fill(t["panel.no_reading.ago_minutes"], minutes=59)
        pairs = [(today, hours), (today, minutes), (yesterday, hours), (yesterday, days1), (older, days)]
        one_line = [fill(t["panel.no_reading.time_ago"], when=w, ago=a) for w, a in pairs]
        whens = [today, yesterday, older]
        agos = [fill(t["panel.no_reading.ago_line"], ago=a) for a in (hours, days1, days, minutes)]
        return one_line, whens, agos

    def day_times(self, font_id: str) -> list[str]:
        """Return a time within a line at its widest on each day (gc_units.h day_time_at): today "12:59 PM", yesterday
        "yesterday at 12:59 PM", earlier "Sep 30 at 12:59 PM"."""
        clock = self.widest([self.am, self.pm, "23:59"], font_id)
        return [
            clock,
            fill(self["limit_screen.when_yesterday"], time=clock),
            fill(self["no_reading.when_date"], date=self.month_day(font_id, short=True), time=clock),
        ]

    def since(self, font_id: str) -> str:
        """Return "Since ... (...)" at its widest (the limit screen, the screen without Home Assistant): today with the
        longest time under a day ("23 h 59 min"), yesterday with a day or more ("1 d 23 h"), an earlier day with
        "999 d 23 h"; before the clock is set "--:--"."""
        today, yesterday, older = self.day_times(font_id)
        hours = fill(self["duration.hours"], hours=23, minutes=59)
        lines = [
            fill(self["since.line"], time=t, duration=d)
            for t, d in (
                (today, hours),
                (self["header.clock_initial"], hours),
                (yesterday, hours),
                (yesterday, fill(self["duration.days"], days=1, hours=23)),
                (older, fill(self["duration.days"], days=999, hours=23)),
            )
        ]
        return self.widest(lines, font_id)

    def limit_screen(self, title_font: str, time_font: str) -> list[tuple[list[str], str]]:
        """Return the limit screen's four lines (gc_units.h limit_screen) at their widest, each as (texts, font id):
        the zone, the sensor range, since when, the last measured reading (with and without its time)."""
        today, *earlier = self.day_times(time_font)
        values = (("22.2", "mmol/L"), ("400", "mg/dL"))
        measured = (
            [fill(self["limit_screen.last_measured"], value=v, unit=u, time=today) for v, u in values]
            + [
                fill(self["limit_screen.last_measured_day"], value=v, unit=u, when=w)
                for v, u in values
                for w in earlier
            ]
            + [fill(self["limit_screen.last_measured_value"], value="22.2", unit="mmol/L")]
        )
        return [
            ([self["limit_screen.low.title"], self["limit_screen.high.title"]], title_font),
            (
                [
                    fill(self["limit_screen.low.range"], limit=self.lo),
                    fill(self["limit_screen.high.range"], limit=self.hi),
                ],
                "line",
            ),
            ([self.since(time_font)], time_font),
            (measured, time_font),
        ]

    def no_connection(self, line_font: str) -> list[list[str]]:
        """Return the lines of the screen without Home Assistant (gc_units.h no_connection_screen) at their widest:
        the title, since when, the last reading (with its time, as LO / HI, and without a time), what to check."""
        clock = self.widest([self.am, self.pm, "23:59"], line_font)
        readings = (
            [
                fill(self["no_connection.last_reading"], value=v, unit=u, time=clock)
                for v, u in (("22.2", "mmol/L"), ("400", "mg/dL"))
            ]
            + [fill(self["no_connection.last_reading_limit"], value=self.lo, time=clock)]
            + self.last_readings
        )
        return [[self["no_connection.title"]], [self.since(line_font)], readings, [self["no_connection.check"]]]

    def month_day(self, font_id: str, short: bool = False) -> str:
        """Return the widest day and month ("September 30"), in either date order."""
        names = [self[f"date.month{'_short' if short else ''}.{m}"] for m in mpt.MONTHS]
        return self.widest([f"{m} 30" for m in names] + [f"30 {m}" for m in names], font_id)

    def status(self) -> tuple[list[str], list[str], list[str]]:
        """Return the status bar's left, middle and right texts at their widest."""
        # the age of the reading: up to 99 minutes, then hours (9999 h: more than a year, the widest a display shows)
        left = [
            fill(self["status.reading_minutes"], minutes=99),
            fill(self["status.reading_hours"], hours=9999),
            self["status.reading_none"],
        ]
        middle = [
            fill(self["status.warm_up_minutes"], minutes=120),
            self["status.warm_up"],
            self["status.custom_ranges"],
        ]
        right = [
            fill(self["status.expiry_days"], days=14, hours=23),
            fill(self["status.expiry_hours"], hours=23, minutes=59),
            fill(self["status.expiry_minutes"], minutes=59),
            self["status.expired"],
            self["status.expiry_none"],
        ]
        return left, middle, right

    def weekly_status(self) -> list[str]:
        """Return the weekly status line at its widest, every direction."""
        out = []
        for d in self.directions:
            out += [
                fill(self["weekly.status.glucose"], value="400", unit="mg/dL", direction=d),
                fill(self["weekly.status.glucose"], value="22.2", unit="mmol/L", direction=d),
                fill(self["weekly.status.min_max"], min="22.2", max="27.8", direction=d),
                fill(self["weekly.status.min_max"], min="400", max="400", direction=d),
                fill(self["weekly.status.value"], value="100.0%", direction=d),
                # two decimals beside a target or goal (gc_units.h pct_against): at most 99.99% (a goal of 100%)
                fill(self["weekly.status.value"], value="99.99%", direction=d),
            ]
        return out + [self["weekly.status.loading"], self["weekly.status.no_data"]]

    PAGES = ("tir", "titr", "mean", "cv", "gmi", "time_low", "time_high", "sd", "min_max")
    STATS = (
        "tir",
        "titr",
        "low",
        "high",
        "very_low",
        "very_high",
        "mean",
        "sd",
        "cv",
        "min",
        "max",
        "gmi_14d",
        "gmi_30d",
        "gmi_90d",
        "gmi_180d",
    )
    GRAPHS = ("6h", "24h", "7d", "14d", "30d", "90d", "180d")
    LOADING = (
        "initializing",
        "downloading",
        "readings",
        "graph_24h",
        "graph_7d",
        "graph_14d",
        "graph_30d",
        "graph_90d",
        "graph_180d",
        "drawing",
        "failed",
    )


def screens(tx: Texts, W, H, square, top, bottom, icon, igap, hf, tf, vf, gap, SW, shf, sf, svf, sgap) -> int:
    """Check About page 16 and the start-up screen (centered); return the number too large.

    The About page: the square panel stacks the icon, the title and the disclaimer (gap apart) above the footer, the
    text 92 % of the screen wide; a wide layout puts the icon in a column of its own and the title, the disclaimer and
    the footer in the column to its right (about_text_width()). The footer: a thin line 9 px high, then the version
    line and the copyright line at the version's size, 4 px apart. Every line within its width, all within H (heights =
    lines x (ascent + descent) + the gaps; the margins top and bottom). The start-up screen: each line of the
    catalogue's start-up line (a sentence) must fit on one line of its own.
    """
    n = 0
    lh = line_height
    TW = int(W * 0.92) if square else W - 2 * top - icon - igap
    title = wrap(tx["about.title"], hf, TW)
    disc = wrap(tx["about.disclaimer"], tf, TW)
    text_h = lh(hf) * len(title) + gap + lh(tf) * len(disc)
    footer = 9 + 4 + lh(vf) + 4 + len(tx.copyright) * lh(vf) + 4 * (len(tx.copyright) - 1)
    total = top + (icon + gap + text_h + footer if square else max(icon, text_h + footer)) + bottom
    widest = max(
        [width(x, f"r_{hf}") for x in title]
        + [width(x, f"r_{tf}") for x in disc]
        + [width(x, f"r_{vf}") for x in [tx.ver, *tx.copyright]]
    )
    ok = total <= H and widest <= TW
    n += not ok
    print(
        f"  {'OK ' if ok else 'TOO'} About page 16 ({'icon on top' if square else 'icon column'}): the title"
        f" {len(title)} line(s) at {hf} px, the disclaimer {len(disc)} lines at {tf} px, the footer"
        f" ({1 + len(tx.copyright)} lines) at {vf} px; widest {widest:.0f} / {TW}; {total} of {H} px"
    )
    parts = tx.line.split("\n")
    lines = [ln for part in parts for ln in wrap(part, sf, SW)]
    total = icon + sgap + lh(shf) + sgap + lh(svf) + sgap + len(lines) * lh(sf)
    widest = max([width(x, f"r_{sf}") for x in lines] + [width(tx.name, f"r_{shf}"), width(tx.startup_ver, f"r_{svf}")])
    ok = total <= H - 8 and widest <= SW and len(lines) == len(parts)
    n += not ok
    print(
        f"  {'OK ' if ok else 'TOO'} start-up screen: the icon {icon} px, the name at {shf} px, the version at {svf} px,"
        f" the disclaimer line {len(lines)} lines at {sf} px, one per sentence: {len(lines) == len(parts)}"
        f" (widest {widest:.0f} / {SW}); {total} of {H} px"
    )
    return n


def word_font_for(text: str, big: str, small: str, room: int) -> str:
    """Return the font of the zone word under the value: the smaller one where the larger is wider than its label
    (room px), as the panels choose it (lvgl_compat.h lvc_font_that_fits)."""
    return small if width(text, big) > room else big


def trend_word(tx: Texts, font_id: str, room: int, center: float, top: float, bottom: float) -> int:
    """Check the trend word of the TREND column; return the number of words that do not fit.

    Each trend word is wrapped at room px (the label's width) as LVGL wraps it: every line within room, at most two
    lines, and the lines (centered at center, LVGL's line height each) between top (the end of the column's label) and
    bottom (the start of the source's name), all in the column's own coordinates.
    """
    bad = 0
    for text in tx.trend_words:
        lines = wrap(text, px(font_id), room)
        widest = max(width(line, font_id) for line in lines)
        h = len(lines) * line_height(px(font_id))
        ok = widest <= room and len(lines) <= 2 and center - h / 2 >= top and center + h / 2 <= bottom
        bad += not ok
        print(
            f"  {'OK ' if ok else 'TOO'} {widest:5.0f} / {room:5.0f}  {font_id:<11} trend word, {len(lines)} line(s),"
            f" y {center - h / 2:.0f}-{center + h / 2:.0f} within {top:.0f}-{bottom:.0f}: {' / '.join(lines)}"
        )
    return bad


def no_reading(tx: Texts, fonts: tuple[str, str, str], split: bool, room: int, top: float, bottom: float) -> int:
    """Check the stack in the glucose value's place without a current reading; return the number that do not fit.

    fonts: the words' (the zone word's large size), the last reading's and its time's; split: the time's age on a
    line of its own. Each line within room px (the words wrapped as LVGL wraps them, at most two lines), and the
    tallest stack (the words' most lines, the last reading, the time's lines; LVGL's line height each) between top
    (the value's top) and bottom (the start of the TREND and 5 MIN DELTA columns), in the glucose block's
    coordinates.
    """
    word, line_font, time_font = fonts
    bad, most = 0, 1
    for text in tx.no_reading:
        lines = wrap(text, px(word), room)
        widest = max(width(line, word) for line in lines)
        ok = widest <= room and len(lines) <= 2
        most = max(most, len(lines))
        bad += not ok
        print(
            f"  {'OK ' if ok else 'TOO'} {widest:5.0f} / {room:5.0f}  {word:<11} no current reading, {len(lines)}"
            f" line(s): {' / '.join(lines)}"
        )
    one_line, whens, agos = tx.reading_times(time_font)
    checks = [("last reading", s, line_font, room) for s in tx.last_readings]
    if split:
        checks += [("last reading's time", s, time_font, room) for s in whens]
        checks += [("last reading's age", s, time_font, room) for s in agos]
    else:
        checks += [("last reading's time and age", s, time_font, room) for s in one_line]
    bad += print_checks(checks)
    h = most * line_height(px(word)) + line_height(px(line_font)) + (2 if split else 1) * line_height(px(time_font))
    ok = h <= bottom - top
    bad += not ok
    print(f"  {'OK ' if ok else 'TOO'} no current reading, the stack {h} px high within {top:.0f}-{bottom:.0f}")
    return bad


def stack(what: str, groups: list[tuple[list[str], str]], room: int, height: int, gap: int = 0) -> int:
    """Check a centered stack of wrapped lines; return the number that do not fit.

    groups: (texts, font id) per line of the stack, each text wrapped at room px as LVGL wraps it (one word wider than
    room cannot wrap: too wide); a line takes the most wrapped lines of its texts. The stack (LVGL's line height per
    wrapped line, gap px between the lines) must fit height px. The wrapped lines are listed, so that a text that wraps
    is visible (never shortened).
    """
    bad, total = 0, 0
    for texts, font_id in groups:
        most = 1
        for text in texts:
            lines = wrap(text, px(font_id), room)
            widest = max(width(line, font_id) for line in lines)
            ok = widest <= room
            bad += not ok
            most = max(most, len(lines))
            print(
                f"  {'OK ' if ok else 'TOO'} {widest:5.0f} / {room:5.0f}  {font_id:<11} {what}, {len(lines)} line(s):"
                f" {' / '.join(lines)}"
            )
        total += most * line_height(px(font_id))
    total += gap * (len(groups) - 1)
    ok = total <= height
    bad += not ok
    print(f"  {'OK ' if ok else 'TOO'} {what}, the stack {total} px high within {height}")
    return bad


def title_and_key(tx: Texts, graph_w: int, title_right: int) -> int:
    """Check, on a period graph graph_w px wide, that the key (left-aligned at x 50, roboto_14) and the title
    (right-aligned, ending at title_right, roboto_16) keep 4 px apart, measured together; return 1 when they do not."""
    key_end = 50 + max(width(tx[f"graph.legend_{k}"], "roboto_14") for k in ("range", "average"))
    title = tx.widest([tx[f"graph.title_{g}"] for g in Texts.GRAPHS if g != "6h"], "roboto_16")
    title_start = title_right - width(title, "roboto_16")
    ok = key_end + 4 <= title_start
    print(
        f"  {'OK ' if ok else 'TOO'} graph key ends {key_end:.0f}, title starts {title_start:.0f} (graph {graph_w} px):"
        f" {title}"
    )
    return 0 if ok else 1


def print_checks(checks: list) -> int:
    """Print each (what, text, font, limit) width check; return the number too wide."""
    bad = 0
    for what, text, font_id, lim in checks:
        for line in text.split("\n"):
            w = width(line, font_id)
            ok = w <= lim
            bad += not ok
            print(f"  {'OK ' if ok else 'TOO'} {w:5.0f} / {lim:5.0f}  {font_id:<11} {what}: {line}")
    return bad


def all_texts(tx: Texts, W: int, edge: int, block: int, fonts: dict) -> list:
    """Return the checks of the texts the original checks leave out, for an LCD layout.

    fonts: the layout's font per role; block: the glucose block's width (the loading texts use the screen's).
    """
    left, middle, right = tx.status()
    sb = fonts["sb"]
    # "Check Home Assistant" takes turns in the middle while a notice is open in Repairs, in its own size (sb_check:
    # one size below the bar's on the 480 px wide layouts, where the bar's size does not leave it the room)
    check, sb_check = tx["status.check_home_assistant"], fonts.get("sb_check", sb)
    middle_w = max(max(width(t, sb) for t in middle), width(check, sb_check))
    room_side = W // 2 - edge - middle_w / 2 - 6
    room_mid = W - 2 * (edge + max(width(t, sb) for t in right) + 6)
    out = [("status left", t, sb, room_side) for t in left] + [("status middle", t, sb, room_mid) for t in middle]
    out += [("status middle", check, sb_check, room_mid)]
    out += [("status right", t, sb, room_side) for t in right]
    out += [("loading", tx[f"loading.{k}"], fonts["load"], W - 8) for k in Texts.LOADING]
    # the clock (left) and the date (right, the weekday above the day and month): each in its half of the header, or
    # (the square panel) side by side with 8 px between them
    clock = tx.widest([tx.am, tx.pm], fonts["clock"])
    dates = [tx.month_day(fonts["date"]), tx.widest([tx[f"date.weekday.{d}"] for d in mpt.WEEKDAYS], fonts["date"])]
    if fonts.get("header_side_by_side"):
        hx = fonts["header_edge"]
        room_clock = W - 2 * hx - 8 - max(width(t, fonts["date"]) for t in dates)
        room_date = W - 2 * hx - 8 - width(clock, fonts["clock"])
    else:
        room_clock = room_date = W // 2 - edge
    out += [("clock", clock, fonts["clock"], room_clock)] + [("date", t, fonts["date"], room_date) for t in dates]
    out += [("TREND label", tx["glucose.trend_label"], fonts["col_lbl"], fonts["col"] - 4)]
    out += [("stat label", tx[f"stat.{k}"], fonts["cell_lbl"], fonts["cell"]) for k in Texts.STATS]
    out += [("graph title", tx[f"graph.title_{g}"], "roboto_16", 135) for g in Texts.GRAPHS]
    out += [("graph legend", tx[f"graph.legend_{k}"], "roboto_14", 110) for k in ("range", "average")]
    out += [("weekly title", tx[f"weekly.{p}.title"], fonts["ti_title"], fonts["ti_w"]) for p in Texts.PAGES]
    out += [("weekly desc from the file (may wrap to 2 lines)", tx.file_line, fonts["ti_desc"], 2 * fonts["ti_w"])]
    out += [("weekly status", t, fonts["ti_status"], fonts["ti_w"]) for t in tx.weekly_status()]
    out += [("weekly label", tx[f"weekly.target.{k}"], "roboto_16", 170) for k in ("consensus", "custom")]
    return out


def landscape(tx: Texts) -> int:
    """Check every landscape layout; return the number of texts too large."""
    bad = 0
    for (W, H), L in LAYOUTS.items():
        LW, RW = L["LW"], W - L["LW"]
        col = LW // 2
        cell = L["cell_w"] - 4
        word = L.get("word_big", "roboto_40")
        word_small = L.get("word_small", "roboto_32")
        d_big, d_small = L.get("delta_big", "roboto_64"), L.get("delta_small", "roboto_40")
        left, middle, right = tx.status()
        checks = [
            ("glucose value", "888", L["value_font"], LW - 4),
            ("glucose value mmol/L", "27.8", L["value_font"], LW - 4),
            ("glucose value, limit reading", tx.lo, L["value_font"], LW - 4),
            ("glucose value, limit reading", tx.hi, L["value_font"], LW - 4),
            ("delta mmol/L, 4 characters", "+2.8", d_small, col - 4),
            ("TREND without a current reading", "---", L["delta_font"], col - 4),
            *[
                ("zone word under the value", z, word_font_for(z, word, word_small, L["word_w"]), L["word_w"])
                for z in tx.zone_words
            ],
            ("5 MIN DELTA label", tx["glucose.delta_label"], L["col_lbl_font"], col - 4),
            ("delta, 3 characters", "+88", d_big, col - 4),
            ("delta, 4 characters", "-100", d_small, col - 4),
            ("clock", tx.pm, L["clock_font"], W // 2 - L["edge"]),
            ("date", f"{tx['date.month.september']} 30", L["date_font"], W // 2 - L["edge"]),
            ("date", tx["date.weekday.wednesday"], L["date_font"], W // 2 - L["edge"]),
            ("status left", left[0], L["sb_font"], W // 2 - L["edge"] - width(middle[0], L["sb_font"]) / 2 - 6),
            ("status middle", middle[0], L["sb_font"], W - 2 * (L["edge"] + width(right[0], L["sb_font"]) + 6)),
            ("status right", right[0], L["sb_font"], W // 2 - L["edge"] - width(middle[0], L["sb_font"]) / 2 - 6),
            ("stat label", tx["stat.gmi_180d"], L["cell_lbl_font"], cell),
            ("stat label", tx["stat.very_high"], L["cell_lbl_font"], cell),
            ("stat label", tx["stat.very_low"], L["cell_lbl_font"], cell),
            ("stat value", "100%", L["cell_val_font"], cell),
            ("stat value", "12.9%", L["cell_val_font"], cell),
            ("stat value", "400", L["cell_val_font"], cell),
            ("stat value", "27.8", L["cell_val_font"], cell),
            ("stat value", "99,9%", L["cell_val_font"], cell),
            ("stat value", "27,8", L["cell_val_font"], cell),
            # the GMI in both A1C units takes the cell's whole width (make_glucocraft_landscape.py gmi_both_font)
            ("stat value, GMI in both A1C units", "12.9%/117", L["gmi_both_font"], L["cell_w"]),
            ("stat value, GMI in both A1C units", "12,9%/117", L["gmi_both_font"], L["cell_w"]),
            ("weekly title", tx["weekly.mean.title"], L["ti_title_font"], RW - 20),
            (
                "weekly status",
                fill(tx["weekly.status.glucose"], value="400", unit="mg/dL", direction=tx.dec),
                L["ti_status_font"],
                RW - 20,
            ),
            (
                "weekly status",
                fill(tx["weekly.status.glucose"], value="22.2", unit="mmol/L", direction=tx.dec),
                L["ti_status_font"],
                RW - 20,
            ),
            (
                "weekly status",
                fill(tx["weekly.status.min_max"], min="22.2", max="27.8", direction=tx.dec),
                L["ti_status_font"],
                RW - 20,
            ),
            (
                "weekly status",
                fill(tx["weekly.status.min_max"], min="400", max="400", direction=tx.dec),
                L["ti_status_font"],
                RW - 20,
            ),
        ]
        print(f"\n{W} x {H}")
        bad += print_checks(checks)
        # without a current reading (landscape_top_layer.yaml no_reading_box): the words at the zone word's large size
        # and width, the last reading and its time (nr_line_font, nr_time_font, nr_split), stacked between the
        # value's top (value_y) and the columns (col_y)
        fonts = (word, L["nr_line_font"], L["nr_time_font"])
        bad += no_reading(tx, fonts, L["nr_split"], L["word_w"], L["value_y"], L["col_y"])
        # the limit screen (landscape_top_layer.yaml limit_box): the zone at the zone word's large size, the sensor
        # range in nr_line_font, since when and the last measured reading in nr_time_font, wrapped at word_w, stacked
        # in the same place as the words without a current reading
        groups = [
            (t, L["nr_line_font"] if f == "line" else f) for t, f in tx.limit_screen(L["word_font"], L["nr_time_font"])
        ]
        bad += stack("limit screen", groups, L["word_w"], L["col_y"] - L["value_y"])
        # the screen without Home Assistant (landscape_top_layer.yaml no_ha_screen): the title in the zone word's large
        # size, the other lines in load_font, wrapped at W - 40, below the header, 12 px apart
        lines = tx.no_connection(L["load_font"])
        groups = [(lines[0], L["word_font"])] + [(t, L["load_font"]) for t in lines[1:]]
        bad += stack("no connection", groups, W - 40, H - L["HDR"], 12)
        # the period graphs' key and title, together (the title ends 16 px from the graph's right edge)
        bad += title_and_key(tx, L["CW"], L["CW"] - 16)
        # the trend word in the TREND column (landscape_top_layer.yaml): the column col_h high, its label at the top
        # (col_lbl_y), the source's name at the bottom (src_y), the word centered at arrow_y
        ch = L["col_h"]
        bad += trend_word(
            tx,
            L["trend_word_font"],
            L["trend_word_w"],
            ch / 2 + L["arrow_y"],
            L["col_lbl_y"] + line_height(px(L["col_lbl_font"])),
            ch + L["src_y"] - line_height(px(L["src_font"])),
        )
        # vertical: the weekly header's description (two lines at most) ends above the status line, and the status
        # line within the header (landscape_top_layer.yaml, the weekly info box ti_h high)
        desc_end = L["ti_desc_y"] + 2 * line_height(px(L["ti_desc_font"]))
        status_end = L["ti_status_y"] + line_height(px(L["ti_status_font"]))
        fits = desc_end <= L["ti_status_y"] and status_end <= L["ti_h"]
        print(
            f"  vertical: weekly description (2 lines) ends {desc_end}, status line {L['ti_status_y']}-{status_end}"
            f" in {L['ti_h']} {'OK' if fits else 'TOO HIGH'}"
        )
        bad += 0 if fits else 1
        # vertical: the glucose block's parts must not overlap
        v_ink = L["value_y"] + 0.93 * px(L["value_font"])  # digits end at the baseline
        w_ink = L["word_y"] + 0.2 * px(word)  # the word's ink starts about 0.2 em below its box top
        w_end = L["word_y"] + px(word) * 1.17
        print(
            f"  vertical: value ink ends {v_ink:.0f}, word ink starts {w_ink:.0f} (box ends {w_end:.0f}),"
            f" columns start {L['col_y']}"
            f" {'OK' if v_ink + 8 <= w_ink and w_end <= L['col_y'] + 4 else 'TIGHT'}"
        )
        check(about_text_width(L) == W - 2 * L["about_top"] - L["about_icon"] - L["about_icon_gap"], "text column")
        bad += screens(
            tx,
            W,
            H,
            False,
            L["about_top"],
            L["about_bottom"],
            L["about_icon"],
            L["about_icon_gap"],
            px(L["about_head_font"]),
            px(L["about_text_font"]),
            px(L["about_ver_font"]),
            L["about_gap"],
            int(W * int(L["splash_w"].rstrip("%")) / 100),
            px(L["splash_head_font"]),
            px(L["splash_font"]),
            px(L["splash_ver_font"]),
            L["splash_gap"],
        )
        print("  all texts:")
        bad += print_checks(
            all_texts(
                tx,
                W,
                L["edge"],
                LW,
                {
                    "sb": L["sb_font"],
                    "sb_check": L["sb_check_font"],
                    "load": L["load_font"],
                    "clock": L["clock_font"],
                    "date": L["date_font"],
                    "col_lbl": L["col_lbl_font"],
                    "col": col,
                    "cell_lbl": L["cell_lbl_font"],
                    "cell": cell,
                    "ti_title": L["ti_title_font"],
                    "ti_desc": L["ti_desc_font"],
                    "ti_status": L["ti_status_font"],
                    "ti_w": RW - 20,
                },
            )
        )
    return bad


def square(tx: Texts) -> int:
    """Check the square panel (480 x 480); return the number of texts too large.

    Its weekly pages hide the glucose block, so a small current value with its zone word sits in the top-right corner
    of trend_info_container (460 px wide, 160 px high); the title, description and status keep their fonts below it.
    """
    print("\n480 x 480 (square panel)")
    # glucocraft-panel.yaml about_screen: top 19, bottom 12, icon 96 on top, 22 / 18 / 16 px, 12 px apart
    bad = screens(tx, 480, 480, True, 19, 12, 96, 8, 22, 18, 16, 12, 432, 32, 24, 22, 24)
    bad += print_checks(
        [
            ("glucose value", "888", "roboto_120", 248),
            ("glucose value mmol/L", "27.8", "roboto_120", 248),
            ("glucose value, limit reading", tx.lo, "roboto_120", 248),
            ("TREND without a current reading", "---", "roboto_64", 111),
            ("weekly title", tx["weekly.mean.title"], "roboto_32", 460),
            *[
                ("zone word under the value", z, word_font_for(z, "roboto_40", "roboto_32", 248), 248)
                for z in tx.zone_words
            ],
            ("stat label (left / right columns)", tx["stat.very_high"], "roboto_12", 56),
            ("stat label (left / right columns)", tx["stat.very_low"], "roboto_12", 56),
            # the side columns' values at 20 px (52 px of content, clipped at 56): whole percentages up to "100%", the
            # Very low / Very high cells with one decimal ("100%" at 100.0, gc_units.h pct1_cell), with either
            # separator, and the minimum and maximum
            *[
                ("stat value (left / right columns)", v, "roboto_20", 56)
                for v in ("100%", "99.9%", "99,9%", "12.9%", "12,9%", "400", "27.8", "27,8", tx.lo, tx.hi)
            ],
        ]
    )
    # the trend word in the 115 x 160 px TREND column: 26 px wrapped at 111 px, centered at 80, between the label (16 px
    # at y -50 from the center) and the source's name (14 px at y +55)
    bad += trend_word(tx, "roboto_26", 111, 80, 80 - 50 + line_height(16) / 2, 80 + 55 - line_height(14) / 2)
    # without a current reading, the stack in the 250 x 160 px middle column (glucocraft-panel.yaml no_reading_box):
    # the words at 40 px wrapped at 248 px, the last reading at 20 px, its time at 16 px split before the bracket
    bad += no_reading(tx, ("roboto_40", "roboto_20", "roboto_16"), True, 248, 0, 160)
    # the limit screen (glucocraft-panel.yaml limit_box) in the same place: the zone at 40 px, the sensor range at
    # 20 px, since when and the last measured reading at 16 px, wrapped at 248 px within 160 px
    groups = [(t, "roboto_20" if f == "line" else f) for t, f in tx.limit_screen("roboto_40", "roboto_16")]
    bad += stack("limit screen", groups, 248, 160)
    # the screen without Home Assistant (glucocraft-panel.yaml no_ha_screen): the title at 32 px, the other lines at
    # 24 px, wrapped at 440 px, 12 px apart, within the 384 px below the header
    lines = tx.no_connection("roboto_24")
    bad += stack("no connection", [(lines[0], "roboto_32")] + [(t, "roboto_24") for t in lines[1:]], 440, 384, 12)
    # the period graphs' key and title together (the title ends at x 330)
    bad += title_and_key(tx, 340, 330)
    values, times = ["888", "27.8", tx.lo, tx.hi, "---"], [tx.pm, "23:59"]
    # the row: the value (24 px), its zone word (18 px), the arrow (24 px) and the time (18 px), 6 px apart
    worst = (
        max(width(t, "roboto_24") for t in values)
        + 6
        + max(width(z, "roboto_18") for z in tx.zone_words)
        + 6
        + 24
        + 6
        + max(width(t, "roboto_18") for t in times)
    )
    # (without a current reading the corner shows "---" in gray with the reading's time, never a number)
    for what, w in (("weekly mini value: value + zone word + arrow + time", worst),):
        ok = w <= 460
        bad += not ok
        print(f"  {'OK ' if ok else 'TOO'} {w:5.0f} /   460  {what}")
    # vertical: the mini row (y 3, 29 px box: the 24 px value and the 24 px arrow), title (y 34, 32 px), description
    # (y 76, two lines at 18 px), status (y 124, 24 px) inside the 160 px container
    lh = line_height
    rows = [
        ("mini row", 3, max(lh(24), 24)),
        ("title", 34, lh(32)),
        ("description (2 lines)", 76, 2 * lh(18)),
        ("status", 124, lh(24)),
    ]
    end = 0
    for what, y, h in rows:
        ok = y >= end and y + h <= 160
        bad += not ok
        print(f"  {'OK ' if ok else 'TOO'} {what}: y {y}-{y + h} (previous ends {end})")
        end = y + h
    # the square panel's other texts: the status bar (16 px, 10 px from the edges), the loading screen,
    # the 115 px TREND / 5 MIN DELTA columns, the statistics labels in the 56 px columns, the 460 px weekly page
    print("  all texts:")
    bad += print_checks(
        [("5 MIN DELTA label", tx["glucose.delta_label"], "roboto_16", 111)]
        + all_texts(
            tx,
            480,
            10,
            480,
            {
                "sb": "roboto_16",
                "sb_check": "roboto_14",
                "load": "roboto_26",
                "clock": "roboto_60",
                "date": "roboto_26",
                "header_side_by_side": True,
                "header_edge": 15,
                "col_lbl": "roboto_16",
                "col": 115,
                "cell_lbl": "roboto_12",
                "cell": 56,
                "ti_title": "roboto_32",
                "ti_desc": "roboto_18",
                "ti_status": "roboto_24",
                "ti_w": 460,
            },
        )
    )
    return bad


def main(argv: list[str] | None = None) -> int:
    """Run every check for a language and print the result; return the number of texts too large."""
    args = sys.argv[1:] if argv is None else argv
    lang = args[0] if args else mpt.SOURCE
    check((mpt.CATALOGUES / f"{lang}.yaml").exists(), f"i18n/{lang}.yaml does not exist")
    tx = Texts(lang)
    print(f"language: {lang}")
    bad = landscape(tx) + square(tx)
    print("\nall fit" if not bad else f"\n{bad} too wide")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
