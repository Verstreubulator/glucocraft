# License: AGPL-3.0-only (LICENSE).
"""The optional third data source: a real recorded history (tools/synth/real_history.py).

The tool itself is tested in every run, against a local HTTP server: GET requests only, one day per request, the
history written as a series marked as real ("artificial": false), never inside the repository, no value printed, and
only the keys "token" (and "url" when no --url is given) read from the connection file.

The real history is personal health information and never part of the default run: the tests that read it run only
when the environment variable GLUCOCRAFT_REAL_HISTORY names the file (create it with
``python -m tools.synth.real_history ... --out FILE``, outside the repository), and are skipped with the reason
otherwise. They put the whole history through GlucoCraft's real import, statistics and CGM Report and compare every
value with the independent calculator (as tests/test_synth_scenarios.py does), apart from the values of the two known
findings that real measurement times off the whole minute show (tests/test_synth_sub_minute_times.py); on a failure
only the names of the values that differ are shown, never a value. tests/test_history_lengths.py cuts it to each
recorder length.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import urllib.parse
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import test_synth_scenarios as T
from test_synth_sub_minute_times import known_finding

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
RH = importlib.import_module("tools.synth.real_history")
S = importlib.import_module("tools.synth.scenarios")
common = importlib.import_module("tools.synth.common")

TOKEN = "test-token-not-real"
T0 = datetime(2026, 6, 1, tzinfo=UTC)


def fake_row(minute: int, value: str) -> dict:
    t = T0 + timedelta(minutes=minute)
    return {
        "state": value,
        "last_updated": (t + timedelta(seconds=30)).isoformat(),
        "attributes": {"unit_of_measurement": "mg/dL", "measurement_timestamp": t.isoformat()},
    }


@pytest.fixture
def history_server():
    """A local server answering GET /api/history/period with one reading per hour; records every request."""
    seen: list[tuple[str, str, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(("GET", self.path, dict(self.headers)))
            parsed = urllib.parse.urlparse(self.path)
            start = datetime.fromisoformat(urllib.parse.unquote(parsed.path.rsplit("/", 1)[1]))
            end = datetime.fromisoformat(urllib.parse.parse_qs(parsed.query)["end_time"][0])
            rows = [
                fake_row(m, "1234")
                for m in range(0, 60 * 24 * 4, 60)
                if start <= T0 + timedelta(minutes=m, seconds=30) < end
            ]
            body = json.dumps([rows]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            seen.append(("POST", self.path, dict(self.headers)))
            self.send_response(405)
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", seen
    httpd.shutdown()


def test_tool_reads_with_get_one_day_per_request_and_prints_no_value(history_server, tmp_path, capsys, monkeypatch):
    base, seen = history_server
    conn = tmp_path / "ha.json"
    conn.write_text(json.dumps({"token": TOKEN, "note": "other secrets: never read", "url": "http://wrong.invalid"}))
    out = tmp_path / "real.json"
    monkeypatch.setattr(
        RH, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: T0 + timedelta(days=4))})
    )
    assert (
        RH.main(
            [
                "--connection",
                str(conn),
                "--url",
                base,
                "--entity",
                "sensor.cgm",
                "--days",
                "4",
                "--pause",
                "0",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert seen and all(method == "GET" for method, _, _ in seen) and len(seen) == 4  # one request per day
    assert all(h["Authorization"] == f"Bearer {TOKEN}" for _, _, h in seen)
    printed = capsys.readouterr()
    assert "1234" not in printed.out + printed.err and TOKEN not in printed.out + printed.err
    data = json.loads(out.read_text())
    assert data["artificial"] is False and len(data["states"]) == 96 and data["unit"] == "mg/dL"
    s = common.Series.load(out)
    assert s.artificial is False and s.states[0].measured == int(T0.timestamp())
    assert s.states[0].recorded == int(T0.timestamp()) + 30


def test_connection_file_keys_read(monkeypatch, tmp_path):
    """Only "token", and "url" only when asked: a connection file may hold other secrets."""
    read: list[str] = []

    class Watch(dict):
        def get(self, key, default=None):
            read.append(key)
            return super().get(key, default)

        def __getitem__(self, key):
            read.append(key)
            return super().__getitem__(key)

    conn = tmp_path / "ha.json"
    conn.write_text("{}")
    monkeypatch.setattr(RH.json, "loads", lambda text: Watch(token=TOKEN, url="http://x", note="secret"))
    assert RH.read_connection(conn, with_url=False) == ("", TOKEN)
    assert read == ["token"]
    assert RH.read_connection(conn, with_url=True) == ("http://x", TOKEN)
    assert "note" not in read


def test_real_history_is_never_written_inside_the_repository(tmp_path):
    s = RH.to_series([fake_row(0, "120"), fake_row(5, "LO")], "sensor.cgm")
    assert s.artificial is False and [x.state for x in s.states] == ["120", "LO"]
    with pytest.raises(SystemExit):
        s.save(REPO / "tools" / "synth" / "real.json")
    assert not (REPO / "tools" / "synth" / "real.json").exists()
    with pytest.raises(SystemExit):
        RH.main(["--connection", "x.json", "--entity", "sensor.cgm", "--out", str(REPO / "real.json")])
    s.save(tmp_path / "real.json")
    assert common.Series.load(tmp_path / "real.json").artificial is False


def test_no_real_history_in_the_repository():
    """No series marked as real anywhere in the repository."""
    for path in REPO.rglob("*.json"):
        if ".git" in path.parts or "node_modules" in path.parts:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        assert not (isinstance(data, dict) and "states" in data and data.get("artificial") is False), path


# ------------------------------------------------------------------------------- the real history (optional)
REAL = os.environ.get("GLUCOCRAFT_REAL_HISTORY", "")
REAL_SKIP = (
    (
        "the real-data tests are optional: set GLUCOCRAFT_REAL_HISTORY to a history file written by "
        "python -m tools.synth.real_history (outside the repository)"
    )
    if not REAL
    else (f"GLUCOCRAFT_REAL_HISTORY names a file that does not exist: {REAL}" if not Path(REAL).is_file() else "")
)


@pytest.mark.skipif(bool(REAL_SKIP), reason=REAL_SKIP or "available")
@pytest.mark.parametrize("report_days", [14, 180])
def test_real_history_through_glucocraft_agrees_with_the_calculator(report_days, tmp_path):
    series = common.Series.load(REAL)
    assert series.artificial is False, "GLUCOCRAFT_REAL_HISTORY must name a real history (real_history.py)"
    assert not common.inside_repository(REAL), "a real history must be kept outside the repository"
    sc = S.from_series(series, "real", report_days=report_days)
    gc, ex = T.GlucoCraftRun(sc, tmp_path), T.ExpectedRun(sc)
    assert gc.readings, "the real history holds no reading GlucoCraft imports"
    keys = []
    for du in T.DISPLAY_UNITS:
        rows = T.compare_store(gc, ex) + T.compare_statistics(gc, ex, du) + T.compare_report(gc, ex, du)
        keys += [f"{k} [{du} display]" for k, a, b in rows if a != b and not known_finding(k)]
    assert not keys, f"{len(keys)} values differ (values not shown): " + ", ".join(keys)
