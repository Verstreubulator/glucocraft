// License: AGPL-3.0-only (LICENSE).
// =============================================================================
// gc_units.h - GlucoCraft panels: the texts' placeholders, glucose in the display unit (Home Assistant's unit
// pattern), the decimal separator and the A1C / GMI unit, the time and date formats, and the gap rule (last part of
// this file)
// =============================================================================
// Every glucose value on the panel is held in mg/dL (GlucoCraft's files and the value_mgdl attributes); only what is
// shown is converted. The unit to show is the Current Glucose sensor's unit (attribute "unit"), chosen in GlucoCraft's
// Display unit setting. Same rules as GlucoCraft (units.py) and the display card:
//   - mg/dL whole numbers; mmol/L one decimal;
//   - 1 mmol/L = 18.016 mg/dL; the consensus thresholds as published (54/70/140/180/250 mg/dL =
//     3.0/3.9/7.8/10.0/13.9 mmol/L; Battelino 2019, tight range Battelino 2023);
//   - graph steps chosen in the display unit (mmol/L 0.5/1/2/5), so the grid falls on round values.
// =============================================================================
#pragma once

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <initializer_list>
#include <string>
#include <type_traits>
#include "esphome/core/defines.h"
#include "esphome/core/preferences.h"   // the saved time zone (boot_tz / keep_tz)
#include "esphome/core/time.h"   // the time zone of ESPHome (USE_TIME_TIMEZONE: components/time/posix_tz.h)
#include "gc_text.h"   // the texts in the panel's language (generated from i18n/<language>.yaml)

namespace gcu {

// =============================================================================
// Texts with placeholders. Every text the panel shows is a constant of gc_text.h (gct::), generated from the panel's
// text catalogue; a text with values names them, "Reading: {minutes} min ago", so that a translation may put them in any
// order. fill() writes such a text with its values (each a string or a whole number) into buf, cut at n - 1 bytes; a
// placeholder without a value is left as it is, so that a missing value shows.
// =============================================================================
struct TextArg {
  const char *name;
  std::string value;
  TextArg(const char *n, const char *v) : name(n), value(v != nullptr ? v : "") {}
  TextArg(const char *n, const std::string &v) : name(n), value(v) {}
  template<typename T, typename std::enable_if<std::is_integral<T>::value, int>::type = 0>
  TextArg(const char *n, T v) : name(n), value(std::to_string(v)) {}
};

inline void fill(char *buf, size_t n, const char *text, std::initializer_list<TextArg> args) {
  if (n == 0) return;
  size_t k = 0;
  auto put = [&](const char *s, size_t len) {
    for (size_t i = 0; i < len && k + 1 < n; i++) buf[k++] = s[i];
  };
  for (const char *p = text != nullptr ? text : ""; *p;) {
    const char *end = *p == '{' ? strchr(p, '}') : nullptr;
    const TextArg *arg = nullptr;
    if (end != nullptr) {
      const size_t len = (size_t) (end - p - 1);
      for (const TextArg &a : args)
        if (strncmp(a.name, p + 1, len) == 0 && a.name[len] == '\0') arg = &a;
    }
    if (arg != nullptr) {
      put(arg->value.c_str(), arg->value.size());
      p = end + 1;
    } else {
      put(p++, 1);
    }
  }
  buf[k] = '\0';
}


constexpr float MGDL_PER_MMOLL = 18.016f;
constexpr double MGDL_PER_MMOLL_D = 18.016;
// The largest glucose value GlucoCraft stores (mg/dL; limits.DEFAULT_UPPER, the widest upper limit of a CGM system;
// glucocraft-shared.js GLU_MAX): a reading above it, or of 0, is no reading
constexpr int GLU_MAX = 500;

// mg/dL -> mmol/L with one decimal, halves away from zero, rounded once: the same rule as GlucoCraft (units.py
// round_half_up) and the cards (rh1), on the same value (the file's float32, the same arithmetic in double). The mean
// and SD in the files are not rounded to tenths of a mg/dL, so a rounding of the mg/dL value first would change the
// last digit of some of them. R1_FLOAT_EPS absorbs the float32 error of a file value (at most 8.5e-6 of a tenth of a
// mmol/L up to 500 mg/dL), so the exact halves 112.6 and 337.8 mg/dL (6.25 and 18.75 mmol/L) round up, as the backend
// rounds them from its own double.
constexpr double R1_FLOAT_EPS = 1e-5;
inline float r1_mmol(float mg) {
  const double x = fabs((double) mg) / MGDL_PER_MMOLL_D * 10.0;   // mmol/L x 10
  return (float) (copysign(floor(x + 0.5 + R1_FLOAT_EPS), (double) mg) / 10.0);
}

// A number Home Assistant sends as attribute text (the 5-minute delta; the 6-hour statistics before the first file),
// read as the double GlucoCraft calculated, not as float32 (whose nearest value of 286.4999948 is 286.5), and
// rounded once as GlucoCraft rounds it (units.py round_half_up: halves away from zero, 1e-9 for the binary error of
// a decimal half), as the display card rounds the same attribute.
// the attribute's number, NAN for none ("None", "unknown", "unavailable", nothing received yet)
inline double attr_value(const std::string &s) {
  char *end = nullptr;
  const double v = strtod(s.c_str(), &end);
  return (!s.empty() && end != s.c_str() && *end == '\0') ? v : NAN;
}
// x rounded once to dec decimals (0 or 1), in units of its last decimal: 286.4999948 -> 286, 7.25 (dec 1) -> 73
inline long round_half_up(double x, int dec = 0) {
  const double scale = dec == 1 ? 10.0 : 1.0;
  return (long) copysign(floor(fabs(x) * scale + 0.5 + 1e-9), x);
}

// =============================================================================
// Decimal separator and A1C / GMI unit (GlucoCraft's International settings, sent as the
// configuration sensor's attributes decimal_separator "." / "," and a1c_unit "percent" / "mmol_mol" / "both", always
// the resolved value; the same rules as the cards, glucocraft-shared.js nf() / dtx() and the a1c settings). Display
// only: the
// panel's own numbers, files and attributes keep the point.
// =============================================================================
inline bool &comma_flag() { static bool v = false; return v; }
inline bool comma() { return comma_flag(); }
// anything but "," (none yet, "None", "unavailable") = the point
inline void set_decimal_separator(const char *s) { comma_flag() = s != nullptr && strcmp(s, ",") == 0; }
// the decimal separator in a text that already holds numbers: a point between two digits becomes the comma
// (dtx() of the dashboard); "Loading..." and other points stay
inline void sep(char *buf) {
  if (!comma() || buf == nullptr) return;
  for (char *p = buf; *p; p++)
    if (*p == '.' && p > buf && p[-1] >= '0' && p[-1] <= '9' && p[1] >= '0' && p[1] <= '9') *p = ',';
}
// a copy of a finished text (for example a weekly page's description from the file, which always has the point) with
// the separator in use: "3.9-10.0 mmol/L" becomes "3,9-10,0 mmol/L"; "Battelino 2019" is unchanged
inline void sep_copy(char *buf, size_t n, const char *src) {
  if (n == 0) return;
  snprintf(buf, n, "%s", src != nullptr ? src : "");
  sep(buf);
}
// a number given in units of its last decimal (t = 655 with dec 2: "6.55"), dec 1 to 3, with the separator in use.
// The sign is written only for t < 0, so a value that rounds to zero is "0.0", never "-0.0" (a negative zero or
// -0.04 gives t = 0), as GlucoCraft (units.py round_half_up returns +0.0) and the display card.
inline void num_scaled(char *buf, size_t n, long t, int dec) {
  const long scale = dec == 1 ? 10 : dec == 2 ? 100 : 1000, a = labs(t);
  snprintf(buf, n, "%s%ld.%0*ld", t < 0 ? "-" : "", a / scale, dec, a % scale);
  sep(buf);
}
// a number with dec decimals (1 to 3) and the separator in use: "6.5" / "6,5"; "---" for none. Halves away from zero,
// as the display card (toFixed) and GlucoCraft (units.py round_half_up): printf "%.1f" rounds an exact half to even
// (1.25 -> "1.2", the card "1.3"). The float32 value times 10, 100 or 1000 is exact in double, so a half is a half;
// no epsilon is added (GlucoCraft writes each percentage as the float32 that rounds as its unrounded value:
// panel_feed.py _pct32).
inline void num(char *buf, size_t n, float v, int dec) {
  if (!std::isfinite(v)) { snprintf(buf, n, "---"); return; }
  if (dec < 1) dec = 1;
  if (dec > 3) dec = 3;
  const long scale = dec == 1 ? 10 : dec == 2 ? 100 : 1000;
  const long t = lround((double) v * (double) scale);
  num_scaled(buf, n, t, dec);
}

inline int &a1c_ref() { static int v = 0; return v; }   // 0 percent, 1 mmol/mol, 2 both
inline int a1c_mode() { return a1c_ref(); }
// anything else (none yet, "None") = percent
inline void set_a1c_unit(const char *u) {
  a1c_ref() = (u && strcmp(u, "mmol_mol") == 0) ? 1 : (u && strcmp(u, "both") == 0) ? 2 : 0;
}
// GMI in mmol/mol: one rule with GlucoCraft's backend (units.gmi_mmol_mol) and the display card (gmiMmolFromMean):
// from the mean glucose with the mmol/mol form of the GMI formula, 12.71 + 4.70587 x mean glucose in mmol/L
// (Bergenstal et al., 2018), whole numbers, halves up. GMI is not converted from its percentage with the IFCC-NGSP
// master equation, which applies to laboratory A1C results.
inline double gmi_mmol_mol_exact(double mean_mgdl) { return 12.71 + 4.70587 * mean_mgdl / MGDL_PER_MMOLL_D; }
inline long gmi_mmol_mol(float mean_mgdl) { return (long) floor(gmi_mmol_mol_exact(mean_mgdl) + 0.5 + 1e-9); }
// Where only the GMI in % is at hand (the axis of the weekly GMI page): the mean it was calculated from, by the
// percent form of the same formula, GMI (%) = 3.31 + 0.02392 x mean glucose in mg/dL (Bergenstal et al., 2018);
// gmi_pct_from_mmol is its inverse
inline double gmi_mmol_from_pct(double pct) { return gmi_mmol_mol_exact((pct - 3.31) / 0.02392); }
inline double gmi_pct_from_mmol(double mm) { return 3.31 + 0.02392 * ((mm - 12.71) / 4.70587 * MGDL_PER_MMOLL_D); }
// a GMI (percent, from the files) and the mean glucose it was calculated from (mg/dL, the same period or week) in the
// A1C unit: compact (statistics cells) "6.5%" / "48" / "6.5%/48"; full (a line of text) "6.5%" / "48 mmol/mol" /
// "6.5% (48 mmol/mol)" as the dashboard; "---" for none. Without a mean, the mmol/mol value from the percentage.
inline void fmt_gmi(char *buf, size_t n, float pct, float mean_mgdl, bool full = false) {
  if (std::isnan(pct) || !(pct > 0)) { snprintf(buf, n, "---"); return; }
  char p[12];
  num(p, sizeof(p), pct, 1);
  const long mm = mean_mgdl > 0 ? gmi_mmol_mol(mean_mgdl) : (long) floor(gmi_mmol_from_pct(pct) + 0.5 + 1e-9);
  if (a1c_mode() == 1) snprintf(buf, n, full ? "%ld mmol/mol" : "%ld", mm);
  else if (a1c_mode() == 2) snprintf(buf, n, full ? "%s%% (%ld mmol/mol)" : "%s%%/%ld", p, mm);
  else snprintf(buf, n, "%s%%", p);
}

inline bool &mmol_flag() { static bool m = false; return m; }
inline bool mmol() { return mmol_flag(); }
// set from the Current Glucose sensor's "unit" attribute
inline void set_unit(const char *u) { mmol_flag() = u != nullptr && strstr(u, "mmol") != nullptr; }
inline const char *unit() { return mmol() ? "mmol/L" : "mg/dL"; }

// mg/dL -> the display unit (a consensus threshold maps to its published mmol/L value)
inline float disp(float mg) {
  if (!mmol() || std::isnan(mg)) return mg;
  int r = (int) lroundf(mg);
  if (fabsf(mg - (float) r) < 0.05f) {
    switch (r) {
      case 54: return 3.0f;
      case 70: return 3.9f;
      case 140: return 7.8f;
      case 180: return 10.0f;
      case 250: return 13.9f;
      default: break;
    }
  }
  return r1_mmol(mg);
}

// a glucose value (mg/dL) as text: "120" / "6.7"; "---" for none
inline void fmt(char *buf, size_t n, float mg) {
  if (std::isnan(mg)) { snprintf(buf, n, "---"); return; }
  if (mmol()) num(buf, n, disp(mg), 1);
  else snprintf(buf, n, "%d", (int) lroundf(mg));
}

// =============================================================================
// Limit readings, LO / HI: a reading outside the sensor's measurement range is stored at
// the sensor's lower / upper limit (40 / 400 mg/dL, ...) and marked; GlucoCraft sends the mark as the Current Glucose
// sensor's attributes limit ("low" / "high" / null) and limit_text ("LO" / "HI" / null). Every display of the CURRENT
// value then shows the text instead of the number (never a measured number), in GlucoCraft's very-low / very-high
// color (const.py ZONE_COLORS). A limit reading has no value: the graphs draw no point for it (the line breaks, and
// the stretch is marked at the graph's bottom or top edge, never at a value), no statistic counts its number, and the
// times in the ranges count it as very low / very high.
// =============================================================================
inline int &limit_ref() { static int v = 0; return v; }   // 0 none, 1 low (LO), 2 high (HI)
inline int limit() { return limit_ref(); }
// the attribute limit: "low" / "high"; "None" (null: no limit reading) or anything else = none. Home Assistant sends
// nothing while the sensor is unavailable, so the last mark stays with the last value.
inline void set_limit(const char *s) {
  limit_ref() = (s && strcmp(s, "low") == 0) ? 1 : (s && strcmp(s, "high") == 0) ? 2 : 0;
}
// the text shown: LO / HI as GlucoCraft's limit_text names it ("LO" / "HI"), else by the mark; the words are the
// catalogue's (gct::value_lo / value_hi, the letters the value fonts hold)
inline int &limit_text_ref() { static int v = 0; return v; }   // 0 = by the mark, 1 LO, 2 HI
inline void set_limit_text(const char *s) {
  limit_text_ref() = (s && strcmp(s, "LO") == 0) ? 1 : (s && strcmp(s, "HI") == 0) ? 2 : 0;
}
inline const char *limit_text(int lim) {
  if (lim == 0) return nullptr;
  const int t = limit_text_ref() ? limit_text_ref() : lim;
  return t == 1 ? gct::value_lo : gct::value_hi;
}
// =============================================================================
// The seven glucose zones, in the order of GlucoCraft's zone mappings (const.py ZONES). The color of the current
// value, LO / HI included, is GlucoCraft's zone_color attribute of the Last glucose reading sensor (a LO reading is
// always very low and a HI reading always very high); the panels keep no zone palette for the value (only the graphs'
// limit marks use the published zone_colors, below). A mapping
// attribute (band_colors, below) reaches an ESPHome device as its Python text, for example
//   {'very_low': '#9B1C1F', 'low': '#E5352B', 'near_low': '#1F8F45', 'tight_range': '#52B964', ...}
// =============================================================================
enum Zone { Z_VERY_LOW = 0, Z_LOW, Z_NEAR_LOW, Z_TIGHT, Z_NEAR_HIGH, Z_HIGH, Z_VERY_HIGH, Z_COUNT };
// the attribute's keys, in the order of the Zone values
inline const char *zone_key(int z) {
  static const char *K[Z_COUNT] = {"very_low", "low", "near_low", "tight_range", "near_high", "high", "very_high"};
  return (z >= 0 && z < Z_COUNT) ? K[z] : "";
}

// =============================================================================
// Sensor warm-up: the status bar shows the minutes since GlucoCraft saw the warm-up start ("Warm-up: 12 min"), not a
// countdown, because the warm-up's length differs by CGM; plain "Warm-up" without a start time. started_at is the
// started_at attribute of binary_sensor.glucocraft_sensor_warm_up, UTC ISO ("YYYY-MM-DDThh:mm:ss+00:00"); now is the
// epoch in seconds. The same text as the display card.
// =============================================================================
// The days since 1970-01-01 of a date (year, month 1-12, day; the proleptic Gregorian calendar), by plain date
// arithmetic (days_from_civil), so that the device's time zone stays untouched
inline long civil_days(long y, int mo, int d) {
  const long yy = y - (mo <= 2), era = (yy >= 0 ? yy : yy - 399) / 400, yoe = yy - era * 400;
  const long doy = (153 * (mo + (mo > 2 ? -3 : 9)) + 2) / 5 + d - 1, doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + doe - 719468;
}
inline long warm_up_minutes(const std::string &started_at, uint32_t now) {
  int y, mo, d, h, mi, s;
  if (sscanf(started_at.c_str(), "%d-%d-%dT%d:%d:%d", &y, &mo, &d, &h, &mi, &s) != 6) return -1;
  // UTC date and time -> epoch; 64-bit seconds: long is 32 bits on these chips and an epoch passes its range in 2038
  const int64_t start = (int64_t) civil_days(y, mo, d) * 86400 + h * 3600L + mi * 60L + s;
  if (start <= 1600000000LL || (int64_t) now < start) return -1;   // no start time, or a clock not yet set
  return (long) (((int64_t) now - start) / 60);
}
inline void warm_up_text(char *buf, size_t n, const std::string &started_at, uint32_t now) {
  const long mins = warm_up_minutes(started_at, now);
  if (mins >= 0) fill(buf, n, gct::status_warm_up_minutes, {{"minutes", mins}});
  else fill(buf, n, gct::status_warm_up, {});
}
// The status bar's age of the newest reading (whole minutes since its measurement time, rounded down): "Reading: 0 min
// ago" to "Reading: 99 min ago", then whole hours, rounded down, "Reading: 1 h ago" (100 to 119 minutes), "Reading: 2 h
// ago"; the abbreviations have no plural. The same text as the display card.
inline void reading_age(char *buf, size_t n, unsigned long minutes) {
  if (minutes <= 99) fill(buf, n, gct::status_reading_minutes, {{"minutes", minutes}});
  else fill(buf, n, gct::status_reading_hours, {{"hours", minutes / 60}});
}
// The words in the glucose value's place while there is no current reading, never a number (the display card's rule,
// docs/DISPLAY_CONTRACT.md): during a warm-up "Sensor warming up"; without current data (warm_up false, no_current
// true) "Sensor expired" after the stated expiry, else "No current data"; nullptr while the reading is current (the
// value is shown in its zone color, also after the stated expiry: the status bar carries the expiry).
inline const char *no_reading_words(bool warm_up, bool no_current, bool expired) {
  if (warm_up) return gct::no_reading_warming_up;
  if (!no_current) return nullptr;
  return expired ? gct::no_reading_sensor_expired : gct::no_reading_no_current_data;
}
// Under the words, the last reading (the display card's rule): "Last reading: 64 mg/dL" / "Last reading: 3.6 mmol/L"
// (rounded once, in the display unit), a limit reading as "Last reading: LO" / "Last reading: HI" (lim: limit(), or
// the saved mark after a reboot); empty without a reading (mg NaN or not above 0).
inline void last_reading(char *buf, size_t n, float mg, int lim) {
  if (std::isnan(mg) || mg <= 0) { if (n) buf[0] = '\0'; return; }
  const char *t = limit_text(lim);
  if (t != nullptr) { fill(buf, n, gct::no_reading_last_reading_limit, {{"value", t}}); return; }
  char v[16];
  fmt(v, sizeof(v), mg);
  fill(buf, n, gct::no_reading_last_reading, {{"value", v}, {"unit", unit()}});
}
// The age of the last reading in whole minutes, rounded down: "45 min ago" under an hour, "2 h 0 min ago" under a day,
// "1 d 3 h ago" from a day
inline void reading_ago(char *buf, size_t n, unsigned long minutes) {
  if (minutes < 60) fill(buf, n, gct::no_reading_ago_minutes, {{"minutes", minutes}});
  else if (minutes < 1440) fill(buf, n, gct::no_reading_ago_hours, {{"hours", minutes / 60}, {"minutes", minutes % 60}});
  else fill(buf, n, gct::no_reading_ago_days, {{"days", minutes / 1440}, {"hours", minutes % 1440 / 60}});
}
inline int hex_digit(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}
// "#RRGGBB" at p -> 0xRRGGBB; -1 when p does not hold one
inline int parse_hex_color(const char *p) {
  if (p == nullptr || p[0] != '#') return -1;
  int v = 0;
  for (int i = 1; i <= 6; i++) {
    const int d = hex_digit(p[i]);
    if (d < 0) return -1;
    v = v * 16 + d;
  }
  return v;
}
// Reads a mapping attribute zone -> "#RRGGBB" (zone_colors, band_colors) into table, in the order of the Zone values.
// Each key is looked up with its quotes ('low' or "low"), so "low" never matches "very_low" or "near_low"; its color
// is the first "#RRGGBB" after the key. A key that is missing or has no valid color keeps its current color ("None"
// or "unavailable" while Home Assistant restarts changes nothing). Returns true when any color changed, so the
// caller redraws.
inline bool read_zone_map(const char *s, int *table) {
  if (s == nullptr) return false;
  bool changed = false;
  for (int z = 0; z < Z_COUNT; z++) {
    char key[16];
    const char *hit = nullptr;
    for (char q : {'\'', '"'}) {
      snprintf(key, sizeof(key), "%c%s%c", q, zone_key(z), q);
      hit = strstr(s, key);
      if (hit != nullptr) break;
    }
    if (hit == nullptr) continue;
    const char *hash = strchr(hit + strlen(key), '#');
    const char *next_key = strchr(hit + strlen(key), ',');   // the color must come before the next entry
    if (hash == nullptr || (next_key != nullptr && hash > next_key)) continue;
    const int c = parse_hex_color(hash);
    if (c >= 0 && c != table[z]) {
      table[z] = c;
      changed = true;
    }
  }
  return changed;
}

// =============================================================================
// Graph range colors. GlucoCraft publishes the colors of the graph bands (its graph range color settings) as the
// configuration sensor's attribute band_colors, in the same form as zone_colors. The bands behind every graph (60 %
// over the graph's black) take their colors from there; the value, LO / HI included, keeps GlucoCraft's zone color.
// Until the attribute arrives (and with a GlucoCraft version that does not send it) the panels use GlucoCraft's
// default below: the colors of the CGM Report's ambulatory glucose profile (AGP; const.py BAND_COLORS), with one
// color for the range outside the tight range, above it and below it.
// =============================================================================
constexpr int DEFAULT_BAND_COLORS[Z_COUNT] = {0x9B1C1F, 0xE5352B, 0x1F8F45, 0x52B964, 0x1F8F45, 0xFCC21B, 0xF08A4B};
inline int *band_table() {
  static int t[Z_COUNT] = {DEFAULT_BAND_COLORS[0], DEFAULT_BAND_COLORS[1], DEFAULT_BAND_COLORS[2], DEFAULT_BAND_COLORS[3],
                           DEFAULT_BAND_COLORS[4], DEFAULT_BAND_COLORS[5], DEFAULT_BAND_COLORS[6]};
  return t;
}
inline int band_color(int z) { return (z >= 0 && z < Z_COUNT) ? band_table()[z] : 0x888888; }
// the band_colors attribute
inline bool set_band_colors(const char *s) { return read_zone_map(s, band_table()); }

// =============================================================================
// Range colors for the graphs' limit marks. The marks of a stretch of readings at the sensor's limit (LO / HI) are
// drawn in the very-low / very-high range color, the color the value has at LO / HI, as on the display card. GlucoCraft
// publishes its range colors as the configuration sensor's attribute zone_colors; until it arrives (and with a
// GlucoCraft version that does not send it) the panels use GlucoCraft's default palette below (const.py ZONE_COLORS).
// The value itself always takes GlucoCraft's zone_color of the Last glucose reading sensor (above), never this table.
// =============================================================================
constexpr int DEFAULT_ZONE_COLORS[Z_COUNT] = {0xC62828, 0xEF5350, 0xFFC107, 0x4CAF50, 0xFFC107, 0x42A5F5, 0x1565C0};
inline int *zone_table() {
  static int t[Z_COUNT] = {DEFAULT_ZONE_COLORS[0], DEFAULT_ZONE_COLORS[1], DEFAULT_ZONE_COLORS[2], DEFAULT_ZONE_COLORS[3],
                           DEFAULT_ZONE_COLORS[4], DEFAULT_ZONE_COLORS[5], DEFAULT_ZONE_COLORS[6]};
  return t;
}
inline int zone_color(int z) { return (z >= 0 && z < Z_COUNT) ? zone_table()[z] : 0x888888; }
// the zone_colors attribute
inline bool set_zone_colors(const char *s) { return read_zone_map(s, zone_table()); }

// =============================================================================
// Text on a colored background (the status bar, whose color is GlucoCraft's status color): black or white,
// whichever has the higher contrast ratio with the background (WCAG 2.2 relative luminance), white when both are equal
// (the same rule as the display card). With GlucoCraft's status colors this gives black on amber, green, red and gray
// and white on purple, each at least 4.5:1.
// =============================================================================
inline float srgb_channel(int c8) {
  const float c = c8 / 255.0f;
  return c <= 0.04045f ? c / 12.92f : powf((c + 0.055f) / 1.055f, 2.4f);
}
inline float luminance(int rgb) {
  return 0.2126f * srgb_channel((rgb >> 16) & 0xFF) + 0.7152f * srgb_channel((rgb >> 8) & 0xFF) +
         0.0722f * srgb_channel(rgb & 0xFF);
}
inline int text_on(int bg) {
  const float l = luminance(bg);
  const float with_white = 1.05f / (l + 0.05f), with_black = (l + 0.05f) / 0.05f;
  return with_white >= with_black ? 0xFFFFFF : 0x000000;
}
// the contrast ratio of two colors (WCAG 2.2: from 1:1 to 21:1)
inline float contrast_ratio(int a, int b) {
  const float la = luminance(a), lb = luminance(b);
  return la > lb ? (la + 0.05f) / (lb + 0.05f) : (lb + 0.05f) / (la + 0.05f);
}

// =============================================================================
// Panel colors. GlucoCraft publishes the wall panels' background (its setting, black by default) and the text color
// for it (black or white, whichever has the higher contrast) as the configuration sensor's attributes
// panel_background and panel_foreground ("#RRGGBB"). The LCD panels apply them to the screen and to every text that
// the black design shows in white or gray: their LVGL styles theme_* (style_definitions), set by the script
// apply_panel_theme from theme_color(). Not themed: the graphs (their own black canvas, white line and white labels on
// every background), the status bar (its status color) and the value (its zone color).
// The grays of the black design (#AAAAAA the secondary texts, #888888 the labels, #808080 the frames of the
// statistics) are mixes of the text color into the background: gray level / 255 of the
// way from the background to the text color, so that black with white text gives exactly those grays. Where a mix
// falls below its minimum contrast with the background (WCAG 2.2 success criteria 1.4.3 and 1.4.11: 4.5:1 for text,
// 3:1 for the frames), it moves on towards the text color until it reaches it.
// The colored status text outside the graphs and the status bar (#4CAF50, the loading text) keeps its hue: where it
// is below 4.5:1 with the background, its lightness (HSL) moves towards the text color's until it reaches 4.5:1
// (ACCESSIBILITY.md rule 2: a darker or lighter shade of the same hue). On black it already passes, so it stays as it
// is.
// The panel keeps the last colors it received in its flash (one SavedTheme), so a restart shows them at once.
// =============================================================================
enum ThemeRole {
  TH_BACKGROUND = 0, TH_TEXT, TH_TEXT_SECONDARY, TH_TEXT_LABEL, TH_FRAME, TH_PROGRESS, TH_COUNT
};
struct ThemeGray {
  int role;
  int level;          // the gray of the black design (0x88 = #888888)
  float min_ratio;    // its minimum contrast with the background
};
constexpr ThemeGray THEME_GRAYS[] = {
    {TH_TEXT_SECONDARY, 0xAA, 4.5f}, {TH_TEXT_LABEL, 0x88, 4.5f}, {TH_FRAME, 0x80, 3.0f}};
constexpr int DEFAULT_PANEL_BACKGROUND = 0x000000;
constexpr int DEFAULT_PANEL_TEXT = 0xFFFFFF;
struct ThemeHue {
  int role;
  int color;   // the color of the black design
};
constexpr ThemeHue THEME_HUES[] = {{TH_PROGRESS, 0x4CAF50}};
constexpr float THEME_HUE_MIN_RATIO = 4.5f;   // small text (WCAG 2.2 success criterion 1.4.3)

// HSL (hue 0-6, saturation and lightness 0-1) to and from 0xRRGGBB
inline void rgb_to_hsl(int rgb, float &h, float &s, float &l) {
  const float r = ((rgb >> 16) & 0xFF) / 255.0f, g = ((rgb >> 8) & 0xFF) / 255.0f, b = (rgb & 0xFF) / 255.0f;
  const float mx = fmaxf(r, fmaxf(g, b)), mn = fminf(r, fminf(g, b)), d = mx - mn;
  l = (mx + mn) / 2.0f;
  s = d == 0.0f ? 0.0f : d / (1.0f - fabsf(2.0f * l - 1.0f));
  if (d == 0.0f) h = 0.0f;
  else if (mx == r) h = fmodf((g - b) / d + 6.0f, 6.0f);
  else if (mx == g) h = (b - r) / d + 2.0f;
  else h = (r - g) / d + 4.0f;
}
inline int hsl_to_rgb(float h, float s, float l) {
  const float c = (1.0f - fabsf(2.0f * l - 1.0f)) * s, x = c * (1.0f - fabsf(fmodf(h, 2.0f) - 1.0f)), m = l - c / 2.0f;
  float r = 0, g = 0, b = 0;
  if (h < 1) { r = c; g = x; } else if (h < 2) { r = x; g = c; } else if (h < 3) { g = c; b = x; }
  else if (h < 4) { g = x; b = c; } else if (h < 5) { r = x; b = c; } else { r = c; b = x; }
  auto ch = [](float v) { return (int) lroundf(fminf(fmaxf(v, 0.0f), 1.0f) * 255.0f); };
  return ch(r + m) << 16 | ch(g + m) << 8 | ch(b + m);
}
// color as it is when it reaches min_ratio with bg, else its shade of the same hue whose lightness has moved towards
// fg's (in steps of 1 %) until it does, at most to fg's lightness
inline int shade_for(int color, int bg, int fg, float min_ratio) {
  if (contrast_ratio(color, bg) >= min_ratio) return color;
  float h, s, l, fh, fs, fl;
  rgb_to_hsl(color, h, s, l);
  rgb_to_hsl(fg, fh, fs, fl);
  const float step = fl > l ? 0.01f : -0.01f;
  int c = color;
  while (contrast_ratio(c, bg) < min_ratio && fabsf(fl - l) > 0.005f) {
    l = fabsf(fl - l) < 0.01f ? fl : l + step;
    c = hsl_to_rgb(h, s, l);
  }
  return c;
}

// a mix of fg into bg: level / 255 of the way from bg to fg, each channel rounded
inline int mix_color(int bg, int fg, int level) {
  int out = 0;
  for (int shift = 16; shift >= 0; shift -= 8) {
    const int b = (bg >> shift) & 0xFF, f = (fg >> shift) & 0xFF;
    const int c = b + (int) lroundf((float) (f - b) * (float) level / 255.0f);
    out |= c << shift;
  }
  return out;
}

struct SavedTheme {
  uint32_t magic;
  int32_t background;
  int32_t text;         // the panel_foreground received; -1 = none yet (then text_on(background))
};
constexpr uint32_t SAVED_THEME_MAGIC = 0x47434831;   // "GCH1": the record holds colors received from GlucoCraft
constexpr uint32_t SAVED_THEME_KEY = 0x67746831;     // the record's preferences key
inline SavedTheme &theme_received() {
  static SavedTheme t{SAVED_THEME_MAGIC, DEFAULT_PANEL_BACKGROUND, -1};
  return t;
}
inline int *theme_table() {
  static int t[TH_COUNT] = {DEFAULT_PANEL_BACKGROUND, DEFAULT_PANEL_TEXT, 0xAAAAAA, 0x888888, 0x808080, 0x4CAF50};
  return t;
}
inline int theme_color(int role) { return (role >= 0 && role < TH_COUNT) ? theme_table()[role] : DEFAULT_PANEL_TEXT; }
// the table from the colors received: the background, the text color, the grays
inline void derive_theme() {
  const SavedTheme &r = theme_received();
  const int bg = r.background, fg = r.text >= 0 ? r.text : text_on(bg);
  int *t = theme_table();
  t[TH_BACKGROUND] = bg;
  t[TH_TEXT] = fg;
  for (const ThemeGray &g : THEME_GRAYS) {
    int level = g.level, c = mix_color(bg, fg, level);
    while (level < 255 && contrast_ratio(c, bg) < g.min_ratio) c = mix_color(bg, fg, ++level);
    t[g.role] = c;
  }
  for (const ThemeHue &hue : THEME_HUES) t[hue.role] = shade_for(hue.color, bg, fg, THEME_HUE_MIN_RATIO);
}
inline esphome::ESPPreferenceObject &theme_pref() {
  static esphome::ESPPreferenceObject pref =
      esphome::global_preferences->make_preference<SavedTheme>(SAVED_THEME_KEY, true);
  return pref;
}
// on_boot: the colors saved last (none saved: the defaults)
inline void boot_theme() {
  SavedTheme s{};
  if (theme_pref().load(&s) && s.magic == SAVED_THEME_MAGIC) theme_received() = s;
  derive_theme();
}
// an attribute's "#RRGGBB" exactly; -1 for anything else ("None", "unavailable" while Home Assistant restarts)
inline int exact_hex_color(const char *s) {
  return (s != nullptr && strlen(s) == 7) ? parse_hex_color(s) : -1;
}
// one received color: a valid new value is taken, derived and saved; returns true when it changed (the caller
// applies the styles)
inline bool take_theme_color(int32_t &slot, const char *s) {
  const int c = exact_hex_color(s);
  if (c < 0 || c == slot) return false;
  slot = c;
  derive_theme();
  theme_pref().save(&theme_received());
  return true;
}
inline bool set_panel_background(const char *s) { return take_theme_color(theme_received().background, s); }
inline bool set_panel_foreground(const char *s) { return take_theme_color(theme_received().text, s); }

// =============================================================================
// The source name is the user's own Home Assistant name. The panel fonts hold ASCII, Latin-1 and the common Central
// European letters, and LVGL leaves out a character its font does not hold ("Sam's" typed with a phone's
// typographic apostrophe would show as "Sams"). The typographic quotation marks, dashes and the ellipsis are
// therefore shown as their plain forms; letters in other scripts are not in the fonts and stay missing.
// =============================================================================
inline std::string plain_punctuation(const std::string &in) {
  std::string out;
  out.reserve(in.size());
  for (size_t i = 0; i < in.size(); i++) {
    const unsigned char c = (unsigned char) in[i];
    if (c == 0xE2 && i + 2 < in.size() && (unsigned char) in[i + 1] == 0x80) {
      const unsigned char d = (unsigned char) in[i + 2];
      const char *rep = nullptr;
      if (d == 0x98 || d == 0x99) rep = "'";                 // U+2018, U+2019
      else if (d == 0x9C || d == 0x9D) rep = "\"";           // U+201C, U+201D
      else if (d == 0x93 || d == 0x94) rep = "-";            // U+2013, U+2014
      else if (d == 0xA6) rep = "...";                       // U+2026
      if (rep != nullptr) { out += rep; i += 2; continue; }
    }
    out += (char) c;
  }
  return out;
}
// the current value: "LO" / "HI" for a limit reading (lim: limit(), or the saved mark after a reboot), else fmt()
inline void fmt_current(char *buf, size_t n, float mg, int lim) {
  const char *t = limit_text(lim);
  if (t != nullptr && !std::isnan(mg)) snprintf(buf, n, "%s", t);
  else fmt(buf, n, mg);
}

// the 5-minute delta (mg/dL, GlucoCraft's unrounded change: attr_value) with its sign, rounded once in the display
// unit: "+5" / "-12"; "+0.3" / "-0.7"; a delta that rounds to zero without a sign, "0" / "0.0" (a sign says a
// direction, and zero has none; never "-0.0")
inline void fmt_delta(char *buf, size_t n, double mg) {
  if (mmol()) {
    const long t = round_half_up(mg / MGDL_PER_MMOLL_D, 1);   // tenths of a mmol/L; 0 for -0.04
    char d[16];
    num_scaled(d, sizeof(d), t, 1);
    snprintf(buf, n, t > 0 ? "+%s" : "%s", d);
  } else {
    const long d = round_half_up(mg);
    snprintf(buf, n, d > 0 ? "+%ld" : "%ld", d);
  }
}

// a whole-number percentage: "73%" (halves away from zero, like the display card's Math.round for
// these positive values; printf "%.0f" rounds an exact .5 to even); "---" for none
inline void pct0(char *buf, size_t n, float v) {
  if (std::isnan(v)) { snprintf(buf, n, "---"); return; }
  snprintf(buf, n, "%ld%%", lroundf(v));
}

// a percentage with one decimal and the separator in use: "1.2%" / "1,2%" (num(): halves away from zero); "---" for
// none
inline void pct1(char *buf, size_t n, float v) {
  if (std::isnan(v)) { snprintf(buf, n, "---"); return; }
  char d[16];
  num(d, sizeof(d), v, 1);
  snprintf(buf, n, "%s%%", d);
}

// the Very low and Very high statistics cells: one decimal as pct1, but a value that rounds to 100.0 as "100%", so that
// it fits the square panel's side column (52 px at 20 px); the display card's cells follow the same rule
// (decided in double, as pct1 rounds: the float32 file value 99.949997 is "99.9%" here as on the display card)
inline void pct1_cell(char *buf, size_t n, float v) {
  if (!std::isnan(v) && lround((double) v * 10.0) >= 1000) { snprintf(buf, n, "100%%"); return; }
  pct1(buf, n, v);
}

// =============================================================================
// The 6-hour statistics before the first file, from the attributes' text (attr_value, round_half_up), as the display
// card shows the same attributes (shown 286 where a float32 sensor would show 287 for 286.4999948). The files'
// float32 values keep the functions above (GlucoCraft writes each as the float32 that rounds as its unrounded value).
// =============================================================================
// a whole-number percentage: "73%"; "---" for none
inline void pct0_attr(char *buf, size_t n, double v) {
  if (std::isnan(v)) { snprintf(buf, n, "---"); return; }
  snprintf(buf, n, "%ld%%", round_half_up(v));
}
// the Very low and Very high cells (pct1_cell): one decimal, a value that rounds to 100.0 as "100%"
inline void pct1_cell_attr(char *buf, size_t n, double v) {
  if (std::isnan(v)) { snprintf(buf, n, "---"); return; }
  const long t = round_half_up(v, 1);
  if (t >= 1000) { snprintf(buf, n, "100%%"); return; }
  char d[16];
  num_scaled(d, sizeof(d), t, 1);
  snprintf(buf, n, "%s%%", d);
}
// a glucose value (mg/dL) in the display unit (fmt): whole mg/dL, or mmol/L with one decimal (a consensus threshold
// as published); "---" for none
inline void fmt_attr(char *buf, size_t n, double mg) {
  if (std::isnan(mg)) { snprintf(buf, n, "---"); return; }
  if (!mmol()) { snprintf(buf, n, "%ld", round_half_up(mg)); return; }
  if (mg == floor(mg) && (mg == 54 || mg == 70 || mg == 140 || mg == 180 || mg == 250)) {
    num(buf, n, disp((float) mg), 1);
    return;
  }
  num_scaled(buf, n, round_half_up(mg / MGDL_PER_MMOLL_D, 1), 1);
}

// a percentage beside the target or goal it is compared with (rule; NAN = none: pct1): one decimal, or two where one
// decimal would show the rule's value although the value is on the other side of it (69.96% beside "more than 70%",
// not 70.0%); where two decimals still show the rule's value, the last digit moves to the value's side (69.996% ->
// 69.99%, 70.004% -> 70.01%). The same rule as GlucoCraft (units.py shown_against) and the display card. GlucoCraft
// rule. The rules (the consensus targets, the percentage goals) are whole numbers or tenths; compared in tenths and
// hundredths of a percent, as the shown digits.
inline void pct_against(char *buf, size_t n, float v, float rule) {
  if (std::isnan(v) || std::isnan(rule)) { pct1(buf, n, v); return; }
  char d[16];
  const long t1 = lround((double) v * 10.0);
  if (t1 != lround((double) rule * 10.0) || v == rule) {
    num_scaled(d, sizeof(d), t1, 1);
  } else {
    const long r2 = lround((double) rule * 100.0);
    long t2 = lround((double) v * 100.0);
    if (t2 == r2) t2 = v < rule ? r2 - 1 : r2 + 1;
    num_scaled(d, sizeof(d), t2, 2);
  }
  snprintf(buf, n, "%s%%", d);
}

// graph grid: a round step in the display unit for a span (mg/dL), returned in mg/dL; mg/dL steps of 10 / 20 / 25 / 50 / 100
inline float axis_step(float span_mg, float lines) {
  float raw = span_mg / lines;
  if (!mmol()) {
    if (raw <= 10) return 10;
    if (raw <= 20) return 20;
    if (raw <= 25) return 25;
    if (raw <= 50) return 50;
    return 100;
  }
  float r = raw / MGDL_PER_MMOLL;
  float s = r <= 0.5f ? 0.5f : r <= 1.0f ? 1.0f : r <= 2.0f ? 2.0f : r <= 5.0f ? 5.0f : 10.0f;
  return s * MGDL_PER_MMOLL;
}

// the first grid line at or above lo (mg/dL), on the step's round values in the display unit
inline float axis_first(float lo_mg, float step_mg) {
  if (!mmol()) return ceilf(lo_mg / step_mg) * step_mg;
  float s = step_mg / MGDL_PER_MMOLL;
  return ceilf((lo_mg / MGDL_PER_MMOLL) / s - 1e-4f) * s * MGDL_PER_MMOLL;
}

// every second line is a major line (in mg/dL: every multiple of twice the step; a step of 50 or more: all)
inline bool axis_major(float g_mg, float step_mg) {
  if (!mmol()) return ((int) g_mg % (int) (step_mg * 2) == 0) || step_mg >= 50;
  float s = step_mg / MGDL_PER_MMOLL;
  long k = lroundf((g_mg / MGDL_PER_MMOLL) / s);
  return (k % 2) == 0 || s >= 5.0f;
}

// a grid line's label: "100"; in mmol/L one format per axis, as on every graph of GlucoCraft (a mixed axis such as
// "7 / 6.5 / 6" reads as two scales): an axis whose step has a half gives every label one decimal ("7.0" / "6.5" /
// "6.0"), an axis with a whole-number step whole numbers ("4" / "6" / "8")
inline void axis_label(char *buf, size_t n, float g_mg, float step_mg) {
  if (!mmol()) { snprintf(buf, n, "%d", (int) lroundf(g_mg)); return; }
  float v = r1_mmol(g_mg), s = step_mg / MGDL_PER_MMOLL;
  if (fabsf(s - roundf(s)) < 0.05f) snprintf(buf, n, "%d", (int) roundf(v));
  else num(buf, n, v, 1);
}

// The weekly pages' axis (the value gd in the page's own unit: %, mg/dL, mmol/L or mmol/mol, on a grid of step) in one
// format per axis, the same rule: every label with the step's decimals ("6.5 / 7.0 / 7.5" with a step of 0.5, "2.25 /
// 2.50 / 2.75" with 0.25, "10 / 20 / 30" with 10), with the separator in use
inline int step_decimals(float step) {
  if (fabsf(step - roundf(step)) < 1e-3f) return 0;
  if (fabsf(step * 10.0f - roundf(step * 10.0f)) < 1e-3f) return 1;
  return 2;
}
inline void weekly_axis_label(char *buf, size_t n, float gd, float step) {
  const int dec = step_decimals(step);
  if (dec == 0) snprintf(buf, n, "%ld", lroundf(gd));
  else num(buf, n, gd, dec);
}

// =============================================================================
// Time and date formats (GlucoCraft's settings, sent as the configuration sensor's attributes
// time_format "12"/"24" and date_format "MDY"/"DMY"/"YMD"; the same formats as GlucoCraft's regional.py and the cards)
// =============================================================================
inline bool &t24_flag() { static bool v = false; return v; }
inline bool t24() { return t24_flag(); }
inline int &date_order_ref() { static int v = 0; return v; }   // 0 MDY, 1 DMY, 2 YMD
inline int date_order() { return date_order_ref(); }
inline void set_time_format(const char *f) { t24_flag() = f != nullptr && strcmp(f, "24") == 0; }
inline void set_date_format(const char *f) {
  date_order_ref() = (f && strcmp(f, "DMY") == 0) ? 1 : (f && strcmp(f, "YMD") == 0) ? 2 : 0;
}

inline const char *month_name(int mon0, bool full) {
  if (mon0 < 0 || mon0 > 11) mon0 = 0;
  return full ? gct::month[mon0] : gct::month_short[mon0];
}

// the clock: "7:46 AM" / "07:46"
inline void clock(char *buf, size_t n, int hour, int minute) {
  if (t24()) { snprintf(buf, n, "%02d:%02d", hour, minute); return; }
  char t[8];
  snprintf(t, sizeof(t), "%d:%02d", hour % 12 == 0 ? 12 : hour % 12, minute);
  fill(buf, n, hour < 12 ? gct::time_am : gct::time_pm, {{"time", t}});
}

// an hour on a time axis: "6am" / "06:00"
inline void hour_label(char *buf, size_t n, int hour) {
  if (t24()) { snprintf(buf, n, "%02d:00", hour); return; }
  fill(buf, n, hour < 12 ? gct::time_hour_am : gct::time_hour_pm, {{"hour", hour % 12 == 0 ? 12 : hour % 12}});
}

// =============================================================================
// Local time = Home Assistant's time zone. ESPHome
// 2026.9 keeps one time zone for the whole device and replaces libc's localtime() / localtime_r() with its own
// conversion in that zone (components/time/posix_tz.cpp); the homeassistant time platform WITHOUT a timezone option
// takes Home Assistant's zone with every time sync (Home Assistant 2026.3 and newer send it pre-parsed;
// api_connection.cpp on_get_time_response). At boot each panel applies the last zone it received from Home Assistant
// (boot_tz() below), never the zone of the computer that built the firmware. tz_signature() changes when the zone
// does (the panels redraw their time axes then).
// =============================================================================
inline int32_t tz_signature() {
#ifdef USE_TIME_TIMEZONE
  const auto &z = esphome::time::get_global_tz();
  return z.std_offset_seconds * 7 + z.dst_offset_seconds * 3 + (int32_t) z.dst_start.month * 101 +
         (int32_t) z.dst_end.month * 103 + (int32_t) z.dst_start.week * 11 + (int32_t) z.dst_end.week * 13 +
         z.dst_start.time_seconds + z.dst_end.time_seconds * 5 + (int32_t) z.dst_start.type * 17;
#else
  return 0;
#endif
}
// UTC (offsets 0, no DST rule)
inline void set_utc() {
#ifdef USE_TIME_TIMEZONE
  esphome::time::ParsedTimezone utc{};
  esphome::time::set_global_tz(utc);
#endif
}

// The device keeps its time zone in RAM only: after a reboot it would be on UTC until Home Assistant answers, and a
// panel that starts while Home Assistant is down or slow would show every local time off by the zone's UTC offset.
// So each panel keeps the last zone received from Home Assistant in its flash (the preferences of ESPHome, one SavedTz):
//   - on_boot: boot_tz() applies the saved zone; UTC only when none was ever saved (the very first boot);
//   - on_time_sync of the homeassistant time platform: mark_ha_time_sync(). ESPHome sets Home Assistant's zone right
//     after that callback, so the zone is copied later, by keep_tz() in the panel's 1-second (or 5-second) interval;
//   - keep_tz() saves only when the zone differs from the saved one (ESPHome writes it to flash at its next sync).
// (A restore_value global cannot hold this structure: ESPHome declares the globals before the includes.)
constexpr uint32_t SAVED_TZ_MAGIC = 0x47435A31;   // "GCZ1": the record holds a zone received from Home Assistant
constexpr uint32_t SAVED_TZ_KEY = 0x6774747A;     // the record's preferences key
struct SavedTz {
  uint32_t magic;
#ifdef USE_TIME_TIMEZONE
  esphome::time::ParsedTimezone tz;
#endif
};
inline esphome::ESPPreferenceObject &tz_pref() {
  static esphome::ESPPreferenceObject pref = esphome::global_preferences->make_preference<SavedTz>(SAVED_TZ_KEY, true);
  return pref;
}
inline SavedTz &tz_saved() { static SavedTz s{}; return s; }   // the copy in RAM of the record in flash
inline bool &ha_time_synced() { static bool v = false; return v; }
inline void mark_ha_time_sync() { ha_time_synced() = true; }
#ifdef USE_TIME_TIMEZONE
inline bool same_rule(const esphome::time::DSTRule &a, const esphome::time::DSTRule &b) {
  return a.time_seconds == b.time_seconds && a.day == b.day && a.type == b.type && a.month == b.month &&
         a.week == b.week && a.day_of_week == b.day_of_week;
}
inline bool same_tz(const esphome::time::ParsedTimezone &a, const esphome::time::ParsedTimezone &b) {
  return a.std_offset_seconds == b.std_offset_seconds && a.dst_offset_seconds == b.dst_offset_seconds &&
         same_rule(a.dst_start, b.dst_start) && same_rule(a.dst_end, b.dst_end);
}
#endif
inline void boot_tz() {
#ifdef USE_TIME_TIMEZONE
  SavedTz s{};
  if (tz_pref().load(&s) && s.magic == SAVED_TZ_MAGIC) {
    tz_saved() = s;
    esphome::time::set_global_tz(s.tz);
    return;
  }
#endif
  set_utc();
}
inline void keep_tz() {
#ifdef USE_TIME_TIMEZONE
  if (!ha_time_synced()) return;   // the zone in use is still the saved one or UTC
  const auto &now = esphome::time::get_global_tz();
  SavedTz &saved = tz_saved();
  if (saved.magic == SAVED_TZ_MAGIC && same_tz(saved.tz, now)) return;
  SavedTz fresh{};
  fresh.magic = SAVED_TZ_MAGIC;
  fresh.tz = now;
  saved = fresh;
  tz_pref().save(&saved);
#endif
}

// the start of the LOCAL hour that contains ts (hour lines and labels at local whole hours, not at UTC hours - they
// differ in half-hour time zones); localtime_r = the one of ESPHome, in Home Assistant's zone (above)
inline uint32_t local_hour_floor(uint32_t ts) {
  time_t t = (time_t) ts;
  struct tm lt;
  if (!localtime_r(&t, &lt)) return ts - ts % 3600;
  return ts - (uint32_t) (lt.tm_min * 60 + lt.tm_sec);
}

// the local hour of the day (0-23) at ts
inline int local_hour(uint32_t ts) {
  time_t t = (time_t) ts;
  struct tm lt;
  return localtime_r(&t, &lt) ? lt.tm_hour : 0;
}

// a date with the month name: "Oct 22" / "22 Oct" (full: "October 22" / "22 October")
inline void day_month(char *buf, size_t n, int mon0, int mday, bool full) {
  if (date_order() == 1) snprintf(buf, n, "%d %s", mday, month_name(mon0, full));
  else snprintf(buf, n, "%s %d", month_name(mon0, full), mday);
}

// a numeric date without the year: "10/22" / "22/10" / "10-22"
inline void numeric_date(char *buf, size_t n, int mon1, int mday) {
  if (date_order() == 1) snprintf(buf, n, "%d/%d", mday, mon1);
  else if (date_order() == 2) snprintf(buf, n, "%02d-%02d", mon1, mday);
  else snprintf(buf, n, "%d/%d", mon1, mday);
}

// When the last reading was taken, in Home Assistant's time zone (localtime_r, above): "Today at 17:39", "Yesterday at
// 17:39", else its date ("Oct 22 at 17:39" / "22 Oct at 17:39"), the time in the setting's format; the day by the
// calendar, not by 24 hours. reading and now: epoch seconds. False (buf empty) without a valid time.
inline bool reading_when(char *buf, size_t n, uint32_t reading, uint32_t now) {
  if (n) buf[0] = '\0';
  time_t r = (time_t) reading, t = (time_t) now;
  struct tm lr, lt;
  if (reading <= 1600000000UL || !localtime_r(&r, &lr) || !localtime_r(&t, &lt)) return false;
  char clk[24];
  clock(clk, sizeof(clk), lr.tm_hour, lr.tm_min);
  const long days = civil_days(lt.tm_year + 1900L, lt.tm_mon + 1, lt.tm_mday) -
                    civil_days(lr.tm_year + 1900L, lr.tm_mon + 1, lr.tm_mday);
  if (days == 0) fill(buf, n, gct::no_reading_when_today, {{"time", clk}});
  else if (days == 1) fill(buf, n, gct::no_reading_when_yesterday, {{"time", clk}});
  else {
    char date[32];
    day_month(date, sizeof(date), lr.tm_mon, lr.tm_mday, false);
    fill(buf, n, gct::no_reading_when_date, {{"date", date}, {"time", clk}});
  }
  return true;
}

// =============================================================================
// Reading times ahead of the clock. One tolerance, the same 5 minutes as GlucoCraft's (the Last reading time sensor's
// time_untrusted): a reading up to 5 minutes ahead of the panel's clock counts as taken now (its age 0) and is stored
// at its own time; further ahead its time cannot be trusted: it is not stored at that time, and the status bar shows
// "Reading: ---" with no time line under the words.
// =============================================================================
constexpr uint32_t FUTURE_TOLERANCE_S = 300;

// How long something has lasted, whole minutes rounded down (as the ages): "45 min", "2 h 5 min", from a day on days
// and hours ("1 d 2 h"), as on the display card
inline void duration_text(char *buf, size_t n, uint32_t seconds) {
  const uint32_t minutes = seconds / 60;
  if (minutes < 60) fill(buf, n, gct::duration_minutes, {{"minutes", minutes}});
  else if (minutes < 1440) fill(buf, n, gct::duration_hours, {{"hours", minutes / 60}, {"minutes", minutes % 60}});
  else fill(buf, n, gct::duration_days, {{"days", minutes / 1440}, {"hours", minutes % 1440 / 60}});
}
// The clock time of an epoch in Home Assistant's time zone ("3:12 AM" / "03:12"); false (buf empty) without one
inline bool clock_at(char *buf, size_t n, uint32_t epoch) {
  if (n) buf[0] = '\0';
  time_t t = (time_t) epoch;
  struct tm lt;
  if (epoch <= 1600000000UL || !localtime_r(&t, &lt)) return false;
  clock(buf, n, lt.tm_hour, lt.tm_min);
  return true;
}
// When something was, within a line of the limit screen or the screen without Home Assistant, by the calendar day in
// Home Assistant's time zone: today the clock time ("3:12 AM"), yesterday "yesterday at 3:12 AM", earlier the date
// ("Oct 22 at 3:12 AM"), as on the display card. Returns the days before today (-1 and buf empty without a valid
// time; now 0, the panel's clock not set: the clock time alone).
inline long day_time_at(char *buf, size_t n, uint32_t epoch, uint32_t now) {
  if (n) buf[0] = '\0';
  time_t e = (time_t) epoch, t = (time_t) now;
  struct tm le, lt;
  if (epoch <= 1600000000UL || !localtime_r(&e, &le)) return -1;
  char clk[24];
  clock(clk, sizeof(clk), le.tm_hour, le.tm_min);
  long days = 0;
  if (now > 1600000000UL && localtime_r(&t, &lt))
    days = civil_days(lt.tm_year + 1900L, lt.tm_mon + 1, lt.tm_mday) -
           civil_days(le.tm_year + 1900L, le.tm_mon + 1, le.tm_mday);
  if (days <= 0) {
    snprintf(buf, n, "%s", clk);
    return 0;
  }
  if (days == 1) {
    fill(buf, n, gct::limit_screen_when_yesterday, {{"time", clk}});
  } else {
    char date[32];
    day_month(date, sizeof(date), le.tm_mon, le.tm_mday, false);
    fill(buf, n, gct::no_reading_when_date, {{"date", date}, {"time", clk}});
  }
  return days;
}

// =============================================================================
// The limit screen. While the current reading is a limit reading (LO / HI), the value's place shows four lines
// instead of a value, modeled on the screen without current data: the zone ("Very low" / "Very high", large, in the
// zone color), "Below sensor range (LO)" / "Above sensor range (HI)", since when the readings have been at the limit
// ("Since 3:12 AM (2 h 5 min)": GlucoCraft's limit_since, the first reading of the current unbroken stretch) and the
// newest measured reading before it ("Last measured: 52 mg/dL at 3:10 AM": GlucoCraft's last_measured_mgdl and
// last_measured_ts, rounded once in the display unit); a time on an earlier day with its day ("Since yesterday at 3:12
// AM (1 d 2 h)", "Last measured: 52 mg/dL, Oct 22 at 3:10 AM"; day_time_at), as on the display card. A line
// GlucoCraft gives no data for stays empty (hidden); while
// the reading times cannot be trusted (ahead of the clock) the lines carry no time. lim: limit() (1 LO, 2 HI); since,
// measured_mgdl, measured_ts: the attributes (NAN for none); now: the panel's clock (0 = not set).
// =============================================================================
constexpr size_t SCREEN_LINE = 96;
inline void limit_screen(char line[][SCREEN_LINE], int lim, double since, double measured_mgdl, double measured_ts,
                         uint32_t now, bool time_ok) {
  for (int i = 0; i < 4; i++) line[i][0] = '\0';
  if (lim == 0) return;
  snprintf(line[0], SCREEN_LINE, "%s", lim == 1 ? gct::limit_screen_low_title : gct::limit_screen_high_title);
  fill(line[1], SCREEN_LINE, lim == 1 ? gct::limit_screen_low_range : gct::limit_screen_high_range,
       {{"limit", limit_text(lim)}});
  char when[48], dur[40];
  if (time_ok && now > 0 && since > 1600000000.0 && (double) now >= since &&
      day_time_at(when, sizeof(when), (uint32_t) since, now) >= 0) {
    duration_text(dur, sizeof(dur), now - (uint32_t) since);
    fill(line[2], SCREEN_LINE, gct::since_line, {{"time", when}, {"duration", dur}});
  }
  if (!std::isnan(measured_mgdl) && measured_mgdl > 0) {
    char v[16];
    fmt_attr(v, sizeof(v), measured_mgdl);
    const long days = time_ok && measured_ts > 1600000000.0
                          ? day_time_at(when, sizeof(when), (uint32_t) measured_ts, now) : -1;
    if (days == 0)
      fill(line[3], SCREEN_LINE, gct::limit_screen_last_measured, {{"value", v}, {"unit", unit()}, {"time", when}});
    else if (days > 0)
      fill(line[3], SCREEN_LINE, gct::limit_screen_last_measured_day, {{"value", v}, {"unit", unit()}, {"when", when}});
    else
      fill(line[3], SCREEN_LINE, gct::limit_screen_last_measured_value, {{"value", v}, {"unit", unit()}});
  }
}

// =============================================================================
// The screen without Home Assistant. After one hour without the panel's connection to Home Assistant (its ESPHome
// API) or without a successful download of GlucoCraft's files, everything below the clock and the date is black
// with four lines: "Cannot reach Home Assistant or load its data", since when ("Since 9:41 PM (1 h 12 min)": whichever
// of the two problems started first; "--:--" while the panel's clock is not set), the last reading with its time (the
// saved reading after a restart; without a trusted time, the reading alone), and what to check. lost_s: how long the
// problem has lasted; now: the panel's clock (0 = not set); mg / lim: the last reading and its LO / HI mark;
// reading_at: its time (0 = none or not trusted).
// =============================================================================
constexpr uint32_t NO_HA_AFTER_S = 3600;   // one hour, the one limit of this screen
inline void no_connection_screen(char line[][SCREEN_LINE], uint32_t lost_s, uint32_t now, float mg, int lim,
                                 uint32_t reading_at) {
  for (int i = 0; i < 4; i++) line[i][0] = '\0';
  snprintf(line[0], SCREEN_LINE, "%s", gct::no_connection_title);
  char clk[48], dur[40];
  if (now == 0 || day_time_at(clk, sizeof(clk), now - lost_s, now) < 0)
    snprintf(clk, sizeof(clk), "%s", gct::header_clock_initial);
  duration_text(dur, sizeof(dur), lost_s);
  fill(line[1], SCREEN_LINE, gct::since_line, {{"time", clk}, {"duration", dur}});
  if (!std::isnan(mg) && mg > 0) {
    char when[24];
    if (reading_at > 0 && clock_at(when, sizeof(when), reading_at)) {
      const char *t = limit_text(lim);
      if (t != nullptr) {
        fill(line[2], SCREEN_LINE, gct::no_connection_last_reading_limit, {{"value", t}, {"time", when}});
      } else {
        char v[16];
        fmt(v, sizeof(v), mg);
        fill(line[2], SCREEN_LINE, gct::no_connection_last_reading, {{"value", v}, {"unit", unit()}, {"time", when}});
      }
    } else {
      last_reading(line[2], SCREEN_LINE, mg, lim);
    }
  }
  snprintf(line[3], SCREEN_LINE, "%s", gct::no_connection_check);
}

// =============================================================================
// The throb of the words without a current reading: for the first 30 minutes of each episode, then they hold still
// and stay on screen for as long as it lasts (a signal that is on all night teaches the eye to skip it). Counted from
// the reading's age, so a restart of the panel does not start it again: an episode without current data begins when
// the newest reading's age passes the no-current-data limit; a warm-up when GlucoCraft saw it begin (its minutes).
// Without a known start (no reading, a reading time ahead of the clock, a warm-up without a start time) the 30 minutes
// count from the moment the panel first showed the episode (shown_s: the seconds since then), as on the display card.
// =============================================================================
constexpr uint32_t THROB_FOR_S = 30 * 60;
inline bool throb_window(bool warm_up, long warm_up_min, bool have_age, uint32_t age_s, uint32_t limit_s,
                         uint32_t shown_s) {
  if (warm_up && warm_up_min >= 0) return (uint32_t) warm_up_min * 60 < THROB_FOR_S;
  if (!warm_up && have_age) return age_s < limit_s + THROB_FOR_S;
  return shown_s < THROB_FOR_S;
}

// =============================================================================
// Gap rule (GlucoCraft's one rule for the graphs, the statistics and "sensor active" - the
// backend's statistics and the dashboard use the same): an interval between two readings longer than
// max(15 min, 3 x the reading interval) is a gap, and every graph line breaks there. The reading interval is decided
// locally from the readings around the interval, exactly as GlucoCraft's gaps.py local_intervals(): the median (upper
// middle) of the 10 intervals before it, filled with the intervals after it when fewer than 10 come before (the start
// of the data), never the interval being judged; intervals of 0 s (the same time twice) do not count; with fewer than
// 3 the 15-minute floor alone applies (GlucoCraft uses the detected interval there; the panels have none). So a
// 1-minute CGM breaks after 15 minutes without data, a 5-minute CGM after 15 minutes, 15-minute history after 45, and
// the rule follows each stretch (1-minute readings resuming after 15-minute history; a second source on another
// interval). The panels apply it to the readings they hold (the files' readings, the 6-hour ring); no file change.
// Why: some sources deliver their history in 15-minute steps (FreeStyle Libre 2, for example), and short sync
// dropouts should not cut the line, while a real outage must show as one.
// =============================================================================
constexpr uint32_t GAP_FLOOR_S = 900;
constexpr int GAP_MEDIAN_N = 10;

// the reading interval (s) around interval k (between readings k-1 and k) of n readings sorted by time; ts(i) gives
// reading i's time. 0 = no interval known (the floor alone applies)
template<typename TS> inline uint32_t reading_interval_at(TS ts, int n, int k) {
  uint32_t iv[GAP_MEDIAN_N];
  int m = 0, slots = 0;   // slots: the intervals taken (the 0-s ones too, as gaps.py), m: those that count
  for (int j = (k - GAP_MEDIAN_N > 1 ? k - GAP_MEDIAN_N : 1); j < k; j++, slots++) {   // the 10 before interval k
    const uint32_t a = ts(j - 1), b = ts(j);
    if (b > a) iv[m++] = b - a;
  }
  for (int j = k + 1; j < n && slots < GAP_MEDIAN_N; j++, slots++) {   // filled with those after it
    const uint32_t a = ts(j - 1), b = ts(j);
    if (b > a) iv[m++] = b - a;
  }
  if (m < 3) return 0;
  for (int i = 1; i < m; i++) {   // insertion sort (at most 10)
    uint32_t v = iv[i];
    int j = i - 1;
    while (j >= 0 && iv[j] > v) { iv[j + 1] = iv[j]; j--; }
    iv[j + 1] = v;
  }
  return iv[m / 2];   // the upper middle, as gaps.py
}

// the gap threshold (s) for interval k: max(15 min, 3 x the reading interval around it)
template<typename TS> inline uint32_t gap_threshold_at(TS ts, int n, int k) {
  const uint32_t r = reading_interval_at(ts, n, k);
  return r * 3 > GAP_FLOOR_S ? r * 3 : GAP_FLOOR_S;
}

// true: the interval between readings k-1 and k is a gap (the line breaks). Up to 15 minutes is never a gap, so the
// median is only needed for the longer intervals.
template<typename TS> inline bool is_gap(TS ts, int n, int k) {
  if (k <= 0 || k >= n) return false;
  const uint32_t a = ts(k - 1), b = ts(k);
  if (b <= a || b - a <= GAP_FLOOR_S) return false;
  return b - a > gap_threshold_at(ts, n, k);
}

}  // namespace gcu
