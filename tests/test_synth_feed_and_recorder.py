# License: AGPL-3.0-only (LICENSE).
"""The test-instance tools: the live feed over REST (tools/synth/live_feed.py) and the recorder writer
(tools/synth/recorder_write.py).

The live feed is run against a local HTTP server that records the requests. The recorder writer's rows are checked
for Home Assistant's recording rules; writing into a SQLite recorder database needs the homeassistant package (its
own recorder models), which the test environment normally does not have: that test is then skipped with the reason.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
common = importlib.import_module("tools.synth.common")
feed = importlib.import_module("tools.synth.live_feed")
rec = importlib.import_module("tools.synth.recorder_write")
G = importlib.import_module("tools.synth.generate")

TOKEN = "test-token-not-real"


def series(states: list[tuple[int, str, int | None]], unit: str = "mg/dL") -> common.Series:
    return common.Series(
        unit=unit,
        interval=60,
        tz="America/Los_Angeles",
        states=[common.SourceState(recorded=r, state=s, measured=m) for r, s, m in states],
    )


# --------------------------------------------------------------------------------------------- the live feed
@pytest.fixture
def server():
    """A local HTTP server that records every request: (path, headers, JSON body)."""
    seen: list[tuple[str, dict, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", seen
    httpd.shutdown()


def test_input_number_feed(server, tmp_path):
    base, seen = server
    conn = tmp_path / "ha.json"
    conn.write_text(json.dumps({"url": base, "token": TOKEN}))
    s = series([(100, "120", 70), (160, "unavailable", None), (220, "LO", 190), (280, "HI", 250), (340, "6,7", 310)])
    items = feed.payloads(s, "input_number.synthetic_glucose")
    lines: list[str] = []
    sleeps: list[float] = []
    url, token = feed.load_connection(conn)
    assert feed.run(url, token, items, 60, s.tz, sleep=sleeps.append, log=lines.append) == 4
    # LO / HI as the numbers GlucoCraft reads as LO / HI for every source (401 would be HI only for Dexcom)
    assert [b["value"] for _, _, b in seen] == [120.0, 39, 501, 6.7]
    assert all(p == "/api/services/input_number/set_value" for p, _, _ in seen)
    assert all(h["Authorization"] == f"Bearer {TOKEN}" for _, h, _ in seen)
    assert sleeps == [60, 60, 60]
    assert not any(TOKEN in line for line in lines)


def test_state_api_feed_sets_text_and_measurement_time(server):
    base, seen = server
    s = series([(130, "120", 100), (190, "LO", 160), (250, "unavailable", None)], unit="mmol/L")
    items = feed.payloads(s, "sensor.synthetic_glucose", state_api=True)
    feed.run(base, TOKEN, items, 0, s.tz, sleep=lambda x: None, clock=lambda: 1_800_000_000, log=lambda x: None)
    assert [b["state"] for _, _, b in seen] == ["120", "LO", "unavailable"]
    first = seen[0][2]["attributes"]
    assert first["unit_of_measurement"] == "mmol/L"
    # measured 30 s before it was recorded: sent as 30 s before now, in local time
    assert first["measurement_timestamp"] == datetime.fromtimestamp(1_800_000_000 - 30, ZoneInfo(s.tz)).isoformat()
    assert "measurement_timestamp" not in seen[2][2]["attributes"]


def test_feed_refuses_a_connection_file_without_token(tmp_path):
    bad = tmp_path / "ha.json"
    bad.write_text(json.dumps({"url": "http://127.0.0.1:1"}))
    with pytest.raises(SystemExit):
        feed.load_connection(bad)


# ------------------------------------------------------------------------------------------- the recorder rows
def test_rows_follow_home_assistants_recording_rules():
    s = series([(100, "120", 70), (160, "120", 130), (220, "125", 190), (280, "unavailable", None)])
    rows = rec.state_rows(s, "sensor.synthetic_glucose")
    assert [r["state"] for r in rows] == ["120", "120", "125", "unavailable"]
    # the same value with a new measurement time is a new row, but last_changed stays at the first
    assert rows[1]["last_changed_ts"] == 100 and rows[1]["last_updated_ts"] == 160
    assert rows[2]["last_changed_ts"] == 220
    assert rows[0]["attributes"]["measurement_timestamp"] == datetime.fromtimestamp(70, ZoneInfo(s.tz)).isoformat()
    assert "measurement_timestamp" not in rows[3]["attributes"]


def test_rows_without_a_time_attribute_drop_repeated_states():
    """Without a measurement-time attribute (an input_number), a repeated value records no row (only
    last_reported moves), as in Home Assistant."""
    s = series([(100, "120", 70), (160, "120", 130), (220, "125", 190)])
    rows = rec.state_rows(s, "input_number.synthetic_glucose", time_attribute="")
    assert [r["state"] for r in rows] == ["120", "125"]
    assert rows[0]["last_reported_ts"] == 160


def _has_homeassistant() -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import homeassistant.components.recorder.db_schema"], capture_output=True, check=False
    )
    return probe.returncode == 0


WRITE_SCRIPT = r"""
import sys
sys.path.insert(0, sys.argv[1])
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, text
from tools.synth import generate, recorder_write
s = generate.generate(None, start=datetime(2026, 3, 1, tzinfo=ZoneInfo("America/Los_Angeles")), hours=6, seed=1)
rows = recorder_write.state_rows(s, "sensor.synthetic_glucose")
url = "sqlite:///" + sys.argv[2]
assert recorder_write.write(url, "sensor.synthetic_glucose", rows) == len(rows)
with create_engine(url).connect() as c:
    n = c.execute(text("select count(*) from states join states_meta using (metadata_id) "
                       "where states_meta.entity_id = 'sensor.synthetic_glucose'")).scalar()
print(n, len(rows))
"""


@pytest.mark.skipif(
    not _has_homeassistant(),
    reason="the homeassistant package is not installed in this Python: "
    "recorder_write.write is untested here (run it on a test instance; docs/DEVELOPER_GUIDE.md)",
)
def test_write_into_a_sqlite_recorder_database(tmp_path):
    out = subprocess.run(
        [sys.executable, "-c", WRITE_SCRIPT, str(REPO), str(tmp_path / "home-assistant_v2.db")],
        capture_output=True,
        text=True,
        check=True,
    )
    n, expected = out.stdout.split()
    assert n == expected


def test_generated_series_writes_rows_for_every_reading():
    s = G.generate(None, start=datetime(2026, 3, 1, tzinfo=ZoneInfo("America/Los_Angeles")), hours=6, seed=1)
    rows = rec.state_rows(s, "sensor.synthetic_glucose")
    readings = [x for x in s.states if x.measured is not None]
    assert len([r for r in rows if "measurement_timestamp" in r["attributes"]]) == len(readings)
