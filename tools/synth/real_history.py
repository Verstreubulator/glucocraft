# License: AGPL-3.0-only (LICENSE).
"""Read a glucose entity's real recorded history from Home Assistant into a LOCAL file, for optional tests.

The third data source of the tests, next to the generator and the sine waves: months of a real CGM history. It is
personal health information:
  * it is written only outside the repository (refused inside it), as a series JSON (common.Series, "artificial":
    false) with the states exactly as recorded (state text, recording time, measurement time);
  * no value is ever printed: the tool reports counts and the time span only;
  * the default test run never reads it: the tests that use it (tests/test_synth_real_history.py,
    tests/test_history_lengths.py) run only when the environment variable GLUCOCRAFT_REAL_HISTORY names the file, and
    are skipped with the reason otherwise.

Home Assistant is only read: GET /api/history/period, one day (24 hours) per request, one request at a time, a pause
between requests (--pause), so that reading months stays gentle on a Home Assistant in use. The token comes from a
JSON file: only its "token" key is read, and its "url" key only when --url is not given (a connection file may hold
other secrets in other keys); the token is never printed.

Usage (from the repository root; OUT outside the repository):
  python -m tools.synth.real_history --connection CONNECTION.json --url http://homeassistant.local \\
      --entity sensor.cgm_glucose --days 200 --out OUT.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .common import DEFAULT_TZ, Series, SourceState, inside_repository, unit_name
from .profile import TIME_ATTRIBUTES, _epoch, fetch_history


def read_connection(path: str | Path, with_url: bool) -> tuple[str, str]:
    """Return (url, token) from a connection file, reading only the key "token" and, when with_url, the key "url"
    (other keys are never read or printed). url is "" when it is not read or the file has none."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise SystemExit(f"{path}: the file needs the key 'token'")
    url = data.get("url") if with_url else None
    return (url.rstrip("/") if isinstance(url, str) else ""), token


def to_series(rows: list[dict], entity_id: str, tz: str = DEFAULT_TZ, time_attribute: str = "") -> Series:
    """The recorded rows of GET /api/history/period as a series, in recording order.

    recorded: last_updated; measured: the measurement-time attribute (time_attribute, else the first known name in
    profile.TIME_ATTRIBUTES), None when the state has none; state: the state text unchanged. The unit is the most
    frequent unit_of_measurement of the rows (mg/dL when none has one).
    """
    states, units = [], Counter()
    names = [time_attribute] if time_attribute else list(TIME_ATTRIBUTES)
    for r in rows:
        recorded = _epoch(r.get("last_updated"))
        if recorded is None:
            continue
        attrs = r.get("attributes") or {}
        if attrs.get("unit_of_measurement"):
            units[str(attrs["unit_of_measurement"])] += 1
        measured = next((t for t in (_epoch(attrs.get(a)) for a in names) if t is not None), None)
        states.append(SourceState(recorded=recorded, state=str(r.get("state", "")), measured=measured))
    states.sort(key=lambda s: s.recorded)
    unit = unit_name(units.most_common(1)[0][0]) if units else "mg/dL"
    return Series(
        unit=unit,
        interval=60,
        tz=tz,
        states=states,
        artificial=False,
        description="A real recorded glucose history: personal health information. Keep it outside the "
        "repository and never share it.",
        meta={"entity_id": entity_id, "read_at": datetime.now(UTC).strftime("%Y-%m-%d")},
    )


def main(argv: list[str] | None = None) -> int:
    """Read a real history into a local file (command line); return the exit code."""
    ap = argparse.ArgumentParser(prog="python -m tools.synth.real_history", description=__doc__.split("\n\n")[0])
    ap.add_argument("--connection", required=True, help='JSON file with the key "token" (and "url")')
    ap.add_argument("--url", help="the Home Assistant base URL (default: the file's url)")
    ap.add_argument("--entity", required=True, help="the glucose entity, for example sensor.cgm_glucose")
    ap.add_argument("--days", type=float, default=200, help="how far back to read (default 200)")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between requests (default 1)")
    ap.add_argument("--tz", default=DEFAULT_TZ, help=f"local time zone of the history (default {DEFAULT_TZ})")
    ap.add_argument("--time-attribute", default="", help="the measurement-time attribute (default: the known names)")
    ap.add_argument("--out", required=True, help="the series JSON to write, outside the repository")
    args = ap.parse_args(argv)
    out = Path(args.out).expanduser()
    if inside_repository(out):
        ap.error(f"--out must be outside the repository: {out}")
    file_url, token = read_connection(args.connection, with_url=not args.url)
    base = (args.url or file_url).rstrip("/")
    if not base:
        ap.error("no Home Assistant URL: pass --url or put the key 'url' in the connection file")
    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)

    def progress(t: datetime, n: int) -> None:
        print(f"\rread up to {t:%Y-%m-%d}: {n} states", end="", file=sys.stderr, flush=True)

    rows = fetch_history(base, token, args.entity, start, end, chunk_days=1, progress=progress, pause=args.pause)
    print(file=sys.stderr)
    series = to_series(rows, args.entity, args.tz, args.time_attribute)
    out.parent.mkdir(parents=True, exist_ok=True)
    series.save(out)
    span = (series.states[-1].recorded - series.states[0].recorded) / 86400 if series.states else 0.0
    print(f"{len(series.states)} states over {span:.1f} days ({series.unit}) written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
