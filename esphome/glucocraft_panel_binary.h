// License: AGPL-3.0-only (LICENSE).
// ============================================================================
// GlucoCraft panel: GLU2 file download, parsing and the derived series (PSRAM)
// ============================================================================
// Used by every GlucoCraft panel; the boards need PSRAM (8 MB on the ESP32-S3 N16R8 boards).
//
// Flow: Boot -> HTTP download to PSRAM -> Parse GLU2 -> Populate arrays -> Display
//
// Downloads run in their own FreeRTOS task (backfill_start_download), never on
// the ESPHome main loop: a download of ~800 KB on the loop would trip the 30 s
// task watchdog on a slow network (>= 30 s) and reboot the panel again and
// again. The task downloads into a staging buffer; the loop polls backfill_download_state() and calls
// backfill_commit_download(), which validates the file before swapping it in,
// so a failed or partial download never touches the data on screen.
//
// Binary Format (GLU2 v3; a later version is read by the parts the panel knows, gc_glu2_readable) - written by
// GlucoCraft's panel_feed.py; the full file adds the GCX1 extension after the trends (see GCXStandard below) and both
// files end with the GCR1 block (gc_find_limit_runs), the GCM1 block (gc_read_extremes) and the GCL2 trailer
// (gc_read_limit_time):
//   HEADER:   40 bytes (magic, version, trend_count, offsets, counts, timestamps)
//   STATS:   308 bytes at offset 40  (7 periods x 44 bytes: 10 floats + 2 uint16)
//   RANGES:   24 bytes at offset 348 (6 floats)
//   READINGS:  6 bytes each at offset 372 (uint32 timestamp + uint16 glucose)
//   TRENDS:   48 bytes each (up to 26 weekly trend records after readings)
//
// Stats periods: 6h, 24h, 7d, 14d, 30d, 90d, 180d (7 total)
// Stats fields: mean, std_dev, cv, gmi, tir, titr, time_low, time_high, min, max
// Trend fields: week timestamps + 10 floats (same metrics as stats)
// The mean, SD, CV and percentages of both are unrounded; the CV and percentages are the float32 that rounds as the
// unrounded value (panel_feed.py _pct32), so the panel rounds each once, halves away from zero, with no epsilon
// (gc_units.h pct0 / pct1 / pct_against); the mean and SD in mmol/L: gc_units.h r1_mmol.
//
// Period graphs: average + min/max per pixel slot, by timestamp; the window ends when the file was fetched. Where the
// average line may join the next slot with data: GlucoCraft's gap rule (gc_units.h gcu::is_gap) on the file's readings.
//
// 6h graph: 180-slot ring, one reading per 2-minute clock slot (ts / 120), the newest in the slot wins.
//
// PSRAM budget: 2 x 1024 KB (live + staging buffer, ~25% of 8 MB) + 2 x 8 KB (delta_buf: the delta file downloaded
// with the full file; ds_buf: the 5-minute statistics refresh); panel_feed.py keeps the full file
// within 960 KB (BACKFILL_BUDGET_BYTES), below the 1024 KB buffer.
// ============================================================================

#pragma once

#include "esphome.h"
#include <cstring>
#include <cstdint>
#include <cmath>
#include <ctime>
#include <string>
#include <algorithm>
#include "gc_units.h"   // the gap rule (gcu::is_gap), shared with the panels' own graphs; the texts (gc_text.h)
#include "lvgl_compat.h"   // the graphs' drawing helpers (the marks of the limit stretches, at the end of this file)

// ESP-IDF includes
#include "esp_http_client.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// ============================================================================
// CONFIGURATION
// ============================================================================
// GlucoCraft's wall-panel address: a GET-only, local-only address on the Home Assistant that runs GlucoCraft (the
// panel YAML's ha_host). Its id comes from secrets.yaml via a build flag (glucocraft-panel.yaml esphome: build_flags),
// so no id or token is in this file. GlucoCraft shows the wall-panel address to administrators on its settings screen
// (Configure > Display card and wall panels); the notification that announces a new address leaves the id out
// (panel_feed.py).
#ifndef GC_WEBHOOK_ID
#error "GC_WEBHOOK_ID not set (glucocraft-panel.yaml build_flags, secrets.yaml glucocraft_panel_webhook_id)"
#endif
// The Home Assistant address comes from each panel YAML (substitutions ha_host / ha_port ->
// esphome: build_flags -DGC_HA_BASE), not from this file
#ifndef GC_HA_BASE
#error "GC_HA_BASE not set (the panel YAML: substitutions ha_host / ha_port, esphome: build_flags)"
#endif
#define BACKFILL_URL     GC_HA_BASE "/api/webhook/" GC_WEBHOOK_ID "?file=backfill"
#define DELTA_URL        GC_HA_BASE "/api/webhook/" GC_WEBHOOK_ID "?file=delta"

#define BINARY_MAGIC_GLU2     0x32554C47  // "GLU2" as little-endian uint32
#define GLU2_MIN_VERSION      3           // the first GLU2 version the panel reads (see gc_glu2_readable)
#define BACKFILL_BUFFER_SIZE  (1024 * 1024)  // 1024KB max for full binary
#define DELTA_BUFFER_SIZE     (8 * 1024)    // 8KB max for delta binary

#define HTTP_OP_TIMEOUT_MS    10000   // per connect/read operation
#define HTTP_TOTAL_TIMEOUT_MS 90000   // whole download (normal: ~1.6 s for 800KB)
#define HTTP_READ_CHUNK       4096
#define DL_TASK_STACK         6144    // internal RAM, freed when the task ends

// ============================================================================
// GLU2 BINARY FORMAT - matches GlucoCraft's panel_feed.py (the writer) field by field
// ============================================================================
#pragma pack(push, 1)

// 40-byte header with offset pointers to each section (v3)
struct GLU2Header {
    uint32_t magic;            // "GLU2" = 0x32554C47
    uint16_t version;          // Format version (3)
    uint16_t trend_count;      // Number of weekly trend records
    uint32_t stats_offset;     // Byte offset to stats section
    uint32_t ranges_offset;    // Byte offset to ranges section
    uint32_t readings_offset;  // Byte offset to readings section
    uint32_t reading_count;    // Number of readings
    uint32_t oldest_ts;        // Unix timestamp of oldest reading
    uint32_t newest_ts;        // Unix timestamp of newest reading
    uint32_t trends_offset;    // Byte offset to trends section
    uint32_t reserved;         // GlucoCraft: trend directions, 2 bits per page, bit 31 = present (one system)
};

// A GLU2 file the panel reads: the magic and version 3 or later. A later version is read by the parts the panel
// knows (the header's offsets and the blocks found by their names) and the rest is ignored; an earlier version is
// refused. The display card's rule (glucocraft-shared.js parseGlu2).
static inline bool gc_glu2_readable(const GLU2Header &h) {
    return h.magic == BINARY_MAGIC_GLU2 && h.version >= GLU2_MIN_VERSION;
}

// Per-period stats: 10 float32 + 2 uint16 = 44 bytes each
// 7 periods (6h, 24h, 7d, 14d, 30d, 90d, 180d) = 308 bytes total
// Period indices: 0=6h, 1=24h, 2=7d, 3=14d, 4=30d, 5=90d, 6=180d
struct GLU2PeriodStats {
    float mean;           // mg/dL
    float std_dev;        // mg/dL; NaN: none (a single reading)
    float cv;             // Coefficient of variation (percentage 0-100)
    float gmi;            // Glucose Management Indicator (0 below 14 days or below 70 % data)
    float tir;            // Time in range (the file's RANGES: consensus or custom; percentage 0-100)
    float titr;           // Time in tight range (the file's RANGES; percentage 0-100)
    float time_low;       // Time below range (percentage 0-100)
    float time_high;      // Time above range (percentage 0-100)
    float time_crit_low;  // Time very low, level 2 (percentage 0-100)
    float time_crit_high; // Time very high, level 2 (percentage 0-100)
    uint16_t min_val;     // Minimum glucose in period (whole mg/dL; the unrounded value is in the GCM1 block)
    uint16_t max_val;     // Maximum glucose in period (whole mg/dL; the unrounded value is in the GCM1 block)
};

// Range thresholds: 6 float32 = 24 bytes
struct GLU2Ranges {
    float critical_min;   // Very low (level 2) threshold (mg/dL)
    float critical_max;   // Very high (level 2) threshold (mg/dL)
    float normal_min;     // Normal low threshold (mg/dL)
    float normal_max;     // Normal high threshold (mg/dL)
    float ideal_min;      // Tight range low threshold (mg/dL)
    float ideal_max;      // Tight range high threshold (mg/dL)
};

// Individual reading: 6 bytes each
struct GLU2Reading {
    uint32_t timestamp;   // Unix epoch
    uint16_t value;       // Glucose mg/dL (panel_feed.py writes 1-500 only; the 0 tests here are defensive)
};

// Weekly trend record: 48 bytes each (includes min/max)
// Week 0 = the 14 days ending now, week 1 = the 14 days ending 7 days ago, etc. (each record holds the 14-day values
// at its week's end, as Change Over Time); week_start_ts = week_end_ts - 7 days, used only for the axis labels
struct GLU2TrendWeek {
    uint32_t week_start_ts;  // Unix timestamp of week start
    uint32_t week_end_ts;    // Unix timestamp of week end
    float mean;              // mg/dL
    float std_dev;           // mg/dL; NaN: none (a single reading)
    float cv;                // Coefficient of variation (%)
    float gmi;               // Glucose Management Indicator
    float tir;               // Time in range (the ranges of the standard shown, %)
    float titr;              // Time in tight range (%)
    float time_low;          // Time below range (%)
    float time_high;         // Time above range (%)
    float min_val;           // Minimum glucose in week (mg/dL)
    float max_val;           // Maximum glucose in week (mg/dL)
};

// GlucoCraft GCX1 extension, right after the trend records in the full file: "GCX1", uint16 version,
// uint16 standard_count, then per standard this block; the FIRST is the standard the weekly pages show (its weeks are
// the trend records above). Consensus ranges and targets, or the custom ranges and goals in GlucoCraft's settings.
struct GCXTarget {
    uint8_t op;              // 0 = no target or goal, 1 = >, 2 = >=, 3 = <, 4 = <=
    uint8_t pad[3];
    float value;
};
struct GCXStandard {
    uint8_t kind;            // 0 = consensus, 1 = custom
    uint8_t pad;
    uint16_t week_count;
    uint32_t directions;     // as the header's directions field
    GCXTarget targets[9];    // one per weekly page
    char desc[9][64];        // the line under each weekly page's title (UTF-8, NUL-terminated)
};

#pragma pack(pop)
static_assert(sizeof(GCXStandard) == 656, "GCX1 standard block is 656 bytes");

#define MAX_TREND_WEEKS 26

// ============================================================================
// GLOBAL STATE
// ============================================================================
namespace backfill {
    static uint32_t file_at = 0;               // clock time the live full file was committed (0 = clock not set)
    static uint32_t file_at_ms = 0;            // millis() of that commit (to date it once the clock is set)
    static uint8_t* binary_buf = nullptr;      // Persistent PSRAM buffer for full binary
    static size_t binary_size = 0;             // Actual bytes stored in buffer
    static bool data_loaded = false;
    static GLU2Header header = {};
    static GLU2PeriodStats stats[7] = {};      // 6h, 24h, 7d, 14d, 30d, 90d, 180d
    static GLU2Ranges ranges = {};
    static GLU2TrendWeek trends[MAX_TREND_WEEKS] = {};
    static uint16_t trend_count = 0;
    static bool trends_loaded = false;
    static GCXStandard standard = {};          // the standard the weekly pages show (GCX1 extension)
    static bool standard_loaded = false;
    // each period's seconds at the sensor's limit (LO, HI), from the files' GCL2 trailer (gc_read_limit_time)
    static uint32_t limit_lo_s[7] = {};
    static uint32_t limit_hi_s[7] = {};
    // each period's minimum and maximum as shown (mg/dL): the unrounded GCM1 values, or STATS' whole mg/dL values
    static float stat_min[7] = {};
    static float stat_max[7] = {};
    // the full file's stretches of limit readings (its GCR1 block): where they start in the live buffer, and how many
    static size_t runs_off = 0;
    static uint16_t runs_n = 0;

    // Background download (written by the download task, read by the loop)
    enum DlState { DL_IDLE = 0, DL_RUNNING = 1, DL_OK = 2, DL_FAILED = 3 };
    static volatile int dl_state = DL_IDLE;
    static bool dl_with_delta = false;
    static uint8_t* staging_buf = nullptr;     // download target, swapped with binary_buf on commit
    static size_t staging_size = 0;
    static uint8_t* delta_buf = nullptr;       // last downloaded delta file
    static size_t delta_size = 0;
    static volatile bool delta_ok = false;
    static uint32_t dl_ms = 0;                 // duration of the last full download
    // millis() of the first failed download of a file since the last one that worked (0 = the last one worked): the
    // panel's screen without Home Assistant counts from it
    static volatile uint32_t dl_failing_since_ms = 0;
}

// the outcome of each download of a file (the download tasks): a success clears the failure, the first failure after
// a success notes its time
static void gc_note_download(bool ok) {
    if (ok) backfill::dl_failing_since_ms = 0;
    else if (backfill::dl_failing_since_ms == 0) backfill::dl_failing_since_ms = millis() | 1;   // | 1: never 0
}
/** millis() since which no download of GlucoCraft's files has worked (0 = the last one worked). */
uint32_t backfill_failing_since_ms() { return backfill::dl_failing_since_ms; }

// First reading with timestamp >= ts (readings are sorted by time): the exact window start (a density estimate with
// a margin could miss the start of a window, for example after an import).
static uint32_t gc_first_reading_at_or_after(uint32_t ts) {
    const GLU2Reading* r = (const GLU2Reading*)(backfill::binary_buf + backfill::header.readings_offset);
    uint32_t lo = 0, hi = backfill::header.reading_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (r[mid].timestamp < ts) lo = mid + 1; else hi = mid;
    }
    return lo;
}

// ============================================================================
// HTTP DOWNLOAD - Streams directly into PSRAM buffer
// ============================================================================

/**
 * Download binary from HTTP URL directly into a PSRAM buffer.
 * Blocking - call ONLY from the download task, never from the main loop.
 *
 * @param url        URL to download from
 * @param buf        Pointer to destination buffer (must be pre-allocated)
 * @param buf_size   Output: actual bytes downloaded
 * @param max_size   Maximum bytes the buffer can hold
 * @return           true on success (HTTP 200 and the full Content-Length received)
 */
bool http_download_to_buffer(const char* url, uint8_t* buf, size_t& buf_size, size_t max_size) {
    // only the file name: the wall-panel address contains its private id, which must not reach the logs
    const char* q = strrchr(url, '?');
    ESP_LOGI("backfill", "Downloading GlucoCraft %s", q ? q + 1 : "file");
    buf_size = 0;

    if (!buf) {
        ESP_LOGE("backfill", "Buffer is null");
        return false;
    }

    esp_http_client_config_t cfg = {};
    cfg.url = url;
    cfg.timeout_ms = HTTP_OP_TIMEOUT_MS;

    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        ESP_LOGE("backfill", "HTTP client init failed");
        return false;
    }

    int64_t start_us = esp_timer_get_time();
    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE("backfill", "HTTP open failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return false;
    }

    int content_len = esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    ESP_LOGI("backfill", "HTTP %d, Content-Length: %d", status, content_len);

    bool ok = true;
    if (status != 200) {
        ESP_LOGE("backfill", "HTTP status %d", status);
        ok = false;
    } else if (content_len > 0 && (size_t)content_len > max_size) {
        // Check if content will fit in buffer
        ESP_LOGE("backfill", "Content too large: %d > %u", content_len, max_size);
        ok = false;
    }

    size_t offset = 0;
    while (ok && offset < max_size) {
        size_t want = max_size - offset;
        if (want > HTTP_READ_CHUNK) want = HTTP_READ_CHUNK;
        int len = esp_http_client_read(client, (char*)(buf + offset), want);
        if (len < 0) {
            ESP_LOGE("backfill", "HTTP read error after %u bytes", offset);
            ok = false;
            break;
        }
        if (len == 0) break;  // end of body (or read timeout - caught by the length check)
        offset += len;
        if (content_len > 0 && offset >= (size_t)content_len) break;
        if ((esp_timer_get_time() - start_us) / 1000 > HTTP_TOTAL_TIMEOUT_MS) {
            ESP_LOGE("backfill", "Download took over %d s - giving up at %u bytes",
                     HTTP_TOTAL_TIMEOUT_MS / 1000, offset);
            ok = false;
            break;
        }
    }

    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    uint32_t ms = (uint32_t)((esp_timer_get_time() - start_us) / 1000);
    if (ok && content_len > 0 && offset != (size_t)content_len) {
        ESP_LOGE("backfill", "Incomplete download: %u of %d bytes in %u ms", (unsigned) offset, content_len, (unsigned) ms);
        ok = false;
    }
    if (!ok || offset == 0) return false;

    buf_size = offset;
    ESP_LOGI("backfill", "Downloaded %u bytes in %u ms", (unsigned) offset, (unsigned) ms);
    return true;
}

// ============================================================================
// BINARY PARSING - Uses offset pointers from header
// ============================================================================

// true when a section of count items of item_size bytes at offset lies inside a file of size bytes. The sums are
// 64-bit: in 32 bits (size_t is 32 bits on the ESP32) a damaged header with a huge count or offset wraps around and
// passes, and the panel then reads far outside the 1 MB buffer.
static inline bool gc_section_fits(uint64_t offset, uint64_t count, uint64_t item_size, uint64_t size) {
    return offset <= size && count <= (size - offset) / item_size;
}

// The GCL2 trailer, the last bytes of both files: the time of each period's readings at the sensor's limit, for the
// warning on its graph. Version skew: a file without it has no such time, and a firmware that does not know it ignores
// it.
//   7 x (uint32 LE seconds of LO, uint32 LE seconds of HI)  periods 6 h, 24 h, 7 d, 14 d, 30 d, 90 d, 180 d
//   "GCL2"                                                  the last 4 bytes of the file
// Returns false when the file has no trailer; lo and hi are then zero.
static constexpr size_t GCL2_SIZE = 14 * sizeof(uint32_t) + 4;
static bool gc_read_limit_time(const uint8_t* data, size_t size, uint32_t* lo, uint32_t* hi, int periods) {
    for (int p = 0; p < periods; p++) lo[p] = hi[p] = 0;
    if (size < GCL2_SIZE || memcmp(data + size - 4, "GCL2", 4) != 0) return false;
    const uint8_t* v = data + size - GCL2_SIZE;
    for (int p = 0; p < periods && p < 7; p++) {
        memcpy(&lo[p], v + p * 8, 4);
        memcpy(&hi[p], v + p * 8 + 4, 4);
    }
    return true;
}

// a text cut inside a letter (at most 63 bytes of a 64-byte field): its incomplete UTF-8 sequence is removed, so the
// labels never get a broken letter; GlucoCraft itself always cuts at a whole letter
static void gc_utf8_trim(char* s) {
    size_t n = strlen(s), i = n;
    while (i > 0 && ((uint8_t) s[i - 1] & 0xC0) == 0x80) i--;   // back over the continuation bytes 10xxxxxx
    if (i == 0) return;
    const uint8_t lead = (uint8_t) s[i - 1];
    const size_t need = lead >= 0xF0 ? 4 : lead >= 0xE0 ? 3 : lead >= 0xC0 ? 2 : 1;
    if (n - (i - 1) < need) s[i - 1] = 0;
}

// The GCM1 block, immediately before the GCL2 trailer in both files: each period's minimum and maximum without
// rounding, so that a value in mmol/L is rounded once, when it is shown (STATS holds them as whole mg/dL, and rounding
// those again to 0.1 mmol/L can change the last digit). Version skew: a file without the block reads as STATS' whole
// mg/dL values, and a firmware that does not know the block keeps reading STATS.
//   7 x (float32 minimum, float32 maximum)  mg/dL, periods 6 h, 24 h, 7 d, 14 d, 30 d, 90 d, 180 d (0 = no readings)
//   "GCM1"                                  the block's last 4 bytes
// Returns false when the file has no GCL2 trailer or no GCM1 block in front of it; mins / maxs are then unchanged.
static constexpr size_t GCM1_SIZE = 14 * sizeof(float) + 4;
static bool gc_read_extremes(const uint8_t* data, size_t size, float* mins, float* maxs, int periods) {
    if (size < GCL2_SIZE + GCM1_SIZE || memcmp(data + size - 4, "GCL2", 4) != 0) return false;
    const uint8_t* end = data + size - GCL2_SIZE;         // where the GCL2 trailer starts
    if (memcmp(end - 4, "GCM1", 4) != 0) return false;
    const uint8_t* v = end - GCM1_SIZE;
    for (int p = 0; p < periods && p < 7; p++) {
        memcpy(&mins[p], v + p * 8, 4);
        memcpy(&maxs[p], v + p * 8 + 4, 4);
    }
    return true;
}

// The GCR1 block, immediately before the GCM1 block in both files: the stretches of readings at the sensor's limit
// (the files' readings leave limit readings out, as they have no value), for the marks at a graph's bottom (LO) or
// top (HI) edge. Version skew: a file without the block has no stretches (no marks), and a firmware that does not
// know it still finds GCM1 and GCL2 by their names from the end.
//   uint16 count
//   count x (uint32 start, uint32 end, uint8 kind)  epoch seconds of the stretch's first and last reading; 1 LO, 2 HI
//   "GCR1"                                           the block's last 4 bytes
// The count is at the block's start, so it is found from the end: the first count c whose 2 bytes stand 9 x c bytes
// before the name and hold c, with every stretch valid (a kind of 1 or 2, a start not after its end, real times). A
// smaller c than the real count cannot match: its 2 bytes would be a stretch's last byte of the end and its kind, at
// least 256. Returns false when the file has no block; *off (the first stretch, from data) and *count are then 0.
static constexpr size_t GCR1_RUN_SIZE = 9;
struct GcLimitRun {
    uint32_t start;
    uint32_t end;
    uint8_t kind;            // 1 LO, 2 HI
};
static inline GcLimitRun gc_limit_run_at(const uint8_t* p) {
    GcLimitRun r;
    memcpy(&r.start, p, 4);
    memcpy(&r.end, p + 4, 4);
    r.kind = p[8];
    return r;
}
static bool gc_find_limit_runs(const uint8_t* data, size_t size, size_t* off, uint16_t* count) {
    *off = 0;
    *count = 0;
    if (size < sizeof(GLU2Header) + 6 + GCM1_SIZE + GCL2_SIZE || memcmp(data + size - 4, "GCL2", 4) != 0) return false;
    const uint8_t* gcm_end = data + size - GCL2_SIZE;
    if (memcmp(gcm_end - 4, "GCM1", 4) != 0) return false;
    const uint8_t* name = gcm_end - GCM1_SIZE - 4;          // where "GCR1" starts
    if (memcmp(name, "GCR1", 4) != 0) return false;
    const size_t room = (size_t) (name - data) - sizeof(GLU2Header);   // the block lies after the header
    for (size_t c = 0; c <= 0xFFFF && c * GCR1_RUN_SIZE + 2 <= room; c++) {
        const uint8_t* p = name - c * GCR1_RUN_SIZE - 2;
        uint16_t n;
        memcpy(&n, p, 2);
        if (n != c) continue;
        bool ok = true;
        for (size_t i = 0; i < c && ok; i++) {
            const GcLimitRun r = gc_limit_run_at(p + 2 + i * GCR1_RUN_SIZE);
            ok = (r.kind == 1 || r.kind == 2) && r.start <= r.end && r.start > 1600000000u;
        }
        if (!ok) continue;
        *off = (size_t) (p + 2 - data);
        *count = (uint16_t) c;
        return true;
    }
    return false;
}

bool parse_binary(const uint8_t* data, size_t size) {
    // Validate minimum header size
    if (size < sizeof(GLU2Header)) {
        ESP_LOGE("backfill", "File too small for header: %u bytes", size);
        return false;
    }

    // Read header into a local copy - the live header/stats stay untouched until the
    // file has passed every check (a bad refresh must not damage the data on screen)
    GLU2Header h;
    memcpy(&h, data, sizeof(GLU2Header));

    // Validate magic and version (gc_glu2_readable)
    if (h.magic != BINARY_MAGIC_GLU2) {
        ESP_LOGE("backfill", "Bad magic: 0x%08X (expected 0x%08X)",
                 (unsigned) h.magic, (unsigned) BINARY_MAGIC_GLU2);
        return false;
    }
    if (!gc_glu2_readable(h)) {
        ESP_LOGE("backfill", "GLU2 version %u: version %u or later expected", (unsigned) h.version,
                 (unsigned) GLU2_MIN_VERSION);
        return false;
    }

    // Every section must lie inside the file (64-bit checks, gc_section_fits)
    if (!gc_section_fits(h.stats_offset, 7, sizeof(GLU2PeriodStats), size)) {
        ESP_LOGE("backfill", "Stats section outside the file (offset %u, file %u bytes)", (unsigned) h.stats_offset,
                 (unsigned) size);
        return false;
    }
    if (!gc_section_fits(h.ranges_offset, 1, sizeof(GLU2Ranges), size)) {
        ESP_LOGE("backfill", "Ranges section outside the file (offset %u, file %u bytes)", (unsigned) h.ranges_offset,
                 (unsigned) size);
        return false;
    }
    if (!gc_section_fits(h.readings_offset, h.reading_count, sizeof(GLU2Reading), size)) {
        ESP_LOGE("backfill", "Readings section outside the file (%u readings at offset %u, file %u bytes)",
                 (unsigned) h.reading_count, (unsigned) h.readings_offset, (unsigned) size);
        return false;
    }

    backfill::header = h;

    // Parse stats (7 periods at stats_offset: 6h, 24h, 7d, 14d, 30d, 90d, 180d)
    memcpy(backfill::stats, data + backfill::header.stats_offset,
           sizeof(GLU2PeriodStats) * 7);

    // Parse ranges
    memcpy(&backfill::ranges, data + backfill::header.ranges_offset,
           sizeof(GLU2Ranges));

    // reading times are health data: DEBUG only, like the statistics below; INFO gives the counts
    ESP_LOGI("backfill", "GLU2 v%u: %u readings",
             (unsigned) backfill::header.version, (unsigned) backfill::header.reading_count);
    ESP_LOGD("backfill", "Readings from ts %u to %u",
             (unsigned) backfill::header.oldest_ts, (unsigned) backfill::header.newest_ts);
    // glucose statistics are health data: DEBUG only, so they are not in a log shared at the default level
    ESP_LOGD("backfill", "Stats 6h: mean=%.1f, tir=%.1f%%, cv=%.1f%%",
             backfill::stats[0].mean, backfill::stats[0].tir, backfill::stats[0].cv);
    ESP_LOGD("backfill", "Stats 24h: mean=%.1f, tir=%.1f%%",
             backfill::stats[1].mean, backfill::stats[1].tir);
    ESP_LOGD("backfill", "Ranges: crit=%.0f-%.0f, normal=%.0f-%.0f, ideal=%.0f-%.0f",
             backfill::ranges.critical_min, backfill::ranges.critical_max,
             backfill::ranges.normal_min, backfill::ranges.normal_max,
             backfill::ranges.ideal_min, backfill::ranges.ideal_max);

    // Parse trends section
    backfill::trend_count = 0;
    backfill::trends_loaded = false;
    if (backfill::header.trend_count > 0) {
        uint16_t tc = backfill::header.trend_count;
        if (tc > MAX_TREND_WEEKS) tc = MAX_TREND_WEEKS;

        if (gc_section_fits(backfill::header.trends_offset, tc, sizeof(GLU2TrendWeek), size)) {
            memcpy(backfill::trends, data + backfill::header.trends_offset,
                   tc * sizeof(GLU2TrendWeek));
            backfill::trend_count = tc;
            backfill::trends_loaded = true;
            ESP_LOGI("backfill", "Trends: %u weeks loaded", (unsigned) tc);
        } else {
            ESP_LOGW("backfill", "Trends section outside the file (%u weeks at offset %u, file %u bytes)",
                     (unsigned) tc, (unsigned) backfill::header.trends_offset, (unsigned) size);
        }
    } else {
        ESP_LOGI("backfill", "No trends section (v%u, count=%u)",
                 (unsigned) backfill::header.version, (unsigned) backfill::header.trend_count);
    }

    // the GCL2 trailer: each period's time at the sensor's limit (none without it)
    gc_read_limit_time(data, size, backfill::limit_lo_s, backfill::limit_hi_s, 7);

    // the minimum and maximum: unrounded from the GCM1 block, else the whole mg/dL values of STATS
    for (int p = 0; p < 7; p++) {
        backfill::stat_min[p] = backfill::stats[p].min_val;
        backfill::stat_max[p] = backfill::stats[p].max_val;
    }
    gc_read_extremes(data, size, backfill::stat_min, backfill::stat_max, 7);

    // the stretches of limit readings (GCR1; none in a file without the block)
    gc_find_limit_runs(data, size, &backfill::runs_off, &backfill::runs_n);

    // GCX1 extension: the standard of the weekly pages (description lines, targets or goals; one system)
    backfill::standard_loaded = false;
    if (backfill::header.trends_offset > 0) {
        // the extension follows the whole trends section (all trend_count weeks, also any beyond MAX_TREND_WEEKS)
        const uint64_t xo = (uint64_t) backfill::header.trends_offset +
                            (uint64_t) backfill::header.trend_count * sizeof(GLU2TrendWeek);
        if (gc_section_fits(xo, 1, 8 + sizeof(GCXStandard), size) && memcmp(data + xo, "GCX1", 4) == 0) {
            memcpy(&backfill::standard, data + xo + 8, sizeof(GCXStandard));
            for (int k = 0; k < 9; k++) {
                backfill::standard.desc[k][63] = 0;
                gc_utf8_trim(backfill::standard.desc[k]);
            }
            backfill::standard_loaded = true;
            ESP_LOGI("backfill", "Weekly pages: %s standard", backfill::standard.kind == 1 ? "custom" : "consensus");
        }
    }

    return true;
}

// ============================================================================
// MAIN API
// ============================================================================

static uint8_t* psram_alloc(size_t size, const char* what) {
    uint8_t* p = (uint8_t*)heap_caps_malloc(size, MALLOC_CAP_SPIRAM);
    if (!p) {
        ESP_LOGE("backfill", "PSRAM alloc failed for %s (%u bytes, %u free)", what, size,
                 heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    }
    return p;
}

// Download task: full binary into the staging buffer, then (optionally) the delta.
static void backfill_download_task(void*) {
    bool ok = false;
    int64_t t0 = esp_timer_get_time();
    if (backfill::staging_buf) {
        ok = http_download_to_buffer(BACKFILL_URL, backfill::staging_buf,
                                     backfill::staging_size, BACKFILL_BUFFER_SIZE);
    }
    gc_note_download(ok);
    backfill::dl_ms = (uint32_t)((esp_timer_get_time() - t0) / 1000);

    backfill::delta_ok = false;
    if (ok && backfill::dl_with_delta && backfill::delta_buf) {
        backfill::delta_ok = http_download_to_buffer(DELTA_URL, backfill::delta_buf,
                                                     backfill::delta_size, DELTA_BUFFER_SIZE);
        if (!backfill::delta_ok) {
            ESP_LOGW("backfill", "Delta download failed - gap will fill from live updates");
        }
    }

    ESP_LOGI("backfill", "Download task done: %s, stack headroom %u bytes",
             ok ? "OK" : "FAILED", (unsigned)uxTaskGetStackHighWaterMark(nullptr));
    backfill::dl_state = ok ? backfill::DL_OK : backfill::DL_FAILED;
    vTaskDelete(nullptr);
}

/**
 * Start a background download of the full binary (+ the delta if with_delta).
 * Returns false if a download is already running or the task cannot start.
 * Poll backfill_download_state() from the loop; on DL_OK call backfill_commit_download().
 */
bool backfill_start_download(bool with_delta) {
    if (backfill::dl_state == backfill::DL_RUNNING) {
        ESP_LOGW("backfill", "Download already running");
        return false;
    }
    if (!backfill::staging_buf) {
        backfill::staging_buf = psram_alloc(BACKFILL_BUFFER_SIZE, "staging buffer");
        if (!backfill::staging_buf) { backfill::dl_state = backfill::DL_FAILED; return false; }
    }
    if (with_delta && !backfill::delta_buf) {
        backfill::delta_buf = psram_alloc(DELTA_BUFFER_SIZE, "delta buffer");
    }
    backfill::dl_with_delta = with_delta;
    backfill::delta_ok = false;
    backfill::staging_size = 0;
    backfill::dl_state = backfill::DL_RUNNING;
    // Core 0 (WiFi/lwIP side), priority 1 - the ESPHome loop on core 1 keeps running
    if (xTaskCreatePinnedToCore(backfill_download_task, "bf_download", DL_TASK_STACK,
                                nullptr, 1, nullptr, 0) != pdPASS) {
        ESP_LOGE("backfill", "Download task create failed (%u bytes internal free)",
                 heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
        backfill::dl_state = backfill::DL_FAILED;
        return false;
    }
    ESP_LOGI("backfill", "Download started in background (delta: %s, PSRAM free %u)",
             with_delta ? "yes" : "no", heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    return true;
}

// ----------------------------------------------------------------------------
// 5-min stats refresh: the delta file (~2 KB, every 5 min) carries the same stats as the
// full binary, computed by GlucoCraft (time-weighted, on its ranges, gaps excluded).
// Own task/buffer/state so it never collides with a full download or a history merge.
// ----------------------------------------------------------------------------
#define GC_DELTA_RUNS_MAX 32   // the stretches of limit readings kept from the 5-minute file (it covers 2 hours)
namespace backfill {
    static volatile int ds_state = DL_IDLE;
    static uint8_t* ds_buf = nullptr;
    static size_t ds_size = 0;
    static GcLimitRun delta_runs[GC_DELTA_RUNS_MAX];
    static int delta_runs_n = 0;
}

static void delta_stats_task(void*) {
    bool ok = http_download_to_buffer(DELTA_URL, backfill::ds_buf, backfill::ds_size, DELTA_BUFFER_SIZE);
    gc_note_download(ok);
    backfill::ds_state = ok ? backfill::DL_OK : backfill::DL_FAILED;
    vTaskDelete(nullptr);
}

bool delta_stats_start() {
    if (backfill::ds_state == backfill::DL_RUNNING) return false;
    if (!backfill::ds_buf) {
        backfill::ds_buf = psram_alloc(DELTA_BUFFER_SIZE, "delta stats buffer");
        if (!backfill::ds_buf) return false;
    }
    backfill::ds_state = backfill::DL_RUNNING;
    if (xTaskCreatePinnedToCore(delta_stats_task, "bf_dstats", DL_TASK_STACK, nullptr, 1, nullptr, 0) != pdPASS) {
        backfill::ds_state = backfill::DL_FAILED;
        return false;
    }
    return true;
}

int delta_stats_state() { return backfill::ds_state; }

/**
 * Rebuild the 6 h ring from everything known: the ring's own live readings,
 * the full file and the latest 5-minute delta file. One reading per 2-min slot by TIMESTAMP (the newest wins), so a
 * gap the panel missed (a Home Assistant or Wi-Fi outage) fills in as soon as GlucoCraft has the readings - like the display card.
 * Returns the number of readings in the ring.
 */
int gc_rebuild_6h(int* vals, uint32_t* tss, int* count, int* index, uint32_t now) {
    const int N = 180;
    // 2-minute CLOCK slots (ts / 120): a reading stays in the same slot at every rebuild,
    // the same grid as the live store and the display card; the newest 180 slots up to now
    const uint32_t slot_s = (6 * 3600) / N, last_key = now / slot_s, first_key = last_key >= (uint32_t)(N - 1) ? last_key - (N - 1) : 0;
    const uint32_t start = first_key * slot_s;
    static int sv[180];
    static uint32_t st[180];
    for (int i = 0; i < N; i++) { sv[i] = 0; st[i] = 0; }
    auto put = [&](uint32_t ts, int v) {
        // a reading further ahead of the clock than the one tolerance (gc_units.h FUTURE_TOLERANCE_S) is left out
        if (ts < start || ts > now + gcu::FUTURE_TOLERANCE_S || v <= 0 || v > gcu::GLU_MAX) return;
        uint32_t k = ts / slot_s - first_key; if (k >= (uint32_t)N) k = N - 1;
        if (ts >= st[k]) { st[k] = ts; sv[k] = v; }
    };
    for (int i = 0; i < N; i++) put(tss[i], vals[i]);
    if (backfill::binary_buf && backfill::data_loaded) {
        const GLU2Reading* r = (const GLU2Reading*)(backfill::binary_buf + backfill::header.readings_offset);
        for (uint32_t i = gc_first_reading_at_or_after(start); i < backfill::header.reading_count; i++) put(r[i].timestamp, r[i].value);
    }
    if (backfill::ds_buf && backfill::ds_state != backfill::DL_RUNNING && backfill::ds_size >= sizeof(GLU2Header)) {
        GLU2Header dh;
        memcpy(&dh, backfill::ds_buf, sizeof(GLU2Header));
        if (gc_glu2_readable(dh) && (uint64_t)dh.readings_offset + (uint64_t)dh.reading_count * sizeof(GLU2Reading) <= backfill::ds_size) {
            const GLU2Reading* r = (const GLU2Reading*)(backfill::ds_buf + dh.readings_offset);
            for (uint32_t i = 0; i < dh.reading_count; i++) put(r[i].timestamp, r[i].value);
        }
    }
    int n = 0;
    for (int k = 0; k < N; k++) if (st[k]) { vals[n] = sv[k]; tss[n] = st[k]; n++; }
    for (int k = n; k < N; k++) { vals[k] = 0; tss[k] = 0; }
    *count = n;
    *index = n % N;
    return n;
}

/** The clock was not set at the commit (boot before Home Assistant / SNTP): date the file now. True if it changed. */
bool backfill_fix_file_time() {
    if (!backfill::data_loaded || backfill::file_at != 0) return false;
    time_t t = ::time(nullptr);
    if (t <= 1600000000) return false;
    backfill::file_at = (uint32_t) t - (millis() - backfill::file_at_ms) / 1000;
    return backfill::file_at > backfill::header.newest_ts;
}

/** End of the period windows: when the live full file was fetched; its newest reading if the clock was not set. */
uint32_t backfill_window_end() {
    if (!backfill::data_loaded) return 0;
    return backfill::file_at > backfill::header.newest_ts ? backfill::file_at : backfill::header.newest_ts;
}

/** Copy the 6h and 24h stats from the downloaded delta into the live stats. */
bool delta_stats_apply() {
    if (backfill::ds_state != backfill::DL_OK) return false;
    backfill::ds_state = backfill::DL_IDLE;
    if (!backfill::data_loaded || backfill::ds_size < sizeof(GLU2Header)) return false;
    GLU2Header h;
    memcpy(&h, backfill::ds_buf, sizeof(GLU2Header));
    if (!gc_glu2_readable(h)) return false;
    if ((uint64_t)h.stats_offset + sizeof(GLU2PeriodStats) * 7 > backfill::ds_size) return false;
    // GlucoCraft's delta file carries the full 6 h / 24 h statistics, min / max included (computed from all
    // readings, not just the file's 2 h), so everything is copied - the same numbers the display card shows.
    for (int p = 0; p < 2; p++) {
        GLU2PeriodStats d;
        memcpy(&d, backfill::ds_buf + h.stats_offset + p * sizeof(GLU2PeriodStats), sizeof(d));
        backfill::stats[p] = d;
        backfill::stat_min[p] = d.min_val;
        backfill::stat_max[p] = d.max_val;
    }
    gc_read_extremes(backfill::ds_buf, backfill::ds_size, backfill::stat_min, backfill::stat_max, 2);
    // the time at the sensor's limit of the 6 h and 24 h periods with them (zero when the delta has no trailer)
    gc_read_limit_time(backfill::ds_buf, backfill::ds_size, backfill::limit_lo_s, backfill::limit_hi_s, 2);
    // its stretches of limit readings, copied (the next 5-minute download reuses the buffer): the newest ones
    backfill::delta_runs_n = 0;
    size_t off;
    uint16_t n;
    if (gc_find_limit_runs(backfill::ds_buf, backfill::ds_size, &off, &n)) {
        const int first = n > GC_DELTA_RUNS_MAX ? n - GC_DELTA_RUNS_MAX : 0;
        for (int i = first; i < n; i++)
            backfill::delta_runs[backfill::delta_runs_n++] = gc_limit_run_at(backfill::ds_buf + off + i * GCR1_RUN_SIZE);
    }
    return true;
}

/**
 * The recent-readings file failed (none during an outage of more than 2 hours, or Home Assistant unreachable): the 6 h
 * and 24 h statistics return to the full file's (refreshed hourly), with their minimums, maximums and time at the limit flags,
 * never numbers frozen from before the gap - the display card's rule. True when they were reset.
 */
bool delta_stats_fallback() {
    if (backfill::ds_state != backfill::DL_FAILED) return false;
    backfill::ds_state = backfill::DL_IDLE;
    if (!backfill::data_loaded || !backfill::binary_buf || backfill::binary_size == 0) return false;
    const uint8_t* data = backfill::binary_buf;
    for (int p = 0; p < 2; p++) {
        GLU2PeriodStats f;
        memcpy(&f, data + backfill::header.stats_offset + p * sizeof(GLU2PeriodStats), sizeof(f));
        backfill::stats[p] = f;
        backfill::stat_min[p] = f.min_val;
        backfill::stat_max[p] = f.max_val;
    }
    gc_read_extremes(data, backfill::binary_size, backfill::stat_min, backfill::stat_max, 2);
    gc_read_limit_time(data, backfill::binary_size, backfill::limit_lo_s, backfill::limit_hi_s, 2);
    backfill::delta_runs_n = 0;   // the full file's stretches remain
    return true;
}

int backfill_download_state() { return backfill::dl_state; }
uint32_t backfill_download_ms() { return backfill::dl_ms; }

/**
 * Validate the downloaded file and make it the live data (pointer swap).
 * Call from the loop when backfill_download_state() == DL_OK.
 * Returns false (live data untouched) if the file does not parse.
 */
bool backfill_commit_download() {
    if (backfill::dl_state != backfill::DL_OK) return false;
    backfill::dl_state = backfill::DL_IDLE;
    if (!parse_binary(backfill::staging_buf, backfill::staging_size)) {
        ESP_LOGW("backfill", "Downloaded file rejected - keeping existing data");
        return false;
    }
    uint8_t* old = backfill::binary_buf;
    backfill::binary_buf = backfill::staging_buf;
    backfill::binary_size = backfill::staging_size;
    backfill::staging_buf = old;  // reused by the next download (allocated on demand if null)
    backfill::data_loaded = true;
    {   // the period graphs end here: a gap at the right edge = no data, never shifted labels
        time_t t = ::time(nullptr);
        backfill::file_at = t > 1600000000 ? (uint32_t) t : 0;
        backfill::file_at_ms = millis();
    }
    ESP_LOGI("backfill", "Committed %u bytes (%u readings), download took %u ms",
             (unsigned) backfill::binary_size, (unsigned) backfill::header.reading_count, (unsigned) backfill::dl_ms);
    return true;
}

/**
 * Load most recent readings from PSRAM buffer into the display's graph arrays.
 *
 * Writes directly into the display's int[180] glucose_history and
 * uint32_t[180] glucose_history_ts arrays.
 * Readings are loaded oldest-first (index 0 = oldest, index N-1 = newest).
 * Timestamps are stored alongside values for time-accurate graph rendering.
 *
 * @param val_array   Pointer to the display's glucose_history int[180] array
 * @param ts_array    Pointer to the display's glucose_history_ts uint32_t[180] array
 * @param max_count   Maximum readings to load (180)
 * @return            Number of readings loaded (0 on failure)
 */
int backfill_load(int* val_array, uint32_t* ts_array, int max_count) {
    if (!backfill::binary_buf || backfill::binary_size == 0) {
        ESP_LOGE("backfill", "No data in PSRAM buffer");
        return 0;
    }

    // Header, stats, and ranges are already parsed (and logged) by backfill_commit_download()
    vTaskDelay(pdMS_TO_TICKS(10));  // Yield to watchdog

    ESP_LOGD("backfill", "Stats 24h: mean=%.1f, tir=%.1f%%",   // health data: DEBUG only
             backfill::stats[1].mean, backfill::stats[1].tir);

    vTaskDelay(pdMS_TO_TICKS(10));  // Yield to watchdog

    // --- Load 6 hours of readings, downsampled to max_count ---
    uint32_t total_readings = backfill::header.reading_count;
    uint32_t newest_ts = backfill::header.newest_ts;
    uint32_t window_6h = 6 * 3600;  // 6 hours in seconds
    uint32_t window_start = (newest_ts > window_6h) ? (newest_ts - window_6h) : 0;

    // Exact start of the 6 h window: binary search on the timestamps (no density estimate)
    uint32_t start_reading = gc_first_reading_at_or_after(window_start);

    uint32_t readings_in_window = total_readings - start_reading;

    // Point directly into the PSRAM buffer - no copy needed
    const GLU2Reading* readings = (const GLU2Reading*)(backfill::binary_buf +
                                   backfill::header.readings_offset +
                                   start_reading * sizeof(GLU2Reading));

    // Filter to only readings within the 6h window
    // Count valid readings first to calculate downsample step
    uint32_t valid_in_window = 0;
    for (uint32_t i = 0; i < readings_in_window; i++) {
        if (readings[i].timestamp >= window_start && readings[i].timestamp <= newest_ts
            && readings[i].value > 0 && readings[i].value <= gcu::GLU_MAX) {
            valid_in_window++;
        }
    }

    // By TIMESTAMP: the 6 h window is max_count equal time slots (2 min each for 180); one reading per slot, gaps
    // stay empty, the newest reading is always kept. (Keeping the newest max_count readings instead would show only
    // the last ~3 h of the graph with ~1-minute data.)
    const uint32_t slot_s = window_6h / (uint32_t)max_count;

    ESP_LOGI("backfill", "6h load: %u readings in window, one per %u s slot by timestamp, target=%d",
             (unsigned) valid_in_window, (unsigned) slot_s, max_count);

    int n = 0;
    int64_t last_slot = -1;  // time slot of the last kept reading

    for (uint32_t i = 0; i < readings_in_window && n < max_count; i++) {
        uint32_t ts = readings[i].timestamp;
        uint16_t v = readings[i].value;

        // Skip readings outside window or invalid
        if (ts < window_start || ts > newest_ts) continue;
        if (v == 0 || v > gcu::GLU_MAX) continue;

        int64_t slot = (int64_t)((ts - window_start) / slot_s);
        if (slot == last_slot) {
            // same 2-minute slot: the newest reading of the slot wins (replaces the one kept before)
            if (n > 0) {
                val_array[n - 1] = (int)v;
                if (ts_array) ts_array[n - 1] = ts;
            }
            continue;
        }
        last_slot = slot;

        val_array[n] = (int)v;
        if (ts_array) ts_array[n] = ts;
        n++;
    }

    backfill::data_loaded = true;

    // counts only: glucose values are health data (DEBUG at most), not in a log shared at the default level
    ESP_LOGI("backfill", "Loaded %d of %u readings", n, (unsigned) total_readings);
    return n;
}

/**
 * Download delta file and merge any readings newer than the full binary.
 *
 * The delta file (updated every 5 min) covers the last 2 hours.
 * After loading the full binary (updated hourly), this fills the gap
 * between the full file's newest reading and "now".
 *
 * New readings are appended to the array, shifting older values left
 * if the array is full (maintaining the 180-reading window).
 * Timestamps are maintained alongside values for time-accurate graph rendering.
 *
 * @param val_array      Pointer to the display's glucose_history int[180] array
 * @param ts_array       Pointer to the display's glucose_history_ts uint32_t[180] array
 * @param current_count  Number of readings currently in the array
 * @param max_count      Maximum array size (180)
 * @return               Number of new readings merged
 */
int backfill_load_delta(int* val_array, uint32_t* ts_array, int current_count, int max_count) {
    if (!backfill::binary_buf || backfill::binary_size == 0) return 0;

    // The delta was downloaded by the download task together with the full binary
    if (!backfill::delta_ok || !backfill::delta_buf) return 0;
    uint8_t* delta_buf = backfill::delta_buf;
    size_t delta_size = backfill::delta_size;
    backfill::delta_ok = false;  // merge each download once

    // Parse delta header (same GLU2 format)
    if (delta_size < sizeof(GLU2Header)) return 0;

    GLU2Header delta_hdr;
    memcpy(&delta_hdr, delta_buf, sizeof(GLU2Header));

    if (!gc_glu2_readable(delta_hdr)) {
        ESP_LOGW("backfill", "Delta file has a bad magic or version");
        return 0;
    }

    // Validate readings section
    if (!gc_section_fits(delta_hdr.readings_offset, delta_hdr.reading_count, sizeof(GLU2Reading), delta_size)) {
        ESP_LOGW("backfill", "Delta file truncated");
        return 0;
    }

    // Find readings newer than the full binary's newest timestamp
    const GLU2Reading* readings = (const GLU2Reading*)(delta_buf + delta_hdr.readings_offset);
    uint32_t cutoff = backfill::header.newest_ts;

    int added = 0;

    // Delta readings are oldest-first, so iterate forward
    for (uint32_t i = 0; i < delta_hdr.reading_count; i++) {
        if (readings[i].timestamp <= cutoff) continue;  // Already have this one

        uint16_t v = readings[i].value;
        int glucose = (v > 0 && v <= gcu::GLU_MAX) ? (int)v : 0;

        if (current_count < max_count) {
            // Array not full yet - just append
            val_array[current_count] = glucose;
            if (ts_array) ts_array[current_count] = readings[i].timestamp;
            current_count++;
        } else {
            // Array full - shift everything left by 1, add at end
            for (int j = 0; j < max_count - 1; j++) {
                val_array[j] = val_array[j + 1];
                if (ts_array) ts_array[j] = ts_array[j + 1];
            }
            val_array[max_count - 1] = glucose;
            if (ts_array) ts_array[max_count - 1] = readings[i].timestamp;
        }

        added++;
    }

    if (added > 0) {
        ESP_LOGI("backfill", "Delta merged %d new readings", added);
    } else {
        ESP_LOGI("backfill", "Delta had no new readings");
    }
    ESP_LOGD("backfill", "Delta cutoff ts=%u", (unsigned) cutoff);   // a reading time: DEBUG only

    return added;
}

// ============================================================================
// DOWNSAMPLED PERIOD LOADING (for multi-period graph display)
// Reads from PSRAM buffer, averages readings into canvas-width slots
// ============================================================================

/**
 * Load downsampled readings for a specific time period from the PSRAM buffer.
 * Divides the time window into canvas_width equal slots.
 * Each slot's value = average of all readings in that slot.
 *
 * @param val_array    Output: int[canvas_width] averaged values (0 = no data in slot)
 * @param canvas_width Number of output points (the canvas pixel width: 340 on the square panel, the layout's own width on the others)
 * @param period_hours Time window in hours (24, 168, 336, 720, 2160, 4320)
 * @param join_out     Output (optional): 1 = the average line joins this slot to the next slot with data, 0 = a gap
 *                     lies between them (GlucoCraft's gap rule, gc_units.h: an interval longer than
 *                     max(15 min, 3 x the reading interval around it))
 * @return             Number of valid (non-zero) output points
 */
int backfill_load_period(int* val_array, int canvas_width,
                         int period_hours,
                         int* slot_min_out = nullptr, int* slot_max_out = nullptr, uint8_t* join_out = nullptr) {
    // Initialize output arrays to zero
    for (int i = 0; i < canvas_width; i++) {
        val_array[i] = 0;
        if (slot_min_out) slot_min_out[i] = 0;
        if (slot_max_out) slot_max_out[i] = 0;
        if (join_out) join_out[i] = 1;
    }

    if (!backfill::binary_buf || !backfill::data_loaded) {
        ESP_LOGW("backfill", "Period load: no data available");
        return 0;
    }

    // Use the already-parsed header for timestamps and offsets
    uint32_t newest_ts = backfill_window_end();   // window ends when the file was fetched
    uint32_t window_seconds = (uint32_t)period_hours * 3600;
    uint32_t window_start = (newest_ts > window_seconds) ? (newest_ts - window_seconds) : 0;
    uint32_t total_readings = backfill::header.reading_count;

    // Exact start of the time window: binary search on the timestamps
    uint32_t start_reading = gc_first_reading_at_or_after(window_start);
    ESP_LOGI("backfill", "Period %dh: window starts at reading %u of %u",
             period_hours, (unsigned) start_reading, (unsigned) total_readings);

    uint32_t readings_to_scan = total_readings - start_reading;

    // Allocate accumulator arrays: track min, max, and sum per slot
    int* slot_min = (int*)calloc(canvas_width, sizeof(int));
    int* slot_max = (int*)calloc(canvas_width, sizeof(int));
    int* slot_sum = (int*)calloc(canvas_width, sizeof(int));
    int* slot_count = (int*)calloc(canvas_width, sizeof(int));
    if (!slot_min || !slot_max || !slot_sum || !slot_count) {
        ESP_LOGE("backfill", "Period load: Cannot allocate accumulators");
        if (slot_min) free(slot_min);
        if (slot_max) free(slot_max);
        if (slot_sum) free(slot_sum);
        if (slot_count) free(slot_count);
        return 0;
    }
    for (int i = 0; i < canvas_width; i++) {
        slot_min[i] = 999;
        slot_max[i] = 0;
    }

    // Point directly into PSRAM buffer - no file I/O or chunk reads needed
    const GLU2Reading* readings = (const GLU2Reading*)(backfill::binary_buf +
                                   backfill::header.readings_offset +
                                   start_reading * sizeof(GLU2Reading));

    // the gap rule over all of the file's readings (the intervals before the window count for its first readings)
    const GLU2Reading* all = (const GLU2Reading*)(backfill::binary_buf + backfill::header.readings_offset);
    auto ts_at = [all](int k) -> uint32_t { return all[k].timestamp; };
    const int n_all = (int) total_readings;
    int prev_slot = -1;   // the slot of the previous reading in the window

    for (uint32_t i = 0; i < readings_to_scan; i++) {
        uint32_t ts = readings[i].timestamp;
        uint16_t v = readings[i].value;

        // Skip readings outside the time window
        if (ts < window_start || ts > newest_ts) continue;
        // Skip invalid values
        if (v == 0 || v > gcu::GLU_MAX) continue;

        // Calculate which slot this reading belongs to
        int slot = (int)(((uint64_t)(ts - window_start) * (uint64_t)canvas_width) / window_seconds);
        if (slot < 0) slot = 0;
        if (slot >= canvas_width) slot = canvas_width - 1;

        // a gap between the previous reading and this one, across slots: the line does not join the two slots (a
        // gap inside one slot is not visible at this width; the slot's minimum-to-maximum bar covers it)
        if (join_out && prev_slot >= 0 && slot != prev_slot && gcu::is_gap(ts_at, n_all, (int)(start_reading + i)))
            join_out[prev_slot] = 0;
        prev_slot = slot;

        int iv = (int)v;
        slot_sum[slot] += iv;
        slot_count[slot]++;
        if (iv < slot_min[slot]) slot_min[slot] = iv;
        if (iv > slot_max[slot]) slot_max[slot] = iv;

        // Yield to watchdog every ~512 readings
        if (i > 0 && i % 512 == 0) {
            vTaskDelay(pdMS_TO_TICKS(2));
        }
    }

    // Average per slot for the main data line, rounded to the whole mg/dL (halves up; a plain integer division would
    // truncate it up to 1 mg/dL low); per slot also the lowest and highest reading, which the graph draws as one
    // vertical line per pixel column, as the display card does.
    int valid_count = 0;
    for (int i = 0; i < canvas_width; i++) {
        if (slot_count[i] > 0) {
            val_array[i] = (slot_sum[i] + slot_count[i] / 2) / slot_count[i];
            if (slot_min_out) slot_min_out[i] = slot_min[i];
            if (slot_max_out) slot_max_out[i] = slot_max[i];
            valid_count++;
        }
        // val_array[i] remains 0 if no readings in this slot
    }

    free(slot_min);
    free(slot_max);
    free(slot_sum);
    free(slot_count);

    // counts only: glucose values are health data (DEBUG at most), not in a log shared at the default level
    ESP_LOGI("backfill", "Period %dh: %d valid slots of %d, scanned %u of %u readings",
             period_hours, valid_count, canvas_width,
             (unsigned) readings_to_scan, (unsigned) total_readings);

    return valid_count;
}

// ============================================================================
// STATISTICS ACCESS (from parsed GLU2 stats section)
// Period indices: 0=6h, 1=24h, 2=7d, 3=14d, 4=30d, 5=90d, 6=180d
//
// All percentages returned as 0-100, as GlucoCraft's statistics sensors publish them
// Example: get_stat_tir(0) returns 82.5 meaning 82.5% (6 h)
// ============================================================================

bool backfill_has_stats() { return backfill::data_loaded; }

// Generic access by period index
float get_stat_mean(int p)       { return backfill::stats[p].mean; }
float get_stat_stddev(int p)     { return backfill::stats[p].std_dev; }
float get_stat_cv(int p)         { return backfill::stats[p].cv; }
float get_stat_gmi(int p)        { return backfill::stats[p].gmi; }
float get_stat_tir(int p)        { return backfill::stats[p].tir; }
float get_stat_titr(int p)       { return backfill::stats[p].titr; }
float get_stat_time_low(int p)       { return backfill::stats[p].time_low; }
float get_stat_time_high(int p)      { return backfill::stats[p].time_high; }
float get_stat_time_crit_low(int p)  { return backfill::stats[p].time_crit_low; }
float get_stat_time_crit_high(int p) { return backfill::stats[p].time_crit_high; }
float get_stat_min(int p)            { return backfill::stat_min[p]; }   // mg/dL, unrounded where the file has GCM1
float get_stat_max(int p)            { return backfill::stat_max[p]; }
// the period's seconds at the sensor's limit (LO, HI), from the GCL2 trailer
uint32_t get_stat_limit_lo(int p)    { return p >= 0 && p < 7 ? backfill::limit_lo_s[p] : 0; }
uint32_t get_stat_limit_hi(int p)    { return p >= 0 && p < 7 ? backfill::limit_hi_s[p] : 0; }
// GlucoCraft shows a period's statistics only when the sensor covered at least 70 % of it (a GlucoCraft rule for
// every period length); below that GlucoCraft writes the period's STATS block as all zeros, and every statistic of the
// period shows "---" (the graphs, the current reading and the time at the limit are not affected). A period with
// readings at the sensor's limit alone is not all zeros: its times in the very low or very high range are above zero.
bool get_stat_withheld(int p) {
    if (p < 0 || p >= 7) return true;
    const GLU2PeriodStats& s = backfill::stats[p];
    return s.mean == 0 && s.cv == 0 && s.gmi == 0 && s.tir == 0 && s.titr == 0 && s.time_low == 0 &&
           s.time_high == 0 && s.time_crit_low == 0 && s.time_crit_high == 0 && s.min_val == 0 && s.max_val == 0 &&
           (s.std_dev == 0 || std::isnan(s.std_dev));
}
// a period whose statistics are shown: not withheld, with readings (a mean) or with readings at the limit alone
bool get_stat_shown(int p) {
    return !get_stat_withheld(p) && (backfill::stats[p].mean > 0 || get_stat_limit_lo(p) > 0 || get_stat_limit_hi(p) > 0);
}

// ============================================================================
// WEEKLY TREND ACCESS
// Week 0 = most recent, Week N = N weeks ago
// ============================================================================
bool backfill_has_trends()      { return backfill::trends_loaded; }
/** GlucoCraft's direction for trend page pg (one system): header field, 2 bits per page, bit 31 = present; the
 *  words in the panel's language (gc_text.h). */
const char* backfill_trend_direction(int pg) {
    uint32_t f = backfill::header.reserved;
    if (!(f >> 31) || pg < 0 || pg > 8) return nullptr;
    switch ((f >> (2 * pg)) & 3) {
        case 1: return gct::weekly_direction_increasing;
        case 2: return gct::weekly_direction_decreasing;
        case 3: return gct::weekly_direction_stable;
        default: return nullptr;
    }
}
int backfill_trend_count()      { return backfill::trend_count; }
/** The weekly pages' standard from the file: true = custom ranges and goals, false = consensus (or no extension). */
bool backfill_trend_custom()    { return backfill::standard_loaded && backfill::standard.kind == 1; }
/** The line under weekly page pg's title, from the file; nullptr = the file has none (the page shows its title alone). */
const char* backfill_trend_desc(int pg) {
    if (!backfill::standard_loaded || pg < 0 || pg > 8 || !backfill::standard.desc[pg][0]) return nullptr;
    return backfill::standard.desc[pg];
}
/** Weekly page pg's target or goal: -1 = not in the file (no line drawn), 0 = none, 1 >, 2 >=, 3 <, 4 <=. */
int backfill_trend_target(int pg, float* value) {
    if (!backfill::standard_loaded || pg < 0 || pg > 8) return -1;
    *value = backfill::standard.targets[pg].value;
    return backfill::standard.targets[pg].op <= 4 ? backfill::standard.targets[pg].op : 0;
}

// A week GlucoCraft sends EMPTY (mean 0: no data that week) reads as NAN for every metric = a gap
static inline bool gc_week_ok(int w) { return w >= 0 && w < backfill::trend_count && backfill::trends[w].mean > 0; }
float get_trend_mean(int w)     { return gc_week_ok(w) ? backfill::trends[w].mean : NAN; }
float get_trend_std_dev(int w)  { return gc_week_ok(w) ? backfill::trends[w].std_dev : NAN; }
float get_trend_cv(int w)       { return gc_week_ok(w) ? backfill::trends[w].cv : NAN; }
float get_trend_gmi(int w)      { return gc_week_ok(w) ? backfill::trends[w].gmi : NAN; }
float get_trend_tir(int w)      { return gc_week_ok(w) ? backfill::trends[w].tir : NAN; }
float get_trend_titr(int w)     { return gc_week_ok(w) ? backfill::trends[w].titr : NAN; }
float get_trend_time_low(int w) { return gc_week_ok(w) ? backfill::trends[w].time_low : NAN; }
float get_trend_time_high(int w){ return gc_week_ok(w) ? backfill::trends[w].time_high : NAN; }
float get_trend_min(int w)      { return gc_week_ok(w) ? backfill::trends[w].min_val : NAN; }
float get_trend_max(int w)      { return gc_week_ok(w) ? backfill::trends[w].max_val : NAN; }
uint32_t get_trend_start_ts(int w) { return (w < backfill::trend_count) ? backfill::trends[w].week_start_ts : 0; }

// ============================================================================
// LIMIT STRETCHES ON THE GRAPHS
// A stretch of readings at the sensor's limit (LO / HI) has no value: the graph line breaks there, and instead of a
// point a run of small marks along the very bottom (LO) or the very top (HI) of the graph area marks it, in the
// very-low / very-high range color (GlucoCraft's zone colors, the colors of the value), over a black strip so that it
// shows on every band, with the letters LO / HI once per stretch, as the graph's other labels. Never a number and
// never at a value; the warning line under the title stays. The display card draws the same marks at the same sizes
// (glucocraft-shared.js gcLimitMarks); the CGM Report draws them in its daily profiles. A stretch differs from a gap
// (no data): a gap has no marks.
// ============================================================================
/**
 * The stretches that overlap the window [from, to] (epoch seconds): the full file's, with recent also the 5-minute
 * file's, and live (nullptr = none: the stretch of the current limit readings, from GlucoCraft's limit_since to now);
 * sorted by their start, and the copies of one stretch (the same kind, overlapping) joined into one. Returns how many
 * were written to out (at most max: the newest are kept).
 */
int gc_limit_runs(uint32_t from, uint32_t to, GcLimitRun* out, int max, bool recent, const GcLimitRun* live) {
    int n = 0;
    auto add = [&](const GcLimitRun& r) {
        if (r.end < from || r.start > to || max <= 0) return;
        if (n < max) { out[n++] = r; return; }
        int oldest = 0;
        for (int i = 1; i < n; i++) if (out[i].start < out[oldest].start) oldest = i;
        if (r.start > out[oldest].start) out[oldest] = r;
    };
    if (backfill::binary_buf && backfill::data_loaded && backfill::runs_n > 0 &&
        backfill::runs_off + (size_t) backfill::runs_n * GCR1_RUN_SIZE <= backfill::binary_size) {
        for (int i = 0; i < backfill::runs_n; i++)
            add(gc_limit_run_at(backfill::binary_buf + backfill::runs_off + i * GCR1_RUN_SIZE));
    }
    if (recent) for (int i = 0; i < backfill::delta_runs_n; i++) add(backfill::delta_runs[i]);
    if (live != nullptr) add(*live);
    for (int i = 1; i < n; i++) {   // insertion sort by the start
        const GcLimitRun v = out[i];
        int j = i - 1;
        while (j >= 0 && out[j].start > v.start) { out[j + 1] = out[j]; j--; }
        out[j + 1] = v;
    }
    int m = 0;
    for (int i = 0; i < n; i++) {
        if (m > 0 && out[m - 1].kind == out[i].kind && out[i].start <= out[m - 1].end) {
            if (out[i].end > out[m - 1].end) out[m - 1].end = out[i].end;
        } else {
            out[m++] = out[i];
        }
    }
    return m;
}

// a text's box on a graph as the graphs draw it: from x, or right-aligned in [x, x + w]
struct GcBox { int32_t x0, y0, x1, y1; };
static GcBox gc_text_box(int32_t x, int32_t y, int32_t w, const char* text, const lv_font_t* font, bool right) {
    lv_point_t s;
    lv_text_get_size(&s, text, font, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    const int32_t x0 = right ? x + w - s.x : x;
    return GcBox{x0, y, x0 + s.x, y + s.y};
}
// true when a limit stretch lies between the times a and b (the graph line between two readings breaks there)
static bool gc_crosses_run(const GcLimitRun* runs, int n, uint32_t a, uint32_t b) {
    for (int i = 0; i < n; i++)
        if (runs[i].start <= b && runs[i].end >= a) return true;
    return false;
}
// the column of a time on a graph cw px wide showing win_s seconds from win_start
static int32_t gc_time_x(uint32_t ts, uint32_t win_start, uint32_t win_s, int32_t cw) {
    if (ts <= win_start || win_s == 0) return 0;
    const int64_t x = ((int64_t) (ts - win_start) * cw) / win_s;
    return x >= cw ? cw - 1 : (int32_t) x;
}
// The sizes of the marks, the same on every panel and on the display card (glucocraft-shared.js LIMIT_MARK): a black
// strip 8 px tall at the edge, marks 2 px wide and 6 px tall every 4 px on it (1 px of black around them), stretches
// of one kind closer than 3 px drawn as one. No letters and no text: the screen in the value's place names a limit
// reading while it lasts, and the edge (bottom: below the sensor's range, top: above it) and the color tell the side.
static constexpr int32_t GC_MARK_STRIP = 8, GC_MARK_H = 6, GC_MARK_W = 2, GC_MARK_STEP = 4, GC_MARK_MERGE = 3;
/**
 * The marks of the stretches on a graph (cw x ch px; the window win_s seconds from win_start; runs sorted by their
 * start, as gc_limit_runs gives them): after its data, as direct pixels (lvgl_compat.h), so over the line and under
 * the graph's labels, which the caller draws after them (lvc_texts).
 */
static void gc_draw_limit_marks(lv_obj_t* cv, const GcLimitRun* runs, int n, uint32_t win_start, uint32_t win_s,
                                int32_t cw, int32_t ch) {
    if (n <= 0) return;
    // the stretches in pixels: [x0, x1] and the kind (1 LO, 2 HI); one of a kind closer than 3 px to the one before
    // it is joined to it
    static int32_t sx0[96], sx1[96];
    static uint8_t sk[96];
    int m = 0;
    for (int i = 0; i < n && m < 96; i++) {
        const uint8_t k = runs[i].kind == 2 ? 2 : 1;
        int32_t x0 = gc_time_x(runs[i].start, win_start, win_s, cw);
        if (x0 > cw - GC_MARK_W) x0 = cw - GC_MARK_W;
        if (x0 < 0) x0 = 0;
        const int32_t x1 = gc_time_x(runs[i].end, win_start, win_s, cw);
        int last = -1;
        for (int j = m - 1; j >= 0; j--) if (sk[j] == k) { last = j; break; }
        if (last >= 0 && x0 - sx1[last] < GC_MARK_MERGE) {
            if (x1 > sx1[last]) sx1[last] = x1;
        } else {
            sx0[m] = x0;
            sx1[m] = x1 > x0 + GC_MARK_W ? x1 : x0 + GC_MARK_W;
            sk[m++] = k;
        }
    }
    const lv_color_t black = lv_color_hex(0x000000);
    const lv_color_t color[3] = {black, lv_color_hex(gcu::zone_color(gcu::Z_VERY_LOW)),
                                 lv_color_hex(gcu::zone_color(gcu::Z_VERY_HIGH))};
    for (int i = 0; i < m; i++) {
        const bool low = sk[i] == 1;
        const int32_t strip_y = low ? ch - GC_MARK_STRIP : 0;
        const int32_t mark_y = low ? ch - 1 - GC_MARK_H : 1;
        const int32_t a = sx0[i] - 1 > 0 ? sx0[i] - 1 : 0, b = sx1[i] + 1 < cw ? sx1[i] + 1 : cw;
        for (int32_t x = a; x < b; x++) lvc_vline(cv, x, strip_y, strip_y + GC_MARK_STRIP - 1, black);
        for (int32_t x = sx0[i]; x + GC_MARK_W <= sx1[i]; x += GC_MARK_STEP)
            for (int32_t dx = 0; dx < GC_MARK_W; dx++) lvc_vline(cv, x + dx, mark_y, mark_y + GC_MARK_H - 1, color[sk[i]]);
    }
    lvc_done(cv);
}
