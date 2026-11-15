# License: AGPL-3.0-only (LICENSE).
"""The regional settings: time format, date format, first day of the week, decimal separator, A1C / GMI unit and paper
size; every value, "auto" for ten countries, and pairwise combinations with the display unit.

The outputs checked: the Configuration attributes (the resolved value the cards and panels read and the stored choice
as *_setting), the summaries (times, dates, decimals, GMI), the CGM Report data
(the values the report page and the PDF format with, the calendars' lead days, the creation stamp), the PDF (paper
size, decimals, the A1C unit; marked slow), the Repairs clock notice, and the CSV export and the HTTP API (always the
decimal point: data, not text).
"""

from __future__ import annotations

import io
import re
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from glucocraft import coordinator as co
from glucocraft.regional import Regional

from test_settings_harness import (
    assert_pairs_covered,
    build,
    entry,
    fixed_clock,  # noqa: F401 (a fixture)
    pairwise,
    series,
    tz_patch,  # noqa: F401 (a fixture)
)

pytestmark = pytest.mark.usefixtures("tz_patch", "fixed_clock")

LA = ZoneInfo("America/Los_Angeles")
REGIONAL = ("time_format", "date_format", "first_weekday", "decimal_separator", "a1c_unit", "paper_size")


def clock(ts: float, fmt: str) -> str:
    """A time of day written out by hand (independent of regional.py): "7:46 AM" / "07:46"."""
    d = datetime.fromtimestamp(ts, LA)
    if fmt == "24":
        return f"{d.hour:02d}:{d.minute:02d}"
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def numeric_date(ts: float, order: str, year: bool = False) -> str:
    d = datetime.fromtimestamp(ts, LA)
    if order == "DMY":
        return f"{d.day:02d}/{d.month:02d}/{d.year}" if year else f"{d.day}/{d.month}"
    if order == "YMD":
        return f"{d.year}-{d.month:02d}-{d.day:02d}" if year else f"{d.month:02d}-{d.day:02d}"
    return f"{d.month:02d}/{d.day:02d}/{d.year}" if year else f"{d.month}/{d.day}"


def numbers_in(text: str) -> list[str]:
    """Every decimal number of a text ("7.9", "7,9")."""
    return re.findall(r"\d+[.,]\d+", text)


# ------------------------------------------------------------------------------------------------ one setting at a time
@pytest.mark.parametrize("setting,expected", [("auto", "12"), ("12", "12"), ("24", "24")])
def test_time_format(tmp_path, setting, expected):
    now = int(time.time())
    run = build(tmp_path, entry(time_format=setting), now=now)
    cfg = run.config()
    assert (cfg["time_format"], cfg["time_format_setting"]) == (expected, setting)
    # the summary's last reading, the report's creation stamp
    last = run.c.last_reading_ts
    assert f"at {clock(last, expected)} (" in run.daily()
    rep = run.report(14)
    assert rep["time_format"] == expected
    created = rep["created"]
    assert (re.search(r"\d (AM|PM)$", created) is not None) is (expected == "12"), created
    assert ("AM" not in created and "PM" not in created) is (expected == "24")
    # data stay data: the API and the CSV carry epoch seconds and ISO times, whatever the format
    assert isinstance(run.api_data("6h")["readings"][-1]["t"], int)
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d,", run.csv().splitlines()[1])


@pytest.mark.parametrize(
    "setting,code,chosen",
    [
        ("auto", "MDY", "auto"),
        ("mdy", "MDY", "MDY"),
        ("dmy", "DMY", "DMY"),
        ("ymd", "YMD", "YMD"),
        ("DMY", "MDY", "auto"),  # not one of the stored choices (lower case): the default, as for any other setting
    ],
)
def test_date_format(tmp_path, setting, code, chosen):
    """The stored choice decides the date format; anything else is the default (auto: from the country)."""
    now = int(time.time())
    # the newest reading from yesterday: the summary shows its date
    rows = series(now, 2, age=26 * 3600)
    run = build(tmp_path, entry(date_format=setting), readings=rows, now=now)
    cfg = run.config()
    assert cfg["date_format"] == code
    assert cfg["date_format_setting"] == chosen  # the choice as the code
    last = run.c.last_reading_ts
    assert f"No current data since {datetime.fromtimestamp(last, LA).strftime('%a')} {numeric_date(last, code)} " in (
        run.daily()
    )
    rep = run.report(14)
    assert rep["date_format"] == code
    assert (
        numeric_date(time.time(), code, year=True) in rep["created"]
        or numeric_date(time.time() - 60, code, year=True) in rep["created"]
    )


@pytest.mark.parametrize("setting,code", [("auto", "MDY"), ("mdy", "MDY"), ("dmy", "DMY"), ("ymd", "YMD")])
@pytest.mark.parametrize("time_format", ["12", "24"])
def test_the_clock_notice_uses_the_date_and_time_format(tmp_path, monkeypatch, setting, code, time_format):
    """The Repairs notice for readings later than the clock names both times with the year."""
    issues = []
    monkeypatch.setattr(co.ir, "async_create_issue", lambda hass, dom, iid, **kw: issues.append(kw))
    run = build(tmp_path, entry(date_format=setting, time_format=time_format))
    newest = run.c.storage.newest_timestamp
    run.c._check_clock(newest - 3600)
    ph = issues[0]["translation_placeholders"]
    assert numeric_date(newest, code, year=True) in ph["newest"]
    assert ph["newest"].endswith(clock(newest, time_format))
    assert ph["now"].endswith(clock(newest - 3600, time_format))


@pytest.mark.parametrize("setting,first", [("auto", 6), ("monday", 0), ("sunday", 6), ("saturday", 5)])
def test_first_weekday(tmp_path, setting, first):
    run = build(tmp_path, entry(first_weekday=setting), days=40)
    cfg = run.config()
    assert (cfg["first_weekday"], cfg["first_weekday_setting"]) == (first, setting)
    rep = run.report(30, full=True)
    assert rep["first_weekday"] == first
    # the page and the PDF leave the empty cells before the 1st and before the first day from these weekdays
    regional = Regional(first_weekday=first)
    for m in rep["monthly"]:
        assert regional.lead_days(m["first_weekday"]) == (m["first_weekday"] - first) % 7
    st = rep["standard"]
    assert regional.lead_days(st["daily_weekdays"][0]) == (st["daily_weekdays"][0] - first) % 7


@pytest.mark.parametrize("setting,sep", [("auto", "."), ("point", "."), ("comma", ",")])
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_decimal_separator(tmp_path, setting, sep, unit):
    now = int(time.time())
    run = build(tmp_path, entry(decimal_separator=setting, unit=unit), now=now)
    cfg = run.config()
    assert (cfg["decimal_separator"], cfg["decimal_separator_setting"]) == (sep, setting)
    other = "," if sep == "." else "."
    for text in (run.daily(), run.weekly()):
        body = text.split("*Targets and ranges")[0]
        nums = numbers_in(body)
        assert nums and all(other not in n for n in nums), [n for n in nums if other in n][:5]
    if unit == "mmol/L":
        assert f"3{sep}9 to 10{sep}0" in run.weekly()  # the consensus range in the display unit
    # data stay data: the configuration's numbers, the API and the CSV always use the point
    assert isinstance(cfg["ranges"]["low"], (int, float))
    assert "," not in run.csv().splitlines()[1].split(",", 2)[2].split(",")[0]
    assert isinstance(run.api_data("24h")["stats"]["mean"], float)
    assert run.report(14)["decimal_separator"] == sep


GMI = {
    "percent": re.compile(r"GMI\)? \d+[.,]\d% \(Bergenstal"),
    "mmol_mol": re.compile(r"GMI\)? \d+ mmol/mol \(Bergenstal"),
    "both": re.compile(r"GMI\)? \d+[.,]\d% \(\d+ mmol/mol\) \(Bergenstal"),
}


@pytest.mark.parametrize(
    "setting,resolved", [("auto", "percent"), ("percent", "percent"), ("mmol_mol", "mmol_mol"), ("both", "both")]
)
def test_a1c_unit(tmp_path, setting, resolved):
    run = build(tmp_path, entry(a1c_unit=setting))
    cfg = run.config()
    assert (cfg["a1c_unit"], cfg["a1c_unit_setting"]) == (resolved, setting)
    weekly = run.weekly()
    assert GMI[resolved].search(weekly), weekly[weekly.find("GMI") - 20 : weekly.find("GMI") + 60]
    for other, rx in GMI.items():
        if other != resolved and not (resolved == "both" and other == "percent"):
            assert not rx.search(weekly), other
    rep = run.report(14)
    assert rep["a1c_unit"] == resolved
    assert rep["standard"]["gmi_mmol_mol"] is not None  # the report always carries both; the page picks


@pytest.mark.parametrize("setting,resolved", [("auto", "letter"), ("letter", "letter"), ("a4", "a4")])
def test_paper_size(tmp_path, setting, resolved):
    run = build(tmp_path, entry(paper_size=setting))
    cfg = run.config()
    assert (cfg["paper_size"], cfg["paper_size_setting"]) == (resolved, setting)
    assert run.report(14)["paper_size"] == resolved


@pytest.mark.slow
@pytest.mark.parametrize("setting,size", [("auto", (612.0, 792.0)), ("letter", (612.0, 792.0)), ("a4", (595.3, 841.9))])
def test_paper_size_in_the_pdf(tmp_path, setting, size):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    (pdf, _pages), _ = build(tmp_path, entry(paper_size=setting)).pdf(14, False)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    for page in reader.pages:
        box = page.mediabox
        assert (round(float(box.width), 1), round(float(box.height), 1)) == size


# ------------------------------------------------------------------------------------------------ "auto" by country
# The resolved values written out by hand from GlucoCraft's documented rules (regional.py: CLDR timeData, the short
# date patterns, weekData firstDay, the decimal symbol, measurementData paperSize; the A1C rule of GlucoCraft)
COUNTRIES = {
    # country, language: time, date, first weekday, decimal, A1C unit, paper
    ("US", "en"): ("12", "MDY", 6, ".", "percent", "letter"),
    ("CA", "en"): ("12", "YMD", 6, ".", "percent", "letter"),
    ("CA", "fr"): ("12", "YMD", 6, ",", "percent", "letter"),
    ("GB", "en"): ("24", "DMY", 0, ".", "mmol_mol", "a4"),
    ("DE", "de"): ("24", "DMY", 0, ",", "both", "a4"),
    ("FR", "fr"): ("24", "DMY", 0, ",", "both", "a4"),
    ("MX", "es"): ("24", "DMY", 6, ".", "percent", "letter"),
    ("PH", "en"): ("12", "MDY", 6, ".", "percent", "letter"),
    ("JP", "ja"): ("24", "YMD", 6, ".", "percent", "a4"),
    ("SE", "sv"): ("24", "YMD", 0, ",", "mmol_mol", "a4"),
    ("BR", "pt"): ("24", "DMY", 6, ",", "percent", "a4"),
    (None, "de"): ("12", "MDY", 6, ",", "percent", "letter"),  # no country: the language decides the separator
    (None, None): ("12", "MDY", 6, ".", "percent", "letter"),
}


@pytest.mark.parametrize("country,language", list(COUNTRIES))
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_auto_resolves_for_the_country(tmp_path, country, language, unit):
    now = int(time.time())
    run = build(tmp_path, entry(unit=unit), country=country, language=language, now=now)
    t, d, w, dec, a1c, paper = COUNTRIES[(country, language)]
    if country is None and unit == "mmol/L":
        a1c = "both"  # without a country, from the glucose unit
    cfg = run.config()
    assert {k: cfg[k] for k in REGIONAL} == dict(zip(REGIONAL, (t, d, w, dec, a1c, paper), strict=True))
    assert all(cfg[f"{k}_setting"] == "auto" for k in REGIONAL)
    rep = run.report(14)
    assert {k: rep[k] for k in REGIONAL} == {k: cfg[k] for k in REGIONAL}
    # the texts follow the resolved values
    assert f"at {clock(run.c.last_reading_ts, t)} (" in run.daily()
    weekly = run.weekly()
    assert GMI[a1c].search(weekly)
    other = "," if dec == "." else "."
    assert all(other not in n for n in numbers_in(weekly.split("*Targets and ranges")[0]))


# ------------------------------------------------------------------------------------------------ pairwise
PAIRWISE = {
    "unit": ["mg/dL", "mmol/L"],
    "decimal_separator": ["auto", "point", "comma"],
    "a1c_unit": ["auto", "percent", "mmol_mol", "both"],
    "date_format": ["auto", "mdy", "dmy", "ymd"],
    "time_format": ["auto", "12", "24"],
    "first_weekday": ["auto", "monday", "sunday", "saturday"],
}
ROWS = pairwise(PAIRWISE)


def test_the_pairwise_rows_cover_every_pair():
    assert_pairs_covered(PAIRWISE, ROWS)


def _expected(row: dict, country="DE") -> dict:
    """The resolved values of a row for Home Assistant in Germany (German): "auto" = the German conventions."""
    return {
        "time_format": {"auto": "24"}.get(row["time_format"], row["time_format"]),
        "date_format": {"auto": "DMY"}.get(row["date_format"], row["date_format"].upper()),
        "first_weekday": {"auto": 0, "monday": 0, "sunday": 6, "saturday": 5}[row["first_weekday"]],
        "decimal_separator": {"auto": ",", "point": ".", "comma": ","}[row["decimal_separator"]],
        "a1c_unit": {"auto": "both"}.get(row["a1c_unit"], row["a1c_unit"]),
    }


@pytest.mark.parametrize("row", ROWS, ids=lambda r: "-".join(str(v) for v in r.values()))
def test_pairwise_unit_decimal_a1c_date_time_weekday(tmp_path, row):
    now = int(time.time())
    data = entry(**row)
    run = build(tmp_path, data, country="DE", language="de", days=40, now=now)
    exp = _expected(row)
    cfg = run.config()
    assert {k: cfg[k] for k in exp} == exp and cfg["unit"] == row["unit"]
    # the summaries: the separator, the unit in words, the GMI in the A1C unit
    weekly = run.weekly()
    words = "millimoles per liter" if row["unit"] == "mmol/L" else "milligrams per deciliter"
    assert f"(glucose in {words})" in weekly
    other = "," if exp["decimal_separator"] == "." else "."
    assert all(other not in n for n in numbers_in(weekly.split("*Targets and ranges")[0]))
    assert GMI[exp["a1c_unit"]].search(weekly)
    # the daily summary's last reading: the time format and the unit in words
    daily = run.daily()
    assert f"at {clock(run.c.last_reading_ts, exp['time_format'])} (" in daily and words in daily
    # the report data: every value the page and the PDF format with, the calendars from the first day
    rep = run.report(30, full=True)
    assert {k: rep[k] for k in exp} == exp and rep["unit"] == row["unit"]
    regional = Regional(first_weekday=exp["first_weekday"])
    leads = [regional.lead_days(m["first_weekday"]) for m in rep["monthly"]]
    assert leads == [(m["first_weekday"] - exp["first_weekday"]) % 7 for m in rep["monthly"]]
    # data stay data
    assert run.csv().splitlines()[0] == "local_time,utc_time,glucose_mg_dl,glucose_display,display_unit,source,limit"
    assert run.csv().splitlines()[-1].split(",")[4] == row["unit"]


@pytest.mark.slow
@pytest.mark.parametrize(
    "row",
    [r for r in ROWS if r["unit"] == "mmol/L"][:4] + [r for r in ROWS if r["unit"] == "mg/dL"][:4],
    ids=lambda r: "-".join(str(v) for v in r.values()),
)
def test_pairwise_in_the_pdf(tmp_path, row):
    """The PDF of a pairwise row: only the decimal separator in use appears in its numbers; mmol/mol only where the
    A1C unit shows it; the creation stamp in the date order."""
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    run = build(tmp_path, entry(**row), country="DE", language="de", days=40)
    (pdf, _pages), data = run.pdf(14, False)
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    exp = _expected(row)
    text = text.replace("AGPL-3.0", "")
    # not decimals: addresses, table and recommendation numbers of the cited sources, document versions
    text = re.sub(
        r"https?://\S+|(?:Table|Recommendation)\s*\d+\.\d+|\bv\d+\.\d+|\bversion\s\d+\.\d+|\d+\.\d+\.\d+", "", text
    )
    if exp["decimal_separator"] == ",":
        assert not re.search(r"\d\.\d", text), re.findall(r".{15}\d\.\d.{10}", text)[:3]
    else:
        assert not re.search(r"\d,\d", text), re.findall(r".{15}\d,\d.{10}", text)[:3]
    assert ("mmol/mol" in text) is (exp["a1c_unit"] in ("mmol_mol", "both"))
    assert data["unit"] == row["unit"] and row["unit"] in text
    assert data["created"] in text


def test_regional_settings_never_change_the_data(tmp_path):
    """Every regional choice at once: the statistics, the panel feed's numbers and the CSV are those of the defaults."""
    now = int(time.time())
    base = build(tmp_path / "a", now=now)
    other = build(
        tmp_path / "b",
        entry(
            time_format="24",
            date_format="ymd",
            first_weekday="saturday",
            decimal_separator="comma",
            a1c_unit="mmol_mol",
            paper_size="a4",
        ),
        now=now,
    )
    assert base.c.period_stats == other.c.period_stats
    assert base.feed_bytes() == other.feed_bytes()
    assert base.csv() == other.csv()
    assert base.api_stats() == other.api_stats()


def test_utc_and_local_times_in_the_csv(tmp_path):
    run = build(tmp_path, entry(time_format="24", date_format="dmy"))
    r = run.c.storage.readings[-1]
    local, utc = run.csv().splitlines()[-1].split(",")[:2]
    assert utc == datetime.fromtimestamp(r.timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert local == datetime.fromtimestamp(r.timestamp, LA).isoformat()


@pytest.mark.parametrize("stored", ["auto", "", None, 24, "13"])
def test_an_unknown_time_format_falls_back_to_12_hours(stored):
    """Every regional value is normalised, the time format too: an unknown value is the default ("12"), so the
    configuration attribute time_format never carries a value the cards do not know."""
    rg = Regional(time_format=stored)
    assert rg.time == "12" and rg.as_dict()["time_format"] == "12"
    assert Regional.from_dict({"time_format": stored}).time == "12"
    assert Regional(time_format="24").time == "24"
