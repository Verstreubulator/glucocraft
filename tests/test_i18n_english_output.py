# License: AGPL-3.0-only (LICENSE).
"""The English texts of the backend are unchanged by the language catalogues (custom_components/glucocraft/i18n).

Every user-visible text of the backend comes from a catalogue (i18n.py). This test compares the English output of
every surface with a snapshot taken from the code before the texts were moved: the notifications (the summaries, the
phone test, the panel notices, the export and print messages), the spoken summary,
the CSV headers, the regional words, the settings-screen texts made in Python, the panel feed's page texts, the
entity names and states, and the CGM Report (the report data's texts, every text the PDF draws with its position, and
the PDF's text and document information).

To accept an intended change of an English text, write the snapshot again and review its difference:
    GLUCOCRAFT_WRITE_SNAPSHOT=1 python -m pytest tests/test_i18n_english_output.py
"""

from __future__ import annotations

import asyncio
import difflib
import io
import json
import os
import time
import types
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from glucocraft import const as C
from glucocraft import contrast as CT
from glucocraft import export as EX
from glucocraft import notification as N
from glucocraft import panel_feed as PF
from glucocraft import panel_notice as pn
from glucocraft import report as R
from glucocraft import sensor as se
from glucocraft import spoken as sp
from glucocraft.change import ChangeOverTime
from glucocraft.regional import Regional

from conftest import LA, PERIOD_KEYS, REPO, stats_record

SNAPSHOT = Path(__file__).parent / "snapshots" / "english_output.json"
PKG = REPO / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = int(NOW_LOCAL.timestamp())


@pytest.fixture
def fixed_clock(monkeypatch):
    """Home Assistant's time zone and the clock of the notifications at NOW (Los Angeles)."""
    from homeassistant.util import dt as dt_util

    import ha_stub

    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", LA)
    monkeypatch.setattr(dt_util, "as_local", lambda x: x.astimezone(LA))
    monkeypatch.setattr(N, "time", types.SimpleNamespace(time=lambda: float(NOW)))
    return ha_stub


# ------------------------------------------------------------------------------------------------ notifications
def _coordinator(make_coordinator, unit="mg/dL", custom=False, avail=95.0, **kw):
    kw.setdefault("current_glucose", 62.0)
    c = make_coordinator(
        display_unit=unit,
        custom_ranges=custom,
        period_stats={
            k: stats_record(data_availability=avail, period_seconds=C.PERIOD_SECONDS[k]) for k in PERIOD_KEYS
        },
        period_stats_agp={k: stats_record(data_availability=avail, pct_time_in_target_range=65.0) for k in PERIOD_KEYS},
        **kw,
    )
    if custom:
        c.range_low, c.range_high = 80, 160
    c.period_stats_agp["90d"] = stats_record(pct_time_in_target_range=58.0)
    c.last_reading_ts, c.display_status = NOW - 60, "current"
    return c


def _summaries(make_coordinator, fake_hass) -> dict[str, str]:
    out = {}
    for unit in ("mg/dL", "mmol/L"):
        for custom in (False, True):
            for avail in (95.0, 50.0):
                c = _coordinator(make_coordinator, unit, custom, avail)
                tag = f"{unit}|custom={custom}|active={avail}"
                out[f"daily|{tag}"] = N._build_daily_summary(c)
                out[f"weekly|{tag}"] = N._build_weekly_summary(c)
                out[f"monthly|{tag}"] = N._build_monthly_summary(c)
    # too little data (sensor active below 70%, the enough-data rule), and the monthly sections by data
    c = _coordinator(make_coordinator)
    for k in PERIOD_KEYS:
        c.period_stats[k] = stats_record(reading_count=5, data_availability=40.0, period_seconds=C.PERIOD_SECONDS[k])
    out["daily|insufficient"] = N._build_daily_summary(c)
    out["weekly|insufficient"] = N._build_weekly_summary(c)
    # the monthly comparisons are judged on the consensus-range statistics of the same readings, short of data alike
    for k in ("90d", "180d"):
        c.period_stats_agp[k] = stats_record(
            reading_count=5, data_availability=40.0, period_seconds=C.PERIOD_SECONDS[k]
        )
    out["monthly|insufficient"] = N._build_monthly_summary(c)
    c = _coordinator(make_coordinator)
    for k in ("90d", "180d"):  # (each with its own length: the time still needed comes from it)
        c.period_stats[k] = stats_record(reading_count=100, data_availability=40.0, period_seconds=C.PERIOD_SECONDS[k])
        # the consensus-range statistics of the same readings, short of data alike (the comparisons are judged on them)
        c.period_stats_agp[k] = stats_record(
            reading_count=100, data_availability=40.0, period_seconds=C.PERIOD_SECONDS[k]
        )
    out["monthly|30d only"] = N._build_monthly_summary(c)
    # no current data, a LO reading, a reading of another day, the sensor expiry line
    c = _coordinator(make_coordinator, trend_arrow="↘")
    c.trend_description = "Falling"
    out["daily|falling"] = N._build_daily_summary(c)
    c.display_status, c.last_reading_ts, c.no_current_data = "no_current_data", NOW - 3 * 3600, True
    out["daily|no current data"] = N._build_daily_summary(c)
    # the no-data notice of that outage: its title and its text
    out["no data notice|title"] = N.t("notify.no_data.title")
    out["no data notice|message"] = N.no_data_notice_message(c, NOW)
    c.last_reading_ts = NOW - 2 * 86400
    out["daily|other day"] = N._build_daily_summary(c)
    # after the stated expiry, a current reading: the reading with its trend and "Sensor expired", no "No current data"
    c = _coordinator(make_coordinator, trend_arrow="↘")
    c.trend_description, c.display_status = "Falling", "expired"
    c.expires_at = datetime.fromtimestamp(time.time(), UTC) - timedelta(hours=2)
    out["daily|expired, current reading"] = N._build_daily_summary(c)
    c = _coordinator(make_coordinator, current_glucose=40.0)
    c.current_limit = "low"
    out["daily|LO"] = N._build_daily_summary(c)
    for hours in (-1, 0.4, 5, 24, 30, 48, 73.6):
        c = _coordinator(make_coordinator)
        # the coordinator's expiry (UTC)
        c.expires_at = datetime.fromtimestamp(time.time(), UTC) + timedelta(hours=hours)
        out[f"daily|expiry {hours}"] = N._build_daily_summary(c)
    for pct in (0, 0.5, 4, 10, 25, 70, 100):
        out[f"pct_to_time|{pct}"] = N._pct_to_time(pct)
    for s in (0, 59, 120, 3600, 3660, 86400, 90061):
        out[f"age|{s}"] = N._age_text(s)
    return out


def _phone_test(monkeypatch) -> dict[str, str]:
    from glucocraft import config_flow as CF
    from glucocraft import flow_schemas as fs

    monkeypatch.setattr(fs.selector, "SelectOptionDict", dict)

    calls = []

    async def call(domain, service, data=None, **kw):
        calls.append((domain, service, data))

    hass = types.SimpleNamespace(
        services=types.SimpleNamespace(has_service=lambda d, n: True, async_call=call),
        config=types.SimpleNamespace(language="en"),
        async_add_executor_job=lambda fn, *a: asyncio.sleep(0, fn(*a)),
    )
    asyncio.run(CF._send_phone_test(hass, ["mobile_app_phone"]))
    (_, _, data) = calls[0]
    names = {"notify": {"mobile_app_phone": None, "family_tablet": None}}
    hass.services.async_services_for_domain = lambda domain: dict(names.get(domain, {}))
    labels = [o["label"] for o in fs._notify_targets(hass, ["mobile_app_old"])]
    return {
        "phone_test|title": data["title"],
        "phone_test|message": data["message"],
        "notify_targets": " | ".join(labels),
        "panel_address|none": CF._panel_address_text({}),
        "panel_address|set": CF._panel_address_text({C.CONF_PANEL_WEBHOOK_ID: "glucocraft_panel_0123"}),
        "plain_text": N.plain_text("**GlucoCraft Daily Summary**\n\n*No data.* 2*3"),
    }


def _phone_types() -> dict[str, str]:
    """The labels of the phone notification types (a selector of the settings screen)."""
    options = (STRINGS.get("selector", {}).get(C.CONF_PHONE_TYPES) or {}).get("options")
    labels = options if options else C.PHONE_TYPES
    return {f"phone_types|{k}": labels[k] for k in C.PHONE_TYPES}


def _panel_notices(monkeypatch) -> dict[str, str]:
    out = {}
    for card in ("square", "rectangular"):
        for model in ("panel", "waveshare-lcd7-800x480", "waveshare-p4-7b-1024x600"):
            notes = []
            monkeypatch.setattr(
                pn.persistent_notification, "async_create", lambda hass, msg, notes=notes, **kw: notes.append((msg, kw))
            )
            devices = {}
            monkeypatch.setattr(pn, "_panels", lambda hass, devices=devices: dict(devices))
            hass = types.SimpleNamespace(
                bus=types.SimpleNamespace(async_listen=lambda *a: lambda: None),
                async_create_task=lambda coro: coro.close(),
                config=types.SimpleNamespace(language="en"),
            )
            ent = types.SimpleNamespace(entry_id="t", data={C.CONF_DISPLAY_CARD: card}, async_on_unload=lambda f: None)
            asyncio.run(pn.async_setup_panel_notice(hass, ent))
            devices["d1"] = types.SimpleNamespace(id="d1", model=model, name="Panel A", name_by_user=None)
            asyncio.run(pn.async_setup_panel_notice(hass, ent))
            msg, kw = notes[0]
            out[f"panel_added|{card}|{model}"] = f"{kw['title']}\n{msg}"
    # the offline notice: the panel's Status entity off for 10 minutes
    for model in ("panel",):
        notes = []
        monkeypatch.setattr(
            pn.persistent_notification, "async_create", lambda hass, msg, notes=notes, **kw: notes.append((msg, kw))
        )
        monkeypatch.setattr(pn.persistent_notification, "async_dismiss", lambda *a, **k: None, raising=False)
        dev = types.SimpleNamespace(id="d1", model=model, name="Hall Panel", name_by_user=None)
        monkeypatch.setattr(pn, "_panel_status_entities", lambda hass, dev=dev: {"binary_sensor.hall_status": dev})
        monkeypatch.setattr(pn, "async_call_later", lambda hass, delay, fn: fn(None) or (lambda: None))
        monkeypatch.setattr(pn, "async_track_state_change_event", lambda *a: lambda: None)
        hass = types.SimpleNamespace(
            bus=types.SimpleNamespace(async_listen=lambda *a: lambda: None),
            states=types.SimpleNamespace(get=lambda e: types.SimpleNamespace(state="off")),
            config=types.SimpleNamespace(language="en"),
        )
        ent = types.SimpleNamespace(entry_id="t", data={}, async_on_unload=lambda f: None)
        asyncio.run(pn.async_setup_panel_offline(hass, ent))
        msg, kw = notes[0]
        out[f"panel_offline|{model}"] = f"{kw['title']}\n{msg}"
    return out


# ------------------------------------------------------------------------------------------------ spoken summary
def _spoken() -> dict[str, str]:
    out = {}
    for unit in ("mg/dL", "mmol/L"):
        for dec in (".", ","):
            for status in ("warm_up", "expired", "no_data", "no_current_data", "expiring_soon", "current"):
                # (the status "no_current_data" comes only with the no-current-data flag on: never with current data)
                for current in (True, False) if status != "no_current_data" else (False,):
                    for mg, limit, zone in (
                        (112.0, None, "tight_range"),
                        (40.0, "low", "very_low"),
                        (500, "high", "very_high"),
                        (62.0, None, "low"),
                    ):
                        for trend in ("Changing Slowly", "↓", "Unknown", None):
                            key = f"{unit}|{dec}|{status}|{current}|{mg}|{limit}|{trend}"
                            out[key] = sp.spoken_summary(
                                status=status,
                                current=current,
                                mg=mg,
                                limit=limit,
                                zone=zone,
                                trend=trend,
                                reading_when="at 7:46 AM",
                                unit=unit,
                                dec=dec,
                            )
    out["no reading"] = sp.spoken_summary(
        status="current", current=True, mg=None, limit=None, zone=None, trend=None, reading_when=None, unit="mg/dL"
    )
    today = date(2026, 10, 22)
    for fmt in ("MDY", "DMY", "YMD"):
        for tf in ("12", "24"):
            rg = Regional(tf, fmt)
            for d in (
                datetime(2026, 10, 22, 21, 45),
                datetime(2026, 10, 21, 21, 45),
                datetime(2026, 3, 2, 7, 5),
                datetime(2025, 12, 31, 0, 1),
            ):
                out[f"when|{fmt}|{tf}|{d.isoformat()}"] = sp.reading_when(d, today, rg)
    for args in (
        (62, "mg/dL"),
        (1, "min"),
        (1.0, "h", 1),
        (3.4, "mmol/L", 1, ","),
        (-1.2, "mg/dL/min", 1, ".", True),
        (2, "d"),
        (5, "%"),
        (48, "mmol/mol"),
        (7, "x"),
        (float("nan"), "mg/dL"),
    ):
        out[f"speak|{args}"] = sp.speak(*args)
    for trend in ("Rising Quickly", "↗", "falling", "", None):
        out[f"speak_trend|{trend}"] = sp.speak_trend(trend)
    for lim in ("low", "high", None):
        out[f"speak_limit|{lim}"] = sp.speak_limit(lim)
    return out


# ------------------------------------------------------------------------------------------------ small texts
@dataclass
class _Rd:
    """A stored reading as the report reads it."""

    timestamp: int
    raw: float
    source: int = 0
    limit: str | None = None


def _small(make_coordinator) -> dict[str, str]:
    out = {}
    rows = [_Rd(NOW - 600, 112.0, 1), _Rd(NOW - 300, 40.0, 2, "low")]
    for unit in ("mg/dL", "mmol/L"):
        out[f"csv|{unit}"] = EX.readings_csv(rows, unit, LA)
    out["csv|lab"] = EX.lab_csv(
        [{"date": "2026-09-01", "a1c": 7.1}, {"date": "2026-06-10", "a1c": 6.53, "a1c_mmol_mol": 48}]
    )
    for tf, df, fw, dec, a1c in (
        ("12", "MDY", 6, ".", "percent"),
        ("24", "DMY", 0, ",", "both"),
        ("24", "YMD", 5, ".", "mmol_mol"),
    ):
        rg = Regional(tf, df, fw, dec, a1c)
        d = datetime(2026, 10, 22, 19, 5)
        out[f"regional|{tf}{df}"] = " | ".join(
            [
                rg.clock(d),
                rg.hour(7),
                rg.hour(19),
                rg.day_month(d),
                rg.day_month(d, long=True),
                rg.weekday_numeric(d),
                rg.stamp(d),
                rg.stamp_year(d),
                *rg.weekday_names(),
                *rg.weekday_names(short=False),
                rg.a1c(7.04, 53.4),
            ]
        )
    out["regional|months"] = " | ".join(Regional().day_month(datetime(2026, m, 1), long=True) for m in range(1, 13))
    out["regional|months_short"] = " | ".join(Regional().day_month(datetime(2026, m, 1)) for m in range(1, 13))
    # the settings screen's contrast note
    colors = C.zone_colors({})
    for card, panel in (
        ("#000000", "#000000"),
        ("#FFFFFF", "#000000"),
        ("theme", "#FFFFFF"),
        ("theme", "#000000"),
        ("#FFFF00", "#FFFF00"),
    ):
        out[f"contrast|{card}|{panel}"] = CT.contrast_note(colors, C.BAND_COLORS, card, panel)
    # the panel feed's page texts (consensus and custom; both units)
    for unit in ("mg/dL", "mmol/L"):
        c = make_coordinator(display_unit=unit)
        for custom in (False, True):
            out[f"panel_feed|{unit}|{custom}"] = "\n".join(PF._range_set(c, custom)[1])
        c.goal_time_in_target_range, c.goal_gmi = None, 7.0
        out[f"panel_feed|{unit}|goals"] = "\n".join(PF._range_set(c, True)[1])
    # the report data's texts and the report service's messages
    out["report|no_comparison"] = R.no_comparison_note(200)
    out["report|goal_rows"] = " | ".join(R.goal_label(row[0]) for row in R.GOAL_ROWS)
    return out


# ------------------------------------------------------------------------------------------------ entities
def _entity_name(platform: str, e) -> str:
    """The entity's English name: its own name (before the translations), else the name of its translation key."""
    own = e.__dict__.get("_attr_name")
    if isinstance(own, str):
        return own
    from test_entity_ids import english_name

    return english_name(platform, e)


def _state_name(platform: str, e, state) -> str:
    """A state as Home Assistant shows it: its name in strings.json when the entity's state is translated."""
    key = e.__dict__.get("_attr_translation_key")
    names = (STRINGS.get("entity", {}).get(platform, {}).get(key or "", {}) or {}).get("state", {})
    return names.get(str(state), str(state))


def _entity_texts(pairs) -> dict[str, str]:
    out = {}
    for platform, e in pairs:
        uid = e._attr_unique_id.split("_", 1)[1]
        out[f"entity|{platform}|{uid}"] = f"{e.entity_id} | {_entity_name(platform, e)}"
    return out


def test_entity_names_and_ids(tmp_path):
    from test_entity_ids import _entities as entities
    from test_trend_and_configuration import settings_coordinator as mk

    c = mk(tmp_path)
    got = _entity_texts(entities(c))
    _compare("entities", got)


def test_entity_states(tmp_path, monkeypatch, fixed_clock):
    """The English states and the texts in the attributes, as Home Assistant shows them."""
    from test_entity_ids import _entities as entities
    from test_trend_and_configuration import settings_coordinator as mk

    monkeypatch.setattr(se, "time", types.SimpleNamespace(time=lambda: float(NOW + 60)))
    c = mk(tmp_path)
    now = NOW
    c.current_glucose, c.no_current_data, c.last_reading_ts = 50.0, False, now
    c.period_stats = {p: stats_record(period_label=p, min_value=40.0) for p in PERIOD_KEYS}
    c.period_stats_agp = dict(c.period_stats)
    c.weekly_trends = c.weekly_trends_agp = [stats_record()] * 3
    c.weekly_trends_at, c.trend_gmi14, c.trend_directions = now, [6.9] * 3, {"tir": "Stable"}
    weeks14 = [{"active": 90, "active_ok": True}, {"active": 50, "active_ok": False}]
    block = ChangeOverTime(weeks14=weeks14, week_ends=[now])
    c.change, c.change_at = (
        {"consensus": block, "personal": ChangeOverTime(weeks14=list(weeks14), week_ends=[now])},
        now,
    )
    pairs = entities(c)
    out = {}
    for platform, e in pairs:
        uid = e._attr_unique_id.split("_", 1)[1]
        if (
            uid == "last_reading"
            or uid.split("_", 1)[0] in PERIOD_KEYS
            and uid.split("_", 1)[1] not in ("min", "max", "data_availability")
        ):
            continue
        try:
            state = e.native_value if platform == "sensor" else e.is_on
        except Exception as err:  # noqa: BLE001 - a state that needs more of Home Assistant
            state = f"<{err.__class__.__name__}>"
        if platform == "sensor" and isinstance(state, str):
            state = _state_name(platform, e, state)
        try:
            attrs = e.extra_state_attributes or {}
        except Exception:  # noqa: BLE001 - attributes that need more of Home Assistant
            attrs = {}
        attrs = attrs if isinstance(attrs, dict) else {}
        texts = {k: v for k, v in attrs.items() if isinstance(v, str) and k not in ("computed_at", "expires_at")}
        out[f"state|{platform}|{uid}"] = json.dumps(
            [state if not isinstance(state, float) else round(state, 3), texts],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    _compare("entity_states", out)


# ------------------------------------------------------------------------------------------------ the CGM Report
def _strings_of(value, path="") -> list[str]:
    """Every text in the report data (numbers left out), with its path."""
    if isinstance(value, str):
        return [f"{path}={value}"]
    if isinstance(value, dict):
        return [s for k in sorted(value, key=str) for s in _strings_of(value[k], f"{path}.{k}")]
    if isinstance(value, (list, tuple)):
        return [s for i, v in enumerate(value) for s in _strings_of(v, f"{path}[{i}]")]
    return []


def _report_cases():
    from test_pdf_layout_and_regional import LAB, READINGS

    kept_from = NOW_LOCAL.replace(hour=0) - timedelta(days=200)
    lab = R.lab_payload(LAB, READINGS, 300, LA, 200, kept_from)
    custom = {
        "ranges": {"very_low": 54, "low": 80, "tight_low": 80, "tight_high": 140, "high": 160, "very_high": 250},
        "goals": {"time_in_range": {"op": ">", "value": 75}, "cv": {"op": "<=", "value": 33}},
    }
    events = [{"start": NOW - 5 * 86400, "end": NOW - 5 * 86400 + 3600}, {"start": NOW - 2 * 86400, "end": None}]
    for name, days, full, unit, rg, extra in (
        ("14 summary mg/dL", 14, False, "mg/dL", Regional(), {}),
        (
            "30 full mmol/L DMY comma both",
            30,
            True,
            "mmol/L",
            Regional("24", "DMY", 0, ",", "both", "a4"),
            {"custom": custom, "research": True},
        ),
        (
            "90 summary mg/dL mmol_mol",
            90,
            False,
            "mg/dL",
            Regional("12", "YMD", 5, ".", "mmol_mol"),
            {"custom": custom},
        ),
    ):
        data = R.build_report(READINGS, 300, LA, days, full, LAB, now=NOW, regional=rg, sensor_events=events, **extra)
        data["lab"] = lab
        data.update(
            {
                "label": {14: "14 Days", 30: "30 Days", 90: "90 Days"}[days],
                "sources": ["Test source"],
                "created": rg.stamp_year(NOW_LOCAL),
                "unit": unit,
                **rg.as_dict(),
                "integration_version": "0.0.5",
                "include_custom_appendix": "custom" in extra,
                "include_research_appendix": bool(extra.get("research")),
            }
        )
        yield name, data, full, name.endswith("both")


def _report(monkeypatch) -> dict[str, str]:
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    from glucocraft import report_pdf as P

    out = {}
    for name, data, full, color in _report_cases():
        out[f"report_data|{name}"] = "\n".join(_strings_of(data))
        drawn: list[str] = []
        page = [1]
        orig = P.Report.text

        def text(self, x, y, s, size=9, bold=False, g=0.0, align="left", said=None, orig=orig, drawn=drawn, page=page):
            drawn.append(f"{page[0]} {x:.1f} {y:.1f} {size:g}{'b' if bold else ''} {align[0]} {s}")
            return orig(self, x, y, s, size, bold, g, align, said)

        monkeypatch.setattr(P.Report, "text", text)
        from reportlab.pdfgen import canvas as cv

        orig_show = cv.Canvas.showPage

        def show(self, orig_show=orig_show, page=page):
            page[0] += 1
            return orig_show(self)

        monkeypatch.setattr(cv.Canvas, "showPage", show)
        pdf, pages = P.render(data, color, full)
        monkeypatch.setattr(P.Report, "text", orig)
        monkeypatch.setattr(cv.Canvas, "showPage", orig_show)
        out[f"report_drawn|{name}"] = "\n".join(drawn)
        reader = pypdf.PdfReader(io.BytesIO(pdf))
        meta = reader.metadata
        out[f"report_meta|{name}"] = f"{pages} | {meta.title} | {meta.author} | {meta.subject} | {meta.creator}"
        out[f"report_pdf_text|{name}"] = "\n".join(p.extract_text() for p in reader.pages)
    return out


def _ipp_messages(monkeypatch) -> dict[str, str]:
    pytest.importorskip("reportlab")
    import urllib.error
    import urllib.request

    from glucocraft import report_pdf as P

    out = {}

    class _Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

    replies = {
        "ok_job": lambda r, timeout: _Resp(
            b"\x02\x00\x00\x00\x00\x00\x00\x01\x02" + b"\x21\x00\x06job-id\x00\x04\x00\x00\x00\x07" + b"\x03"
        ),
        "ok": lambda r, timeout: _Resp(b"\x02\x00\x00\x00\x00\x00\x00\x01\x03"),
        "refused": lambda r, timeout: (_ for _ in ()).throw(
            urllib.error.HTTPError("u", 426, "Upgrade Required", {}, None)
        ),
        "invalid": lambda r, timeout: (_ for _ in ()).throw(ValueError("x")),
        "unreachable": lambda r, timeout: (_ for _ in ()).throw(
            urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        ),
        "other": lambda r, timeout: (_ for _ in ()).throw(RuntimeError("x")),
        "short": lambda r, timeout: _Resp(b"\x02\x00"),
        "rejected": lambda r, timeout: _Resp(b"\x02\x00\x04\x00\x00\x00\x00\x01\x03"),
    }
    for name, fn in replies.items():
        monkeypatch.setattr(urllib.request, "urlopen", fn)
        out[f"ipp|{name}"] = repr(P.ipp_print("printer.local", b"%PDF", "job"))
    return out


# ------------------------------------------------------------------------------------------------ the comparison
def _compare(group: str, got: dict[str, str]) -> None:
    """Compare a group of outputs with the snapshot, or write it (GLUCOCRAFT_WRITE_SNAPSHOT=1)."""
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.is_file() else {}
    if os.environ.get("GLUCOCRAFT_WRITE_SNAPSHOT"):
        snap[group] = got
        SNAPSHOT.parent.mkdir(exist_ok=True)
        text = json.dumps(snap, ensure_ascii=False, indent=0, sort_keys=True) + "\n"
        SNAPSHOT.write_text(text, encoding="utf-8", newline="\n")  # LF on every system
        return
    assert group in snap, f"no snapshot of {group}: write it with GLUCOCRAFT_WRITE_SNAPSHOT=1"
    want = snap[group]
    assert sorted(got) == sorted(want), sorted(set(got) ^ set(want))
    bad = [k for k in want if got[k] != want[k]]
    if bad:
        k = bad[0]
        diff = "\n".join(
            difflib.unified_diff(want[k].splitlines(), got[k].splitlines(), "snapshot", "now", lineterm="", n=1)
        )
        pytest.fail(f"{len(bad)} English outputs changed, the first: {k}\n{diff[:6000]}")


def test_notifications(make_coordinator, fake_hass, fixed_clock, monkeypatch):
    _compare("summaries", _summaries(make_coordinator, fake_hass))
    _compare("phone", {**_phone_test(monkeypatch), **_phone_types()})
    _compare("panel_notices", _panel_notices(monkeypatch))


def test_spoken_summary():
    _compare("spoken", _spoken())


def test_small_texts(make_coordinator):
    _compare("small", _small(make_coordinator))


@pytest.mark.slow
def test_report(monkeypatch, fixed_clock):
    _compare("report", _report(monkeypatch))


def test_printer_messages(monkeypatch):
    _compare("ipp", _ipp_messages(monkeypatch))


def test_export_and_print_messages():
    """The export and print notifications, word for word as the code wrote them before the catalogues."""
    from glucocraft.report_service import print_message

    files = ["glucocraft-readings_2026-10-22_090000.csv", "glucocraft-lab-a1c_2026-10-22_090000.csv"]
    assert EX.export_message({"readings": 5000, "files": files}) == (
        "5000 readings exported to the folder glucocraft_exports of the Home Assistant configuration: "
        f"{files[0]}, {files[1]}. The last 12 exports are kept."
    )
    assert print_message(True, "sent to the printer (job 7)", 6, "x.pdf") == (
        "Printed: sent to the printer (job 7). 6 pages; PDF kept as x.pdf."
    )
    assert print_message(False, "printer not reachable (Connection refused)", 9, "y.pdf") == (
        "Not printed: printer not reachable (Connection refused). 9 pages; PDF kept as y.pdf."
    )
    # a count of one: the singular (before the catalogues: "1 readings", "1 pages")
    assert EX.export_message({"readings": 1, "files": files[:1]}).startswith("1 reading exported to the folder")
    assert print_message(True, "sent to the printer", 1, "z.pdf").endswith("1 page; PDF kept as z.pdf.")


def test_reading_time_is_utc_aware():
    """The fixed clock of this test is a real instant (a guard for the snapshot's times)."""
    assert datetime.fromtimestamp(NOW, UTC).astimezone(LA) == NOW_LOCAL
