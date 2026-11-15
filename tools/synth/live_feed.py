# License: AGPL-3.0-only (LICENSE).
"""Push a series to a TEST Home Assistant entity over REST, one value every N seconds.

Two ways to write the value:
  input_number (default): POST /api/services/input_number/set_value {"entity_id", "value"}. An input_number holds
    numbers only, so LO / HI are written as numbers beyond the sensor limits (--lo-value / --hi-value; default 39 /
    501 mg/dL, 2.2 / 27.9 mmol/L: GlucoCraft reads them as LO / HI for every source, while 401 is HI only for a
    Dexcom-type source) and no-reading states are skipped; the entity has no measurement-time attribute, so
    GlucoCraft uses the time of the update. A dropped connection or a timeout is retried 3 times, then the feed
    stops with the reason; 401 / 403 / 404 stop it at once.
  --state-api: POST /api/states/<entity_id> with the state text and the attributes unit_of_measurement and
    measurement_timestamp (the measurement time moved to now), so that the entity looks like a CGM sensor, LO / HI
    and "unavailable" included. A state set this way lasts until Home Assistant restarts.

The URL and token come from a JSON file ({"url": ..., "token": ...}); nothing is hard-coded and the token is never
printed. Usage (from the repository root):
  python -m tools.synth.live_feed --connection TEST_CONNECTION.json --series series.json \
      --entity input_number.synthetic_glucose --every 60
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from .common import HI_TEXT, LO_TEXT, UNIT_MMOLL, Series, load_connection

NO_READING = {"", "unavailable", "unknown", "none", "null", "nan", "---", "--", "-"}
_BELOW = re.compile(r"^(below|under|<)")
_ABOVE = re.compile(r"^(above|over|>)")
# The numbers an input_number source gets for LO / HI when none are given. GlucoCraft
# (limits.classify) reads 39 mg/dL or less as LO for every source, and a value above the upper limit as HI; 401 is HI
# only for a Dexcom-type source, so for a test source (input_number / template: automatic upper limit 500) HI must be
# above 500. mmol/L: 2.2 or less is LO; above 27.8 (500 mg/dL as shown in mmol/L) is HI.
CLI_LIMIT_VALUES = {"mg/dL": (39.0, 501.0), UNIT_MMOLL: (2.2, 27.9)}
RETRIES = 3


def _post(url: str, token: str, body: dict, timeout: int = 30, retries: int = RETRIES, sleep=time.sleep) -> int:
    """POST one value. 401 / 403 / 404 stop the feed at once (wrong token, entity or service); a dropped connection
    or a timeout is retried `retries` times (5, 10, 15 s apart), then the feed stops with the reason, so that a
    series never gets a silent hole."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body, allow_nan=False).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                raise SystemExit(f"Home Assistant refused the request ({err.code}); check the token") from err
            if err.code == 404:
                raise SystemExit("Home Assistant answered 404: the entity or the service does not exist") from err
            return err.code
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            if attempt == retries:
                raise SystemExit(
                    f"Home Assistant not reachable after {retries + 1} attempts: {getattr(err, 'reason', err)}"
                ) from err
            sleep(5 * (attempt + 1))
    return 0


def payloads(
    series: Series,
    entity_id: str,
    state_api: bool = False,
    lo_value: float | None = None,
    hi_value: float | None = None,
) -> list[tuple[str, dict]]:
    """The (REST path, JSON body) of each value to send, in recording order; the measurement time is filled in at
    sending time (key "_measured_offset": seconds of the measurement before its recording).

    input_number: LO / LOW / "Below ..." are sent as lo_value, HI / HIGH / "Above ..." as hi_value. None: the values
    GlucoCraft reads as LO / HI for every source (CLI_LIMIT_VALUES: 39 / 501 mg/dL, 2.2 / 27.9 mmol/L; 401 mg/dL would
    be HI only for a Dexcom-type source). No-reading states and texts that are no number are not sent; a value that is
    not finite is never sent."""
    lo_default, hi_default = CLI_LIMIT_VALUES[series.unit]
    lo_value = lo_default if lo_value is None else lo_value
    hi_value = hi_default if hi_value is None else hi_value
    out = []
    for s in sorted(series.states, key=lambda x: x.recorded):
        text = s.state.strip()
        if state_api:
            attrs = {"unit_of_measurement": series.unit}
            if s.measured is not None:
                attrs["_measured_offset"] = s.recorded - s.measured
            out.append((f"/api/states/{entity_id}", {"state": text, "attributes": attrs}))
            continue
        low = text.lower()
        if low in NO_READING:
            continue
        if text.upper() in (LO_TEXT, "LOW") or _BELOW.match(low):
            value = lo_value
        elif text.upper() in (HI_TEXT, "HIGH") or _ABOVE.match(low):
            value = hi_value
        else:
            try:
                value = float(text.replace(",", "."))
            except ValueError:
                continue
            if not math.isfinite(value):
                continue
        out.append(("/api/services/input_number/set_value", {"entity_id": entity_id, "value": value}))
    return out


def run(
    base: str,
    token: str,
    items: list[tuple[str, dict]],
    every: float,
    tz: str,
    start: int = 0,
    count: int | None = None,
    loop: bool = False,
    sleep=time.sleep,
    clock=time.time,
    log=print,
) -> int:
    """Send items[start:] one every `every` seconds; return how many were sent."""
    if not (isinstance(every, (int, float)) and math.isfinite(every) and every >= 0):
        raise ValueError("every must be a finite number of seconds, 0 or more")
    if not 0 <= start <= len(items):
        raise ValueError(f"start must be between 0 and {len(items)}")
    sent, i = 0, start
    zone = ZoneInfo(tz)
    while i < len(items) and (count is None or sent < count):
        path, body = items[i]
        body = json.loads(json.dumps(body))
        attrs = body.get("attributes")
        if attrs is not None and "_measured_offset" in attrs:
            attrs["measurement_timestamp"] = datetime.fromtimestamp(
                clock() - attrs.pop("_measured_offset"), zone
            ).isoformat()
        status = _post(base + path, token, body, sleep=sleep)
        log(f"{i}: {body.get('value', body.get('state'))} -> {status}")
        sent += 1
        i += 1
        if loop and i >= len(items):
            i = 0
        if i < len(items) and (count is None or sent < count):
            sleep(every)
    return sent


def main(argv: list[str] | None = None) -> int:
    """Send a series to a test Home Assistant (command line); return the exit code."""
    ap = argparse.ArgumentParser(prog="python -m tools.synth.live_feed", description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--connection", required=True, help='JSON file with the "url" and "token" of the test Home Assistant'
    )
    ap.add_argument("--series", required=True, help="a series JSON (generate.py --out)")
    ap.add_argument("--entity", required=True, help="input_number.<name>, or any entity with --state-api")
    ap.add_argument("--every", type=float, default=60, help="seconds between values (default 60)")
    ap.add_argument("--start", type=int, default=0, help="index of the first value")
    ap.add_argument("--count", type=int, help="stop after this many values")
    ap.add_argument("--loop", action="store_true", help="start again at the first value after the last")
    ap.add_argument("--state-api", action="store_true", help="POST /api/states (text states and attributes)")
    ap.add_argument(
        "--lo-value", type=float, help="the number written for LO (input_number; default 39 mg/dL or 2.2 mmol/L)"
    )
    ap.add_argument(
        "--hi-value",
        type=float,
        help="the number written for HI (input_number; default 501 mg/dL or "
        "27.9 mmol/L, above every upper limit; 401 is HI only for a Dexcom-type source)",
    )
    args = ap.parse_args(argv)
    if not args.state_api and not args.entity.startswith("input_number."):
        ap.error("without --state-api the entity must be an input_number")
    base, token = load_connection(args.connection)
    series = Series.load(args.series)
    lo_cli, hi_cli = CLI_LIMIT_VALUES[series.unit]
    items = payloads(
        series,
        args.entity,
        args.state_api,
        lo_cli if args.lo_value is None else args.lo_value,
        hi_cli if args.hi_value is None else args.hi_value,
    )
    print(f"{len(items)} values for {args.entity}, one every {args.every:g} s", file=sys.stderr)
    run(base, token, items, args.every, series.tz, args.start, args.count, args.loop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
