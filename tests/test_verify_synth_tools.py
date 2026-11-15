# License: AGPL-3.0-only (LICENSE).
"""The synthetic-data tools refuse unreasonable input clearly, never produce silently wrong data, and keep the
behavior the tests below pin. Also the calculation branches that the rest of the suite did not reach (coverage).
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import threading
import types
from datetime import datetime, timedelta
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from glucocraft import limits as L
from glucocraft import panel_feed as PF
from glucocraft import report as R
from glucocraft import statistics as st
from glucocraft.storage import GlucoseReading

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
common = importlib.import_module("tools.synth.common")
E = importlib.import_module("tools.synth.expected")
G = importlib.import_module("tools.synth.generate")
P = importlib.import_module("tools.synth.profile")
S = importlib.import_module("tools.synth.scenarios")
X = importlib.import_module("tools.synth.example_profile")
feed = importlib.import_module("tools.synth.live_feed")
rec = importlib.import_module("tools.synth.recorder_write")

LA = ZoneInfo("America/Los_Angeles")
START = datetime(2026, 3, 1, tzinfo=LA)
T0 = 1_780_000_000


# ------------------------------------------------------------------------------------------- units
@pytest.mark.parametrize("text", ["mmol/mol", "mg/dL mmol", "%", "", "mgdl/mmol", "mol"])
def test_unit_name_refuses_what_is_no_glucose_unit(text):
    """'mmol/mol' (the A1C unit) was taken as mmol/L, and any text containing 'mg' as mg/dL."""
    with pytest.raises(ValueError):
        common.unit_name(text)


@pytest.mark.parametrize(
    "text, unit",
    [
        ("mg/dL", "mg/dL"),
        ("MG/DL", "mg/dL"),
        ("mgdl", "mg/dL"),
        ("mmol/L", "mmol/L"),
        ("mmol/l", "mmol/L"),
        (" mmol ", "mmol/L"),
    ],
)
def test_unit_name_accepts_the_glucose_units(text, unit):
    assert common.unit_name(text) == unit


def test_scenario_and_generator_refuse_a_wrong_unit():
    with pytest.raises(ValueError):
        S.build("range_limits", "mmol/mol")
    with pytest.raises(ValueError):
        G.generate(None, start=START, hours=1, unit="mmol/mol")


# ------------------------------------------------------------------------------------------- expected.py
@pytest.mark.parametrize("text", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_expected_classify_non_finite_is_no_reading(text):
    assert E.classify(text, "mg/dL") == ("none", None)
    assert E.classify(text, "mmol/L") == ("none", None)


def test_expected_store_refuses_unsorted_or_repeated_times():
    """An unsorted list gave a negative weight (-300 s) without any error."""
    with pytest.raises(ValueError):
        E.Store.build([E.Reading(T0 + 300, Fraction(100)), E.Reading(T0, Fraction(200))])
    with pytest.raises(ValueError):
        E.Store.build([E.Reading(T0, Fraction(100)), E.Reading(T0, Fraction(200))])
    with pytest.raises(ValueError):
        E.Store.build([E.Reading("x", Fraction(100))])
    assert E.Store.build([]).readings == []


def test_expected_refuses_negative_periods():
    with pytest.raises(ValueError):
        E.metrics([(0, 10, E.Reading(0, Fraction(100)))], -5)
    store = E.Store.build([E.Reading(T0, Fraction(100))])
    for ndays in (0, -1, 1.5):
        with pytest.raises(ValueError):
            E.report(store, LA, T0 + 86400, ndays)


# ------------------------------------------------------------------------------------------- scenarios
def test_scenarios_refuse_unknown_names_and_time_going_back():
    with pytest.raises(ValueError, match="known"):
        S.build("no_such_scenario", "mg/dL")
    tr = S.Track(S.NOW, 300, "mg/dL").reading(100)
    with pytest.raises(ValueError):
        tr.skip(0)
    with pytest.raises(ValueError):
        tr.skip(-60)
    with pytest.raises(ValueError):
        tr.hold(100, -10)


def test_every_scenario_has_strictly_increasing_measurement_times():
    for name in S.NAMES:
        for unit in common.UNITS:
            sc = S.build(name, unit)
            for states in (sc.states, sc.secondary or []):
                t = sorted({s.measured for s in states if s.measured is not None})
                assert len(t) == len({s.measured for s in states if s.measured is not None})
                assert all(isinstance(x, int) for x in t), name


# ------------------------------------------------------------------------------------------- generate
@pytest.mark.parametrize(
    "kw",
    [
        {"hours": float("nan")},
        {"hours": float("inf")},
        {"hours": -5},
        {"upper": 30},
        {"upper": float("nan")},
        {"delay": -100},
        {"delay": 1.5},
    ],
)
def test_generator_refuses_impossible_options(kw):
    """These gave an empty series, a series of only HI, never any HI, or states recorded before they were measured."""
    with pytest.raises(ValueError):
        G.generate(None, start=START, **({"hours": 1} | kw))


def test_generator_takes_one_modifier_name_as_a_name():
    a = G.generate(None, start=START, hours=6, seed=3, modifiers="stable")
    b = G.generate(None, start=START, hours=6, seed=3, modifiers=["stable"])
    assert [s.state for s in a.states] == [s.state for s in b.states]


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["time_of_day"].__setitem__("p50", [None] * 96),
        lambda p: p["time_of_day"].__setitem__("p50", [float("nan")] * 96),
        lambda p: p["time_of_day"].__setitem__("p50", p["time_of_day"]["p50"][:10]),
        lambda p: p["time_of_day"].__setitem__("p25", [x + 500 for x in p["time_of_day"]["p25"]]),  # p25 > p50
        lambda p: p["gaps"].__setitem__("per_week", -5),
        lambda p: p["variability"].__setitem__("ar1_phi", 1.5),
        lambda p: p["noise"].__setitem__("sd_mgdl", float("inf")),
        lambda p: p["intervals"].__setitem__("missed_share", 2.0),
    ],
)
def test_generator_refuses_a_broken_profile(change):
    """A NaN profile generated plausible-looking values; a negative rate or phi >= 1 was clamped without a word."""
    prof = X.example_profile()
    change(prof)
    with pytest.raises(ValueError):
        G.generate(prof, start=START, hours=1)


def test_example_profile_is_valid():
    assert P.validate_profile(X.example_profile())


def test_generator_across_the_fall_back_hour_keeps_time_in_order():
    s = G.generate(
        None, start=datetime(2026, 10, 31, 22, tzinfo=LA), hours=6, interval=300, aligned=True, modifiers=["no_gaps"]
    )
    measured = [x.measured for x in s.states if x.measured is not None]
    assert all(b - a == 300 for a, b in pairwise(measured)) and len(measured) == 72
    recorded = [x.recorded for x in s.states]
    assert all(b > a for a, b in pairwise(recorded))


def test_no_reading_states_are_not_counted_twice_at_gaps():
    """The profile counts every no-reading state, also the one at a gap's start; the generator wrote that one at
    every gap AND the profile's full rate at random (refit: about twice the profile's rate)."""
    prof = X.example_profile()
    prof["no_reading_states"]["per_week"] = 4.0
    prof["gaps"]["per_week"] = 4.0
    s = G.generate(prof, start=START, hours=24 * 70, interval=300, seed=4)
    c = s.meta["counts"]
    per_week = c["no_reading"] / 10
    assert c["gaps"] > 0 and per_week < 6.0  # about 4 per week, never about 8


def test_noise_is_not_added_on_top_of_what_the_body_already_has():
    """The profile's noise is measured from second differences of real readings (curvature, jitter and rounding
    included); refit from a generated series it came out about 1.5 x the profile's. Now within 30 %."""
    s = G.generate(None, start=START, hours=24 * 21, interval=60, seed=7)
    rows = [{"state": x.state, "attributes": {"measurement_timestamp": x.measured}} for x in s.states]
    fitted = P.fit(*P.parse_states(rows)[:1])
    want = X.example_profile()["noise"]["sd_mgdl"]
    assert abs(fitted["noise"]["sd_mgdl"] - want) / want < 0.3


# ------------------------------------------------------------------------------------------- profile fit
def test_fit_refuses_non_finite_and_sorts_its_input():
    good = [(T0 + 60 * i, 100.0 + (i % 9), "reading") for i in range(300)]
    with pytest.raises(ValueError):
        P.fit(good[:150] + [(T0 + 60 * 150, float("nan"), "reading")] + good[151:])
    shuffled = sorted(good, key=lambda r: (r[0] * 7919) % 300)
    assert P.fit(shuffled)["summary"] == P.fit(good)["summary"]


def test_a_fitted_profile_holds_no_series():
    """Only summary statistics: no list longer than the 96 time-of-day bins, nothing per reading."""
    s = G.generate(None, start=START, hours=24 * 14, seed=2)
    rows = [{"state": x.state, "attributes": {"measurement_timestamp": x.measured}} for x in s.states]
    fitted = P.fit(*P.parse_states(rows)[:1])

    def lists(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from lists(v)
        elif isinstance(o, list):
            yield len(o)
            for v in o:
                yield from lists(v)

    assert max(lists(fitted)) <= P.BINS
    measured = {x.measured for x in s.states if x.measured}
    text = json.dumps(fitted)
    assert not any(str(t) in text for t in list(measured)[:500])


# ------------------------------------------------------------------------------------------- the live feed
def test_feed_writes_lo_and_hi_that_glucocraft_reads_as_lo_and_hi():
    """mmol/L: LO was sent as 39 and HI as 401, which GlucoCraft reads as 702 / 7224 mg/dL (both HI). The command
    line's mg/dL HI is 501: 401 is HI only for a Dexcom-type source, a measured 401 for the test source."""
    s = common.Series(
        unit="mmol/L",
        interval=60,
        tz="America/Los_Angeles",
        states=[
            common.SourceState(100, "LO", 70),
            common.SourceState(160, "Below 2.8 mmol/L", 130),
            common.SourceState(220, "nan", 190),
            common.SourceState(280, "HI", 250),
            common.SourceState(340, "Above 22.2 mmol/L", 310),
            common.SourceState(400, "6.7", 370),
        ],
    )
    values = [b["value"] for _, b in feed.payloads(s, "input_number.x")]
    assert values == [2.2, 2.2, 27.9, 27.9, 6.7]
    kinds = [L.classify(v, "mmol/L").kind for v in values]
    assert kinds == ["low", "low", "high", "high", "reading"]
    lo, hi = feed.CLI_LIMIT_VALUES["mg/dL"]
    assert L.classify(lo, None).kind == "low"
    for upper in L.UPPER_CHOICES:
        assert L.classify(hi, None, 40, upper).kind == "high"
    assert L.classify(401, None, 40, 500).kind == "reading"  # why 401 is not the default for the test source


def test_feed_stops_with_a_reason_when_home_assistant_is_unreachable():
    waits = []
    with pytest.raises(SystemExit, match="not reachable"):
        feed.run(
            "http://127.0.0.1:9", "t", [("/api/x", {"value": 1})], 0, "UTC", sleep=waits.append, log=lambda x: None
        )
    assert waits == [5, 10, 15]


@pytest.fixture
def answering():
    """A local HTTP server that answers every POST with the status in `codes` (one per request)."""
    codes: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(codes.pop(0) if codes else 200)
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", codes
    httpd.shutdown()


def test_feed_stops_on_a_missing_entity(answering):
    base, codes = answering
    codes.append(404)
    with pytest.raises(SystemExit, match="404"):
        feed.run(base, "t", [("/api/x", {"value": 1})], 0, "UTC", sleep=lambda x: None, log=lambda x: None)


@pytest.mark.parametrize("kw", [{"every": -1}, {"every": float("nan")}, {"start": 5}, {"start": -1}])
def test_feed_refuses_impossible_options(kw):
    with pytest.raises(ValueError):
        feed.run(
            "http://127.0.0.1:9",
            "t",
            [("/api/x", {"value": 1})],
            kw.get("every", 0),
            "UTC",
            start=kw.get("start", 0),
            sleep=lambda x: None,
            log=lambda x: None,
        )


# ------------------------------------------------------------------------------------------- recorder_write
def _has_homeassistant() -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import homeassistant.components.recorder.db_schema"], capture_output=True, check=False
    )
    return probe.returncode == 0


REFUSALS = r"""
import sys, sqlite3
sys.path.insert(0, sys.argv[1])
from datetime import datetime
from zoneinfo import ZoneInfo
from tools.synth import generate, recorder_write as rw
s = generate.generate(None, start=datetime(2026, 3, 1, tzinfo=ZoneInfo("America/Los_Angeles")), hours=2, seed=1)
rows = rw.state_rows(s, "sensor.synthetic_glucose")
url = "sqlite:///" + sys.argv[2]
out = []
def attempt(name, fn):
    try:
        fn(); out.append(name + ":written")
    except (SystemExit, ValueError) as e:
        out.append(name + ":refused")
attempt("first", lambda: rw.write(url, "sensor.synthetic_glucose", rows))
attempt("again", lambda: rw.write(url, "sensor.synthetic_glucose", rows))
attempt("replace", lambda: rw.write(url, "sensor.synthetic_glucose", rows, replace=True))
# a series that ends before states the entity already has (a source recorded up to the moment core was stopped):
# refused, then written with replace, which removes the later states as well
early = rows[: len(rows) // 2]
attempt("later_states", lambda: rw.write(url, "sensor.synthetic_glucose", early))
attempt("later_replace", lambda: rw.write(url, "sensor.synthetic_glucose", early, replace=True))
con = sqlite3.connect(sys.argv[2])
newest = con.execute("select max(last_updated_ts) from states").fetchone()[0]
con.close()
out.append("newest:" + ("series" if abs(newest - early[-1]["last_updated_ts"]) < 1e-6 else "other"))
attempt("bad_id", lambda: rw.write(url, "Not An Id", rows))
attempt("unsorted", lambda: rw.write(url, "sensor.other", list(reversed(rows))))
con = sqlite3.connect(sys.argv[2]); con.execute("update schema_changes set schema_version = 40"); con.commit(); con.close()
attempt("old_schema", lambda: rw.write(url, "sensor.other", rows))
con = sqlite3.connect(sys.argv[3]); con.execute("create table states (state_id integer primary key)"); con.commit(); con.close()
attempt("missing_tables", lambda: rw.write("sqlite:///" + sys.argv[3], "sensor.other", rows))
print(" ".join(out))
"""


@pytest.mark.skipif(
    not _has_homeassistant(),
    reason="the homeassistant package is not installed in this Python: "
    "recorder_write.write is untested here (run it on a test instance; docs/DEVELOPER_GUIDE.md)",
)
def test_recorder_write_refuses_what_would_corrupt_the_history(tmp_path):
    out = subprocess.run(
        [sys.executable, "-c", REFUSALS, str(REPO), str(tmp_path / "a.db"), str(tmp_path / "b.db")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert out == [
        "first:written",
        "again:refused",
        "replace:written",
        "later_states:refused",
        "later_replace:written",
        "newest:series",
        "bad_id:refused",
        "unsorted:refused",
        "old_schema:refused",
        "missing_tables:refused",
    ]


# ------------------------------------------------------------------------------------------- uncovered branches
def test_classify_a_nan_number_is_no_reading():
    assert L.classify(float("nan"), None).kind == "none"
    assert L.classify(float("nan"), "mmol/L").kind == "none"


def test_gri_zone_of_no_value_is_none():
    assert st.gri_zone(None) is None and E.gri_zone(None) is None


def test_custom_goals_are_not_judged_without_data():
    start = datetime(2026, 6, 1, tzinfo=LA)
    g = R.minute_grid([], 300, int(start.timestamp()), int((start + timedelta(days=14)).timestamp()), LA)
    out = R.custom_appendix(g, start, 14, {"ranges": {}, "goals": {"time_in_range": {"op": ">", "value": 70}}})
    assert out["comparable"] is False
    assert all(r["met"] is None for r in out["rows"]) and out["stats"] == {"active": 0.0, "enough_data": False}


def test_panel_thinning_keeps_the_newest_when_the_last_day_alone_is_too_big(tmp_path):
    from glucocraft.storage import GlucoCraftStorage

    rows = [GlucoseReading(T0 + 300 * i, 100.0 + i % 50) for i in range(400)]
    storage = GlucoCraftStorage(str(tmp_path), "t")  # a real store: the snapshot slices it by bisection
    storage.load()
    storage._readings = list(rows)
    c = types.SimpleNamespace(storage=storage, detected_interval_seconds=300)
    copied, _intervals, _any = PF._snapshot(c, 0)
    assert PF._readings(copied, 0, 1) == [(rows[-1].timestamp, int(rows[-1].raw))]
    assert len(PF._readings(copied, 0, None)) == 400
