# License: AGPL-3.0-only (LICENSE).
"""Write a series into a Home Assistant recorder database as one entity's recorded state history.

For a TEST Home Assistant only. It uses Home Assistant's own recorder models (homeassistant.components.recorder.
db_schema), so the rows match the schema of the Home Assistant version it runs with; run it with the Python of that
Home Assistant, in a container of its image, while Home Assistant core is stopped (the recorder must not write at the
same time). GlucoCraft's history import then reads the entity's history as it reads a real source's.

On a Home Assistant OS test machine (the host console, or the SSH add-on with protection mode off), with the
tools/synth folder copied to /share/synth and the series to /share/series.json. `ha core stop` stops the
homeassistant container, so the write runs in a new container of the same image:

  IMAGE=$(docker inspect --format '{{.Config.Image}}' homeassistant)   # the running Home Assistant's image
  ha core stop
  docker run --rm -v /mnt/data/supervisor/homeassistant:/config -v /mnt/data/supervisor/share:/share -w /share \
      --entrypoint python3 "$IMAGE" -m synth.recorder_write --series /share/series.json \
      --entity sensor.synthetic_glucose --db-url sqlite:////config/home-assistant_v2.db --confirm-core-stopped
  ha core start

Rows follow Home Assistant's rules: last_updated is the recording time; last_changed changes only when the state text
changes (a new reading with the same value changes only its measurement-time attribute); a state equal to the previous
one in text and attributes is not written (Home Assistant records no row for it). The measurement time is written as
the attribute measurement_timestamp (ISO 8601, local time), which GlucoCraft reads as the CGM's measurement time.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

from .common import Series

TIME_ATTRIBUTE = "measurement_timestamp"
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def state_rows(
    series: Series,
    entity_id: str,
    friendly_name: str = "Synthetic glucose",
    time_attribute: str = TIME_ATTRIBUTE,
    extra_attributes: dict | None = None,
) -> list[dict]:
    """Return the rows Home Assistant would record for the series' states, in recording order.

    Each row: {"state", "attributes", "last_updated_ts", "last_changed_ts", "last_reported_ts"} (epoch seconds).
    time_attribute "" writes no measurement time (an input_number keeps no such attribute).
    """
    tz = ZoneInfo(series.tz)
    base = {
        "state_class": "measurement",
        "unit_of_measurement": series.unit,
        "device_class": "blood_glucose_concentration",
        "friendly_name": friendly_name,
        **(extra_attributes or {}),
    }
    rows: list[dict] = []
    last_changed = None
    for s in sorted(series.states, key=lambda x: x.recorded):
        attrs = dict(base)
        if time_attribute and s.measured is not None:
            attrs[time_attribute] = datetime.fromtimestamp(s.measured, tz).isoformat()
        if rows and rows[-1]["state"] == s.state and rows[-1]["attributes"] == attrs:
            rows[-1]["last_reported_ts"] = float(s.recorded)  # Home Assistant only updates last_reported
            continue
        if not rows or rows[-1]["state"] != s.state:
            last_changed = float(s.recorded)
        rows.append(
            {
                "entity_id": entity_id,
                "state": s.state,
                "attributes": attrs,
                "last_updated_ts": float(s.recorded),
                "last_changed_ts": last_changed,
                "last_reported_ts": float(s.recorded),
            }
        )
    return rows


def write(db_url: str, entity_id: str, rows: list[dict], replace: bool = False) -> int:
    """Insert the rows into the recorder database at db_url with Home Assistant's models; return the rows written.

    replace=True first deletes the entity's existing states from the series' first time on (in its span and after
    it), so that the series becomes the entity's newest history: a source that recorded states up to the moment core
    was stopped takes a series that ends a little earlier. A database without the recorder's tables is created with
    the schema of the installed Home Assistant. Refused (nothing written): an existing database with another schema
    version or without the recorder's tables, an entity that already has states in the span or after it (unless
    replace), rows not in increasing recording time, and a malformed entity id.
    """
    from homeassistant.components.recorder import db_schema as dbs
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import Session

    try:
        from homeassistant.util.ulid import ulid_at_time, ulid_to_bytes
    except ImportError:  # an older Home Assistant: random context ids
        ulid_at_time = ulid_to_bytes = None

    if not ENTITY_ID.match(entity_id or ""):
        raise ValueError(f"not an entity id: {entity_id!r} (domain.object_id, lower case)")
    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    if dbs.TABLE_STATES not in tables:
        dbs.Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(dbs.SchemaChanges(schema_version=dbs.SCHEMA_VERSION, changed=datetime.now(UTC)))
            session.commit()
    else:
        # an existing database must have this Home Assistant's schema: rows written with other models could be
        # wrong or lost when Home Assistant migrates it
        needed = {dbs.TABLE_STATES, dbs.TABLE_STATES_META, dbs.TABLE_STATE_ATTRIBUTES, dbs.TABLE_SCHEMA_CHANGES}
        if missing := sorted(needed - tables):
            raise SystemExit(f"the recorder database lacks the tables {', '.join(missing)}; not written")
        with Session(engine) as session:
            version = (
                session.query(dbs.SchemaChanges.schema_version)
                .order_by(dbs.SchemaChanges.change_id.desc())
                .limit(1)
                .scalar()
            )
        if version != dbs.SCHEMA_VERSION:
            raise SystemExit(
                f"the recorder database has schema version {version}, this Home Assistant writes "
                f"{dbs.SCHEMA_VERSION}: start Home Assistant once to migrate it, or use the Python of "
                "the Home Assistant that owns it; not written"
            )
    if not rows:
        return 0
    if any(b["last_updated_ts"] <= a["last_updated_ts"] for a, b in pairwise(rows)):
        raise ValueError("the rows must be in strictly increasing recording time (state_rows)")
    with Session(engine) as session:
        meta = session.query(dbs.StatesMeta).filter(dbs.StatesMeta.entity_id == entity_id).one_or_none()
        if meta is None:
            meta = dbs.StatesMeta(entity_id=entity_id)
            session.add(meta)
            session.flush()
        first, last = rows[0]["last_updated_ts"], rows[-1]["last_updated_ts"]
        in_span = session.query(dbs.States).filter(
            dbs.States.metadata_id == meta.metadata_id,
            dbs.States.last_updated_ts >= first,
            dbs.States.last_updated_ts <= last,
        )
        if replace:
            session.query(dbs.States).filter(
                dbs.States.metadata_id == meta.metadata_id, dbs.States.last_updated_ts >= first
            ).delete(synchronize_session=False)
        elif in_span.limit(1).count():
            raise SystemExit(
                f"{entity_id} already has recorded states between the series' first and last time: "
                "writing again would record every state twice; pass --replace to replace them"
            )
        if (
            session.query(dbs.States)
            .filter(dbs.States.metadata_id == meta.metadata_id, dbs.States.last_updated_ts > last)
            .limit(1)
            .count()
        ):
            raise SystemExit(
                f"{entity_id} has recorded states after the series' last time: the series would not "
                "be the entity's newest history (pass --replace to replace them, or write a series that ends later)"
            )
        prev = (
            session.query(dbs.States)
            .filter(dbs.States.metadata_id == meta.metadata_id, dbs.States.last_updated_ts < first)
            .order_by(dbs.States.last_updated_ts.desc())
            .first()
        )
        attr_cache: dict[str, object] = {}
        for i, r in enumerate(rows):
            shared = json.dumps(r["attributes"], separators=(",", ":"), ensure_ascii=False)
            attr = attr_cache.get(shared)
            if attr is None:
                h = dbs.StateAttributes.hash_shared_attrs_bytes(shared.encode())
                attr = (
                    session.query(dbs.StateAttributes)
                    .filter(dbs.StateAttributes.hash == h, dbs.StateAttributes.shared_attrs == shared)
                    .first()
                ) or dbs.StateAttributes(hash=h, shared_attrs=shared)
                attr_cache[shared] = attr
            upd = r["last_updated_ts"]
            ctx = ulid_to_bytes(ulid_at_time(upd)) if ulid_at_time else os.urandom(16)
            st = dbs.States(
                state=r["state"],
                last_updated_ts=upd,
                origin_idx=0,
                context_id_bin=ctx,
                states_meta_rel=meta,
                state_attributes=attr,
                old_state=prev,
            )
            # Home Assistant leaves last_changed_ts / last_reported_ts empty when they equal last_updated_ts
            st.last_changed_ts = None if r["last_changed_ts"] == upd else r["last_changed_ts"]
            if hasattr(dbs.States, "last_reported_ts"):
                st.last_reported_ts = None if r["last_reported_ts"] == upd else r["last_reported_ts"]
            session.add(st)
            prev = st
            if i % 5000 == 4999:
                session.flush()
        if hasattr(dbs, "RecorderRuns"):
            session.add(
                dbs.RecorderRuns(
                    start=datetime.fromtimestamp(first, UTC),
                    end=datetime.fromtimestamp(last, UTC),
                    closed_incorrect=False,
                    created=datetime.now(UTC),
                )
            )
        session.commit()
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    """Write a series into a recorder database (command line); return the exit code."""
    ap = argparse.ArgumentParser(prog="python -m synth.recorder_write", description=__doc__.split("\n\n")[0])
    ap.add_argument("--series", required=True, help="a series JSON (generate.py --out)")
    ap.add_argument("--entity", required=True, help="the entity whose history is written (sensor.synthetic_glucose)")
    ap.add_argument("--db-url", default="sqlite:////config/home-assistant_v2.db", help="SQLAlchemy URL of the recorder")
    ap.add_argument("--friendly-name", default="Synthetic glucose")
    ap.add_argument("--time-attribute", default=TIME_ATTRIBUTE, help='"" writes no measurement time')
    ap.add_argument(
        "--replace", action="store_true", help="first delete the entity's states from the series' first time on"
    )
    ap.add_argument("--dry-run", action="store_true", help="only count the rows")
    ap.add_argument(
        "--confirm-core-stopped",
        action="store_true",
        help="required: Home Assistant core is stopped (ha core stop) and this is a test instance",
    )
    args = ap.parse_args(argv)
    series = Series.load(args.series)
    rows = state_rows(series, args.entity, args.friendly_name, args.time_attribute)
    if args.dry_run:
        print(f"{len(rows)} rows for {args.entity} ({len(series.states)} states)")
        return 0
    if not args.confirm_core_stopped:
        ap.error("stop Home Assistant core first (ha core stop), then pass --confirm-core-stopped")
    t = time.time()
    n = write(args.db_url, args.entity, rows, args.replace)
    print(f"wrote {n} rows for {args.entity} in {time.time() - t:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
