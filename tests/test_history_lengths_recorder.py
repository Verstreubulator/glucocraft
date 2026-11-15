# License: AGPL-3.0-only (LICENSE).
"""The history lengths of tests/test_history_lengths.py through a real Home Assistant recorder.

Each history (no history, 1 day, 10 days, 30 days, six months, more than six months; whole days, with gaps, a
partial first day; mg/dL and mmol/L) is written into one SQLite recorder database with tools/synth/recorder_write.py
(Home Assistant's own recorder models), one entity per history. Home Assistant's recorder then starts on that
database, and GlucoCraft's history import reads each entity exactly as it does at its setup: history_import.
async_rows_kept with the recorder's get_significant_states (significant_changes_only=False, as _get_history) and
rows_from_states with the coordinator's measurement-time parser. The rows must equal those of the in-memory path of
tests/test_history_lengths.py, whose statistics and report are compared there with the independent calculator.

Needs the homeassistant package: the Python named by the environment variable GLUCOCRAFT_HA_PYTHON (a virtual
environment with Home Assistant installed), or this Python if it has it. Skipped with the reason otherwise. Slow:
about a million rows are written and read.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TZ = "America/Los_Angeles"


def _entity(length: str, variant: str, unit: str) -> str:
    return f"sensor.h_{length}_{variant}_{unit.replace('/', '').lower()}"


def _ha_python() -> str | None:
    """A Python with the homeassistant package, or None."""
    for exe in (os.environ.get("GLUCOCRAFT_HA_PYTHON"), sys.executable):
        if (
            exe
            and subprocess.run(
                [exe, "-c", "import homeassistant.components.recorder.db_schema"], capture_output=True, check=False
            ).returncode
            == 0
        ):
            return exe
    return None


# ----------------------------------------------------------------------- the part run with Home Assistant's Python
async def _recorder_rows(workdir: Path) -> dict[str, list]:
    """Write every history, start the recorder on the database, read each back through GlucoCraft's import path."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from glucocraft import history_import as hi
    from glucocraft.coordinator import _parse_reading_time
    from homeassistant import config_entries, loader
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import get_significant_states
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import recorder as recorder_helper
    from homeassistant.setup import async_setup_component

    from tools.synth import common, recorder_write, scenarios

    db_url = f"sqlite:///{(workdir / 'home-assistant_v2.db').as_posix()}"
    cases = [(n, v, u) for n, v in scenarios.histories() for u in common.UNITS]
    built = {}
    for length, variant, unit in cases:
        sc = scenarios.history(length, variant, unit)
        built[(length, variant, unit)] = sc
        series = common.Series(unit=sc.unit, interval=sc.interval, tz=sc.tz, states=sc.states)
        rows = recorder_write.state_rows(series, _entity(length, variant, unit))
        recorder_write.write(db_url, _entity(length, variant, unit), rows)

    (workdir / "config").mkdir(exist_ok=True)
    hass = HomeAssistant(str(workdir / "config"))
    loader.async_setup(hass)
    recorder_helper.async_initialize_recorder(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    await hass.config.async_set_time_zone(TZ)
    hass.config.skip_pip = True
    assert await async_setup_component(
        hass, "recorder", {"recorder": {"db_url": db_url, "commit_interval": 0, "auto_purge": False}}
    )
    await hass.async_start()
    await get_instance(hass).async_recorder_ready.wait()
    out = {}
    try:
        for (length, variant, unit), sc in built.items():
            entity = _entity(length, variant, unit)

            async def fetch(start: int, end: int, entity=entity):
                def utc(ts):
                    """An epoch time as a UTC datetime."""
                    return datetime.fromtimestamp(ts, UTC)

                history = await get_instance(hass).async_add_executor_job(
                    lambda: get_significant_states(
                        hass,
                        utc(start),
                        utc(end),
                        [entity],
                        include_start_time_state=False,
                        significant_changes_only=False,
                        minimal_response=False,
                        no_attributes=False,
                    )
                )
                return history.get(entity, [])

            def to_rows(states, newest, sc=sc):
                return hi.rows_from_states(
                    states,
                    sc.unit == common.UNIT_MMOLL,
                    True,
                    sc.limits,
                    parse_time=_parse_reading_time,
                    now=sc.now,
                    after=newest,
                )

            rows = await hi.async_rows_kept(fetch, to_rows, sc.now, ZoneInfo(sc.tz), 200)
            out[entity] = [list(r) for r in rows]
    finally:
        await hass.async_stop()
    return out


def _main(workdir: str) -> None:
    sys.path[:0] = [str(REPO), str(REPO / "custom_components")]
    print(json.dumps(asyncio.run(_recorder_rows(Path(workdir)))))


if __name__ == "__main__":  # run by the test below with Home Assistant's Python, which needs no pytest
    _main(sys.argv[1])
    raise SystemExit(0)


# ------------------------------------------------------------------------------------------------- the test
import pytest

HA_PYTHON = _ha_python()


@pytest.mark.slow
@pytest.mark.skipif(
    HA_PYTHON is None,
    reason="needs the homeassistant package: set GLUCOCRAFT_HA_PYTHON to the Python "
    "of a virtual environment with Home Assistant installed (docs/DEVELOPER_GUIDE.md)",
)
def test_every_history_length_reads_back_through_a_real_recorder(tmp_path):
    from glucocraft import history_import as hi

    import test_history_lengths as H
    import test_synth_scenarios as T

    run = subprocess.run(
        [HA_PYTHON, str(Path(__file__).resolve()), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=3600,
    )
    assert run.returncode == 0, run.stderr[-3000:]
    real = json.loads(run.stdout.strip().splitlines()[-1])
    for length, variant, unit in H.CASES:
        sc = H.S.history(length, variant, unit)

        async def fetch(start, end, sc=sc):
            return [T._state(s, sc.unit) for s in sc.states if start <= s.recorded <= end]

        def to_rows(states, newest, sc=sc):
            return hi.rows_from_states(states, sc.unit == "mmol/L", True, sc.limits, now=sc.now, after=newest)

        from zoneinfo import ZoneInfo

        want = [list(r) for r in asyncio.run(hi.async_rows_kept(fetch, to_rows, sc.now, ZoneInfo(sc.tz), 200))]
        got = real[_entity(length, variant, unit)]
        assert got == want, (
            f"{H.case_id(length, variant, unit)}: {len(got)} rows from the recorder, {len(want)} expected"
        )
