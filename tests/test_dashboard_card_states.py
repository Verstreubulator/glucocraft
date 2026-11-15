# License: AGPL-3.0-only (LICENSE).
"""The dashboard cards while their data load, fail or age, the graph band key, and the card picker.

- A graph says that it is loading until the history file is downloaded the first time; without a file it says why
  (no readings stored yet, or the download failed); a graph drawn from an older file after a failed download says when
  that file was downloaded.
- The band key under a graph never shows an inverted span ("181–180") and shows whole mg/dL values also for limits
  stored with a decimal (a limit entered in mmol/L), as the Documentation's Colors table does.
- The Statistics card draws the last reading and the period table separately: the minute that changes the reading's
  age leaves the table in place.
- Every card of the card picker has its own description, and docs/DASHBOARD.md lists the options of every card.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import pytest

from js_i18n import catalogue
from node_runner import needs_node
from test_accessibility_checks import GLU2, JS, UI_STATES, _eval

UI = JS["ui"]
FE = catalogue("frontend")
DASHBOARD_MD = (Path(__file__).resolve().parents[1] / "docs" / "DASHBOARD.md").read_text(encoding="utf-8")
AGP = {"very_low": 54, "low": 70, "tight_low": 70, "tight_high": 140, "high": 180, "very_high": 250}


def _ui(body: str) -> object:
    return _eval("ui", body)


# ------------------------------------------------------------------------------------------------ loading and failure
@needs_node
def test_a_graph_says_loading_why_it_has_no_file_and_when_an_older_file_was_downloaded():
    out = _ui(
        GLU2
        + textwrap.dedent(f"""
        const states = {json.dumps(UI_STATES)}, S = window.__glucocraftFiles;
        let answer = null, release;
        const fetchWithAuth = async () => {{ await new Promise((r) => {{ release = r; }}); return answer; }};
        const graph = async (hours) => {{
          const h = new (customElements.get("glucocraft-history"))(); h.setConfig({{ hours, title: "G" }});
          h._w = 700; h._hass = {{ states, fetchWithAuth }};
          const drawing = h.draw(); await new Promise((r) => setTimeout(r, 0));
          const box = h.shadowRoot.querySelector(".g"), loading = box.innerHTML;
          release(); await drawing;
          return [loading, box.innerHTML];
        }};
        answer = {{ ok: false, status: 500 }};
        const [loading, failed] = await graph(336);
        S.at = {{}}; answer = {{ ok: false, status: 503 }};
        const [, none] = await graph(336);
        S.at = {{}}; answer = {{ ok: true, arrayBuffer: async () => glu2() }};
        const [, fresh] = await graph(336);
        S.at = {{}}; answer = {{ ok: false, status: 500 }};
        const [, older] = await graph(336);
        return {{ loading, failed, none, fresh, older }};
    """)
    )
    assert out["loading"] == '<div class="s">G: loading.</div>'
    assert out["failed"] == '<div class="s">G: the history could not be downloaded. The download is tried again.</div>'
    assert out["none"] == '<div class="s">G: no history available yet.</div>'
    assert "<svg" in out["fresh"] and "Drawn from the history downloaded at" not in out["fresh"]
    # the file of the first download is drawn again, with the time it was downloaded
    assert "<svg" in out["older"] and re.search(r"Drawn from the history downloaded at \d", out["older"])


# ------------------------------------------------------------------------------------------------ the band key
def _band_key(ranges: dict, mmol: bool = False) -> list[str]:
    html = _ui(
        f"GC.mmol = {json.dumps(mmol)};"
        "const h = new (customElements.get('glucocraft-history'))(); h.setConfig({});"
        f"return h.bandKey({json.dumps(ranges)});"
    )
    # the shown text of each item (a span hidden from screen readers where a spoken form follows)
    return re.findall(r'</i>(?:<span aria-hidden="true">)?([^<]+)', html)


@needs_node
@pytest.mark.parametrize(
    ("ranges", "mmol", "key"),
    [
        (AGP, False, ["under 54", "54–69", "70–140", "141–180", "181–250", "over 250"]),
        # the tight range reaches the high limit: no in-range band above it ("181–180")
        ({**AGP, "tight_high": 180}, False, ["under 54", "54–69", "70–180", "181–250", "over 250"]),
        ({**AGP, "tight_high": 180}, True, ["under 3.0", "3.0–3.8", "3.9–10.0", "10.1–13.9", "over 13.9"]),
        # limits entered in mmol/L, stored with one decimal in mg/dL: whole mg/dL values, as the Documentation shows
        (
            {**AGP, "low": 68.5, "tight_high": 160.3, "very_high": 250.1},
            False,
            ["under 54", "54–68", "69–69", "70–160", "161–180", "181–250", "over 250"],
        ),
    ],
)
def test_the_band_key_shows_no_inverted_or_unrounded_span(ranges, mmol, key):
    assert _band_key(ranges, mmol) == key


# ------------------------------------------------------------------------------------------------ the Statistics card
@needs_node
def test_the_statistics_card_leaves_its_table_in_place_when_only_the_age_changes():
    out = _ui(
        textwrap.dedent(f"""
        const states = {json.dumps(UI_STATES)};
        states["sensor.glucocraft_last_reading_time"] = {{ state: new Date(Date.now() - 5 * 60e3).toISOString(),
          last_updated: "a" }};
        const s = new (customElements.get("glucocraft-statistics"))(); s.setConfig({{}}); s._hass = {{ states }};
        s.draw();
        const root = s.shadowRoot, lr = root.querySelector(".lr"), pt = root.querySelector(".pt");
        const before = lr.innerHTML, table = pt.innerHTML.includes("<table");
        // marks: a part that is written again loses its mark
        root.innerHTML = "frame"; pt.innerHTML = "table";
        const now = Date.now; Date.now = () => now() + 60e3; s.draw(); Date.now = now;
        return {{ table, frame: root.innerHTML, kept: pt.innerHTML, aged: lr.innerHTML !== before,
          ages: [/5 minutes ago/.test(before), /6 minutes ago/.test(lr.innerHTML)] }};
    """)
    )
    assert out == {"table": True, "frame": "frame", "kept": "table", "aged": True, "ages": [True, True]}


# ------------------------------------------------------------------------------------------------ the card picker
def test_every_card_in_the_card_picker_has_its_own_description_and_its_options_in_the_guide():
    block = re.search(r"const GC_PICKER = \[(.*?)\];", UI, re.DOTALL).group(1)
    cards = re.findall(r'\["glucocraft-([a-z-]+)", "([a-z_]+)"\]', block)
    assert len(cards) >= 14
    descriptions = [FE[f"ui.card_picker.description.{key}"] for _, key in cards]
    assert len(set(descriptions)) == len(descriptions)
    assert all(FE[f"ui.card_picker.{key}"] for _, key in cards)
    assert "ui.card_picker.description" not in FE  # no shared description
    options = DASHBOARD_MD.split("## Cards on other dashboards", 1)[1].split("\n## ", 1)[0]
    for card, _ in cards:
        assert f"| `custom:glucocraft-{card}` |" in options, card
