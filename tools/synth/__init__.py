# License: AGPL-3.0-only (LICENSE).
"""Synthetic CGM data for GlucoCraft's tests and test Home Assistant instances.

All data produced here is artificial, except the optional local real history of real_history.py. Nothing in this
package is used by the integration itself.

Modules:
  common          units, rounding and the source-state text of a glucose value (no GlucoCraft import)
  profile         fits a profile (characteristics only) from a Home Assistant entity's recorded history (read-only)
  example_profile the neutral, artificial example profile used by default
  generate        a seeded generator of realistic series built from a profile
  scenarios       the named edge-case scenarios, each in mg/dL and mmol/L, and the recorder histories by length
  sine            deterministic sine waves (three peaks and three valleys per day) with closed-form statistics
  real_history    reads a real history into a local file outside the repository (optional; GET only)
  expected        an independent calculator of the published metrics and GlucoCraft's documented rules
  recorder_write  writes a series into a Home Assistant recorder database as an entity's state history
  live_feed       pushes a series to a Home Assistant entity over REST, one value every N seconds

Run the command-line tools from the repository root as modules, for example
``python -m tools.synth.generate --help``. See docs/DEVELOPER_GUIDE.md, "Synthetic data".
"""
