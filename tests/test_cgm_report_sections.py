# License: AGPL-3.0-only (LICENSE).
"""The CGM Report's sections: the high-glucose section, the data quality page, the 26-week weekly trend, the two
optional appendices (custom ranges and goals; measures defined for research), the cited context in the PDF and on the
report page, the sensor-active label, and the report options of the service and the page."""

from __future__ import annotations

import asyncio
import io
import json
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from glucocraft import const as C
from glucocraft import i18n
from glucocraft import report as R
from glucocraft import report_service as RS
from glucocraft.regional import Regional

from conftest import LA, Rd
from js_i18n import catalogue

REPO = Path(__file__).resolve().parents[1]
JS = (REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-report.js").read_text(encoding="utf-8")

NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = NOW_LOCAL.timestamp()
END = NOW_LOCAL.replace(hour=0)  # the report's last midnight


def flat(start: int, end: int, value: float = 120.0, step: int = 300) -> list[Rd]:
    return [Rd(t, value) for t in range(start, end, step)]


def ts(day_offset: int, hour: float) -> int:
    """Epoch s at local hour `hour` of the day END - day_offset days."""
    return int((END - timedelta(days=day_offset)).timestamp() + hour * 3600)


# ------------------------------------------------------------------------------------------------ high glucose
def test_time_above_by_night_and_day():
    start = ts(14, 0)
    rd = [
        Rd(t, 200.0 if (t - start) % 86400 < 3 * 3600 else 260.0 if (t - start) % 86400 < 9 * 3600 else 120.0)
        for t in range(start, int(END.timestamp()), 300)
    ]
    out = R.build_report(rd, 300, LA, 14, False, [], now=NOW)
    hi = out["highs"]
    # night 00:00-05:59: 3 h at 200 and 3 h at 260 -> 100 % above 180, 50 % above 250; day: 3 h at 260 of 18 h
    assert hi["night_above_180"] == 100.0 and hi["night_above_250"] == 50.0
    assert hi["day_above_180"] == pytest.approx(16.7) and hi["day_above_250"] == pytest.approx(16.7)
    assert "events" not in hi  # the main report lists no events


# ------------------------------------------------------------------------------------------------ data quality
def test_data_quality_gaps_days_limits_and_events():
    rd = flat(ts(20, 0), int(NOW))
    gap_from, gap_to = ts(5, 10), ts(5, 13)  # a 3-hour gap on one day of the period
    rd = [r for r in rd if not gap_from < r.timestamp < gap_to]
    rd[next(i for i, r in enumerate(rd) if r.timestamp >= ts(3, 2))] = Rd(ts(3, 2), 40.0, "low")
    events = [{"start": ts(4, 8), "end": ts(4, 9)}, {"start": ts(30, 8), "end": None}]  # the second: before the period
    out = R.build_report(rd, 300, LA, 14, False, [], now=NOW, sensor_events=events, regional=Regional("24", "DMY"))
    q = out["quality"]
    assert q["gaps"] == 1 and q["longest_gap_minutes"] == 180
    assert q["longest_gap_to"] == "13:00"  # ends on the day it began: the clock time only
    assert q["no_data_minutes"] == 175  # the gap minus the earlier reading's own 5 minutes
    assert len(q["per_day"]) == 14 and [d["active"] for d in q["per_day"]].count(100.0) == 13
    gap_day = next(d for d in q["per_day"] if d["date"] == (END - timedelta(days=5)).strftime("%Y-%m-%d"))
    assert gap_day["active"] == 87.8  # 1265 of 1440 minutes covered: 87.847%, shown rounded once
    assert q["limit_readings"] == {"low": 1, "high": 0}
    assert [e["minutes"] for e in q["sensor_events"]] == [60]


# ------------------------------------------------------------------------------------------------ the weekly trend
def test_weekly_trend_for_90_days_and_longer_only_with_the_week_rule():
    rd = flat(ts(199, 0), int(NOW))
    thin_from, thin_to = ts(28, 0), ts(21, 0)  # the 4th week before the end: 3 hours of data only
    rd = [r for r in rd if not thin_from <= r.timestamp < thin_to or r.timestamp < thin_from + 3 * 3600]
    assert "weekly" not in R.build_report(rd, 300, LA, 30, False, [], now=NOW)
    for days in (90, 180):
        wk = R.build_report(rd, 300, LA, days, False, [], now=NOW)["weekly"]
        assert len(wk) == R.TREND_WEEKS == 26
        assert wk[-1]["week_end"] == (END - timedelta(days=1)).strftime("%Y-%m-%d")  # oldest first
        assert wk[-4]["tir"] is None and wk[-4]["mean"] is None  # 3 hours of the week: less than 70% sensor active
        assert wk[-1]["tir"] == 100.0 and wk[-1]["mean"] == 120 and wk[-1]["cv"] == 0.0


# ------------------------------------------------------------------------------------------------ custom appendix
CUSTOM = {
    "ranges": {"very_low": 54, "low": 80, "tight_low": 70, "tight_high": 140, "high": 180, "very_high": 250},
    "goals": {
        "time_in_range": {"op": ">", "value": 70},
        "time_low": {"op": "<", "value": 4},
        "cv": {"op": "<=", "value": 36},
    },
}


def test_custom_appendix_uses_the_settings_and_the_14_day_70_percent_rule():
    rd = [Rd(t, 75.0 if (t // 300) % 10 == 0 else 120.0) for t in range(ts(40, 0), int(NOW), 300)]
    out = R.build_report(rd, 300, LA, 14, False, [], now=NOW)
    assert "custom_appendix" not in out and out["include_custom_appendix"] is False
    ca = R.build_report(rd, 300, LA, 14, False, [], now=NOW, custom=CUSTOM)["custom_appendix"]
    rows = {r["key"]: r for r in ca["rows"]}
    assert rows["time_low"]["value"] == 10.0  # 75 counts as low with the limit 80: one reading in ten
    assert rows["time_low"]["met"] is False and rows["time_in_range"]["met"] is True and rows["cv"]["met"] is True
    assert rows["gmi"]["goal"] is None and rows["gmi"]["met"] is None  # no goal set
    assert ca["comparable"] and ca["ranges_differ"] and sum(r["goal"] is not None for r in ca["rows"]) == 3
    # less than 70 % data: nothing compared
    sparse = [r for r in rd if (r.timestamp // 3600) % 2 == 0]
    ca2 = R.build_report(sparse, 300, LA, 14, False, [], now=NOW, custom=CUSTOM)["custom_appendix"]
    assert not ca2["comparable"] and all(r["met"] is None for r in ca2["rows"])


# ------------------------------------------------------------------------------------------------ research appendix
def _events(rd, days=14):
    s, e = int((END - timedelta(days=days)).timestamp()), int(END.timestamp())
    return R.extended_high_events(rd, 300, s, e, LA)


def test_extended_high_event_start_end_and_duration():
    base = ts(3, 8)
    rd = flat(ts(20, 0), base)
    rd += [Rd(base + k * 300, 300.0) for k in range(24)]  # 24 x 5 min = 120 min above 250
    t = base + 24 * 300
    rd += [Rd(t + k * 300, 200.0) for k in range(6)]  # 181-250: does not end it
    t += 6 * 300
    rd += [Rd(t + k * 300, 170.0) for k in range(2)] + [Rd(t + 600, 260.0)]  # 10 min at or below 180: not yet
    t += 900
    rd += [Rd(t + k * 300, 150.0) for k in range(10)]  # 15 minutes at or below 180: the end
    end_reading = t
    rd += flat(t + 3000, int(NOW))
    ev = _events(rd)
    assert ev["events"] == 1 and ev["longest_minutes"] == ev["total_minutes"] == (end_reading - base) // 60
    assert "list" not in ev  # the appendix shows the count, the total and the longest event, no list


def test_extended_high_needs_120_minutes_and_a_gap_ends_it():
    base = ts(4, 8)
    short = flat(ts(20, 0), base) + [Rd(base + k * 300, 300.0) for k in range(23)] + flat(base + 23 * 300, int(NOW))
    assert _events(short)["events"] == 0  # 115 minutes: no event
    # a run interrupted by a gap before 120 minutes: no event; after 120 minutes: ends at the end of its last reading
    cut = (
        flat(ts(20, 0), base)
        + [Rd(base + k * 300, 300.0) for k in range(12)]
        + [Rd(base + 12 * 300 + 3600 + k * 300, 300.0) for k in range(20)]
        + flat(base + 3 * 3600 + 7200, int(NOW))
    )
    assert _events(cut)["events"] == 0
    long = (
        flat(ts(20, 0), base) + [Rd(base + k * 300, 300.0) for k in range(30)] + flat(base + 30 * 300 + 3600, int(NOW))
    )
    ev = _events(long)
    assert ev["events"] == 1 and ev["longest_minutes"] == 30 * 5  # to the end of the last reading before the gap


def test_extended_high_counted_in_the_period_where_it_starts():
    base = int(END.timestamp()) - 3600  # starts 23:00 on the last day
    rd = flat(ts(20, 0), base) + [Rd(base + k * 300, 300.0) for k in range(40)] + flat(base + 40 * 300, int(NOW))
    ev = _events(rd)
    assert ev["events"] == 1 and ev["longest_minutes"] == 40 * 5  # continues past midnight, counted fully


# ------------------------------------------------------------------------------------------------ the PDF
reportlab = pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")
from glucocraft import report_pdf as P


def _pdf(days=30, custom=None, research=False, unit="mg/dL", rg=None):
    rg = rg or Regional()
    rd = [Rd(t, 300.0 if (t // 3600) % 24 in (14, 15, 16) else 120.0) for t in range(ts(199, 0), int(NOW), 300)]
    d = R.build_report(
        rd,
        300,
        LA,
        days,
        False,
        [],
        now=NOW,
        regional=rg,
        custom=custom,
        research=research,
        sensor_events=[{"start": ts(3, 8), "end": ts(3, 9)}],
    )
    d.update(
        {
            "label": f"{days} Days",
            "sources": ["Test"],
            "created": "x",
            "unit": unit,
            **rg.as_dict(),
            "integration_version": "0",
        }
    )
    pdf, _pages = P.render(d, True, False)
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    return text.replace(" ", " "), d


def _norm(s: str) -> str:
    return " ".join(s.replace(" ", " ").split())


@pytest.mark.slow
def test_pdf_new_sections_cited_context_and_label():
    text, _d = _pdf(90)
    for heading in ("Hyperglycemia — 90 Days", "Data Quality — 90 Days", "Weekly Trend — 26 Weeks"):
        assert heading in text
    for name in (
        "tar",
        "sufficiency",
        "cv",
        "names",
        "event",
        "both",
        "patterns",
        "forms",
        "profile",
        "lab_difference",
        "lab_ada",
    ):
        part = _norm(P.ctx(name).replace("✓ / ✗ ", "")).split("✓")[-1][:120].strip()
        assert part in text, part
    note = C.sensor_active_note()
    assert note in text and text.count(note) == 1  # once per surface
    assert text.lower().count("close to, but not the same as") == 1  # About This Report does not repeat it
    # the Data Quality Notes point to About This Report for the gap rule
    assert "Data gaps and sensor active are as defined in About This Report, Data." in text
    assert f"{C.sensor_active_label()}:" in text
    assert "Night (12:00 AM–5:59 AM)" in text and "Day (6:00 AM–11:59 PM)" in text
    assert "Appendix" not in text  # off by default
    # the references: the ADA sections with the DOI as https://doi.org/... only; Johnson et al. is not cited
    assert "https://doi.org/10.2337/dc26-S006" in text and "https://doi.org/10.2337/dc26-S007" in text
    assert "Johnson" not in text and "pubmed" not in text.lower()


@pytest.mark.slow
def test_pdf_appendices_only_when_chosen():
    text, _ = _pdf(14, custom=CUSTOM, research=True)
    assert "Appendix: Custom Ranges and Goals — 14 Days" in text
    assert "Appendix: Measures Defined for Research — 14 Days" in text
    keys = ["intro", "definition", "no_standard", "limits"]
    keys += [f"method.{i}" for i in range(1, P.RESEARCH_METHOD_STEPS + 1)]
    for key in keys:
        s = i18n.t(f"report.research.{key}", ada_a=P.ADA_A)
        assert _norm(s)[:100] in text, s[:40]
    assert "Number of events" in text and "Longest event" in text
    assert text.index("About This Report") < text.index("Appendix: Custom") < text.index("References The works")
    assert "Ranges in the settings: very low below 54; low below 80" in text


@pytest.mark.slow
def test_pdf_mmol_puts_the_display_unit_first_and_keeps_table_numbers():
    rg = Regional("24", "DMY", 0, ",", "percent", "a4")
    text, _ = _pdf(14, research=True, unit="mmol/L", rg=rg)
    assert "above 10,0 mmol/L (180 mg/dL)" in text and "Table 6.2" in text and "Table 6,2" not in text
    assert "Night (00:00–05:59)" in text


def test_dx_and_both_helpers():
    P._RG["rg"], P._U["mmol"] = Regional(decimal_separator=","), True
    try:
        assert (
            P._ctx(f"above 180 mg/dL (10.0 mmol/L) (ADA, Table{P.NB}6.2; Recommendation{P.NB}7.9)")
            == f"above 10,0 mmol/L (180 mg/dL) (ADA, Table{P.NB}6.2; Recommendation{P.NB}7.9)"
        )
        assert P._both("181–250 mg/dL (10.1–13.9 mmol/L)") == "10.1–13.9 mmol/L (181–250 mg/dL)"
    finally:
        P._RG["rg"], P._U["mmol"] = Regional(), False
    assert P._dur(45) == "45 min" and P._dur(125) == "2 h 5 min" and P._dur(120) == "2 h" and P._dur(1500) == "1 d 1 h"


# ------------------------------------------------------------------------------------------------ service and page
def test_report_options_parse_the_shared_names():
    assert RS.report_options({}) == (False, False)
    assert RS.report_options({"include_custom_appendix": "1", "include_research_appendix": True}) == (True, True)
    assert RS.report_options({"include_custom_appendix": "0", "include_research_appendix": "false"}) == (False, False)


def test_warm_ups_are_recorded_with_their_end():
    saved = []
    hass = types.SimpleNamespace(async_create_task=lambda coro: saved.append(asyncio.run(coro)))
    coord = types.SimpleNamespace(warmup_started_at=0, last_reading_ts=0, async_add_listener=lambda f: None)
    reports = RS.GlucoCraftReports(hass, coord)
    now = int(NOW)
    coord.warmup_started_at = now - 3600
    reports.track_warmup()
    reports.track_warmup()  # the same warm-up: recorded once
    coord.warmup_started_at, coord.last_reading_ts = 0, now
    reports.track_warmup()
    assert reports._events == [{"start": now - 3600, "end": now}] and len(saved) == 2


def test_report_page_offers_the_options_and_mirrors_the_texts():
    # the page's texts: its own (report_page.*) and those it shares with the PDF (report.json)
    page = {**catalogue("frontend"), **catalogue("report")}
    for key, label, text in (
        ("include_custom_appendix", "report_page.options.custom.label", "Include the custom ranges and goals appendix"),
        ("include_research_appendix", "report_page.options.research.label", "Include the research measures appendix"),
    ):
        assert f'["{key}", "{label}"' in JS and page[label] == text
        assert f"{key}: false" in JS  # off by default
    # the same words as in the PDF: the page shows the PDF's texts (report.json keys), never a copy of its own
    for key in (
        "report.sensor_active.label",
        "report.sensor_active.note",
        "report.quality.gap_rule",
        "report.research.intro",
        "report.research.limits",
        "report.context.both",
        "report.context.event",
        "report.context.patterns",
        "report.context.profile",
    ):
        assert f'"{key}"' in JS and key in page, key
    assert "Johnson et al., 2019" not in JS and "Johnson et al., 2019" not in json.dumps(page)


def test_report_cache_lives_until_a_reading_it_depends_on_changes(monkeypatch):
    """The report is built from a copy of the readings (the lock only for the copy) and cached until the readings up
    to reads_until change: a new reading after it reuses the report; a late reading before the last midnight, the
    laboratory results or the local date rebuild it. The creation time is current on every call."""
    import time as _time

    from homeassistant.util import dt as dt_util

    import ha_stub

    # the clock at noon in Los Angeles, so that the report has readings after its last midnight
    ha_stub.set_clock(datetime(2026, 10, 21, 12, 0, tzinfo=LA).timestamp())
    monkeypatch.setattr(dt_util, "get_time_zone", lambda name: LA, raising=False)
    monkeypatch.setattr(dt_util, "now", lambda tz=None: datetime.fromtimestamp(_time.time(), LA))
    now = int(_time.time())
    midnight = int(datetime.fromtimestamp(now, LA).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    stored = [Rd(t, 120.0) for t in range(now - 20 * 86400, now - 600, 300)]
    locked = []

    async def executor(fn, *args):
        return fn(*args)

    def _locked(fn, *args):
        locked.append(fn.__name__)
        return fn(*args)

    hass = types.SimpleNamespace(
        config=types.SimpleNamespace(time_zone="America/Los_Angeles"), async_add_executor_job=executor
    )
    storage = types.SimpleNamespace(readings=stored, reading_intervals=lambda fb: [300] * len(stored), edits=0)
    coord = types.SimpleNamespace(
        storage=storage,
        locked=_locked,
        detected_interval_seconds=300,
        regional=Regional(),
        display_unit="mg/dL",
        configuration=lambda: {"primary_source": {"name": "Test"}},
        settings={},
        integration_version="0",
        async_add_listener=lambda listener: None,
    )
    builds = []
    real = R.build_report
    monkeypatch.setattr(RS.R, "build_report", lambda *a, **k: builds.append(1) or real(*a, **k))
    reports = RS.GlucoCraftReports(hass, coord)
    first = asyncio.run(reports.async_data(14))
    assert "reads_until" not in first and first["sources"] == ["Test"] and len(builds) == 1
    assert locked == ["copy"]  # only the copy under the lock; the report itself is built without it
    stored.append(Rd(now - 300, 120.0))  # a new reading after reads_until (the first one after midnight)
    again = asyncio.run(reports.async_data(14))
    assert len(builds) == 1 and again["standard"] is first["standard"] and again is not first
    stored.insert(next(i for i, r in enumerate(stored) if r.timestamp > midnight - 3000), Rd(midnight - 3000 + 1, 99.0))
    asyncio.run(reports.async_data(14))
    assert len(builds) == 2  # a late reading before the last midnight
    reports._lab = [{"date": "2026-09-01", "a1c": 7.0}]  # a laboratory result (the actions also clear the cache)
    asyncio.run(reports.async_data(14))
    assert len(builds) == 3
