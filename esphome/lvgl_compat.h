// License: AGPL-3.0-only (LICENSE).
// =============================================================================
// lvgl_compat.h - canvas drawing helpers of the GlucoCraft LCD panels (LVGL 9, ESPHome 2026.4 and newer)
// =============================================================================
// What this file provides, each with its own comment below:
//   - one draw layer per graph (lvc_begin / lvc_end): the graphs' shapes are rendered in one pass, which keeps the
//     drawing of a graph fast, and its texts after all its shapes (no grid line covers a label);
//   - direct RGB565 pixels (lvc_px, lvc_vline, lvc_done): the graph lines and range bars without one canvas
//     invalidation per pixel;
//   - the shapes through the draw layer: lvc_draw_line (one line from points[0] to points[1]), lvc_fill_rect,
//     lvc_draw_text;
//   - GlucoCraft's setting Outline text: the 1-pixel edge of the canvas texts (lvc_draw_text_edged) and of the labels
//     (lvc_label_edge);
//   - the no-current-data throb of a label (lvc_throb; lv_anim_set_reverse_duration: LVGL 9.2 and newer).
// Included by the panel configurations (esphome: includes: lvgl_compat.h).
// =============================================================================

#pragma once

#include <cmath>
#include <string>
#include <vector>

#include "lvgl.h"

// =============================================================================
// One draw layer per graph. Every lvc_fill_rect / lvc_draw_text / lvc_draw_line call on its own opens a draw layer on
// the canvas, renders its one shape and closes the layer again (which also invalidates the whole canvas). A graph has
// several dozen of them. Between lvc_begin(canvas) and lvc_end(canvas) the same calls on that canvas only queue their
// shape on one shared layer; lvc_end() renders them all in one pass, in the order they were queued. The queued shapes
// are allocated by LVGL in PSRAM (the LVGL allocator of ESPHome), not in internal RAM.
// Direct pixels (lvc_px / lvc_vline) write into the canvas buffer at once, so they belong AFTER lvc_end() (a shape
// rendered later would cover them); they do not invalidate the canvas per pixel (lv_canvas_set_px does, which made
// the per-pixel graph lines and range bars slow): call lvc_done(canvas) once when the pixels are written.
// Texts last (the grid lines must never cover part of an axis label): inside a batch, lvc_draw_text only
// notes its text, and lvc_end() queues every noted text after all the batch's shapes, in the order they were drawn.
// So no grid line, border or band of a graph can cover its value labels, time labels, title or legend, whatever order
// a graph draws them in; the positions stay the same. The display card draws its texts after its grid as well.
// =============================================================================
struct LvcText {
  int32_t x, y, w;
  lv_draw_label_dsc_t dsc;
  std::string text;
};
struct LvcBatch {
  lv_obj_t *canvas = nullptr;
  lv_layer_t layer;
  std::vector<LvcText> texts;   // the batch's texts, queued at lvc_end() after its shapes
};
inline LvcBatch &lvc_batch() {
  static LvcBatch b;
  return b;
}
// One line of text on a layer at (x, y) with LV_TEXT_FLAG_EXPAND, so that the box width w never wraps the text and y is
// the top edge of the glyphs; without the flag a narrow box wraps the text and the glyph positions leave the canvas.
inline void lvc_label_on(lv_layer_t *layer, int32_t x, int32_t y, int32_t w, lv_draw_label_dsc_t *dsc,
                         const char *text) {
  dsc->text = text;
  // a queued label keeps only the pointer: let LVGL copy the text, which can be a local buffer that is gone (or
  // reused for the next label) before the layer is rendered
  dsc->text_local = 1;
  // LVGL 9's lv_text_flag_t is strictly typed in C++; need explicit cast.
  dsc->flag = static_cast<lv_text_flag_t>(dsc->flag | LV_TEXT_FLAG_EXPAND);
  const lv_font_t *font = dsc->font ? dsc->font : LV_FONT_DEFAULT;
  int32_t lh = lv_font_get_line_height(font);
  lv_area_t coords = {x, y, x + w - 1, y + lh - 1};
  lv_draw_label(layer, dsc, &coords);
}
// queue the batch's noted texts on its layer (after its shapes) and render the layer
inline void lvc_finish_batch(LvcBatch &b) {
  for (auto &t : b.texts) lvc_label_on(&b.layer, t.x, t.y, t.w, &t.dsc, t.text.c_str());
  lv_canvas_finish_layer(b.canvas, &b.layer);
  b.texts.clear();
  b.canvas = nullptr;
}
inline void lvc_begin(lv_obj_t *canvas) {
  LvcBatch &b = lvc_batch();
  if (b.canvas != nullptr) lvc_finish_batch(b);   // an unfinished batch: render it first
  lv_canvas_init_layer(canvas, &b.layer);
  b.canvas = canvas;
}
inline void lvc_end(lv_obj_t *canvas) {
  LvcBatch &b = lvc_batch();
  if (b.canvas != canvas) return;
  lvc_finish_batch(b);
}
// Labels over direct pixels (bands, grid, data, then the labels, on every graph). lvc_end_shapes() renders the
// batch's shapes and keeps its texts back; the graph then writes its data as direct pixels; lvc_texts() draws the kept
// texts on a layer of their own, over the data. A second lvc_end_shapes() before lvc_texts() first draws the texts
// still kept, so none is lost.
struct LvcKept {
  lv_obj_t *canvas = nullptr;
  std::vector<LvcText> texts;
};
inline LvcKept &lvc_kept() {
  static LvcKept k;
  return k;
}
inline void lvc_texts(lv_obj_t *canvas) {
  LvcKept &k = lvc_kept();
  if (k.canvas != canvas) return;
  lv_layer_t layer;
  lv_canvas_init_layer(canvas, &layer);
  for (auto &t : k.texts) lvc_label_on(&layer, t.x, t.y, t.w, &t.dsc, t.text.c_str());
  lv_canvas_finish_layer(canvas, &layer);
  k.texts.clear();
  k.canvas = nullptr;
}
inline void lvc_end_shapes(lv_obj_t *canvas) {
  LvcBatch &b = lvc_batch();
  if (b.canvas != canvas) return;
  LvcKept &k = lvc_kept();
  if (k.canvas != nullptr) lvc_texts(k.canvas);   // texts still kept from another graph: draw them first
  lv_canvas_finish_layer(canvas, &b.layer);
  k.texts.swap(b.texts);
  b.texts.clear();
  k.canvas = canvas;
  b.canvas = nullptr;
}
// the layer for one shape: the open batch of this canvas, or a layer of its own (own = true: finish it after drawing)
inline lv_layer_t *lvc_layer_for(lv_obj_t *canvas, lv_layer_t *own_layer, bool *own) {
  LvcBatch &b = lvc_batch();
  if (b.canvas == canvas) {
    *own = false;
    return &b.layer;
  }
  lv_canvas_init_layer(canvas, own_layer);
  *own = true;
  return own_layer;
}
inline void lvc_finish_own(lv_obj_t *canvas, lv_layer_t *layer, bool own) {
  if (own) lv_canvas_finish_layer(canvas, layer);
}

// one pixel straight into the canvas buffer (RGB565, the panels' canvas format), no invalidation; outside = ignored
inline void lvc_px(lv_obj_t *canvas, int32_t x, int32_t y, lv_color_t color) {
  lv_draw_buf_t *buf = lv_canvas_get_draw_buf(canvas);
  if (buf == nullptr || x < 0 || y < 0 || x >= (int32_t) buf->header.w || y >= (int32_t) buf->header.h) return;
  if (buf->header.cf != LV_COLOR_FORMAT_RGB565) {   // any other format: LVGL's own (slower) path
    lv_canvas_set_px(canvas, x, y, color, LV_OPA_COVER);
    return;
  }
  lv_color16_t *p = (lv_color16_t *) lv_draw_buf_goto_xy(buf, x, y);
  if (p == nullptr) return;
  p->red = color.red >> 3;
  p->green = color.green >> 2;
  p->blue = color.blue >> 3;
}
// a vertical run of pixels from y0 to y1 (inclusive, either order)
inline void lvc_vline(lv_obj_t *canvas, int32_t x, int32_t y0, int32_t y1, lv_color_t color) {
  if (y0 > y1) { int32_t t = y0; y0 = y1; y1 = t; }
  for (int32_t y = y0; y <= y1; y++) lvc_px(canvas, x, y, color);
}
// the canvas is redrawn on the screen once, after its direct pixels
inline void lvc_done(lv_obj_t *canvas) { lv_obj_invalidate(canvas); }

// One straight line from points[0] to points[1] (LVGL 9's line descriptor takes the two end points; further points
// are not drawn, and every caller passes count = 2). Fewer than two points draw nothing.
inline void lvc_draw_line(lv_obj_t *canvas, lv_point_t *points, uint32_t count,
                          lv_draw_line_dsc_t *dsc) {
  if (count < 2) return;
  lv_layer_t own_layer;
  bool own;
  lv_layer_t *layer = lvc_layer_for(canvas, &own_layer, &own);
  dsc->p1.x = points[0].x;
  dsc->p1.y = points[0].y;
  dsc->p2.x = points[1].x;
  dsc->p2.y = points[1].y;
  lv_draw_line(layer, dsc);
  lvc_finish_own(canvas, layer, own);
}

// Filled rectangle on a canvas through its draw layer: ONE draw call instead of w*h lv_canvas_set_px calls
// (each of which also invalidates the whole canvas - a 480x189 band fill would take ~91,000 of them, and the weekly
// pages would load slowly). opa defaults to cover, an opaque fill; a translucent fill (the graph bands' 60 %) is
// blended over the canvas by the draw layer.
inline void lvc_fill_rect(lv_obj_t *canvas, int32_t x, int32_t y, int32_t w, int32_t h, lv_color_t color,
                          lv_opa_t opa = LV_OPA_COVER) {
  if (w <= 0 || h <= 0) return;
  lv_layer_t own_layer;
  bool own;
  lv_layer_t *layer = lvc_layer_for(canvas, &own_layer, &own);
  lv_draw_rect_dsc_t dsc;
  lv_draw_rect_dsc_init(&dsc);
  dsc.bg_color = color;
  dsc.bg_opa = opa;
  dsc.radius = 0;
  dsc.border_width = 0;
  lv_area_t area = {x, y, x + w - 1, y + h - 1};
  lv_draw_rect(layer, &dsc, &area);
  lvc_finish_own(canvas, layer, own);
}

// One line of text at (x, y) on a canvas (lvc_label_on). Inside a batch of this canvas it is noted and drawn at
// lvc_end(), after the batch's shapes (texts last, above); outside a batch it is drawn at once on a layer of its own.
inline void lvc_draw_text(lv_obj_t *canvas, int32_t x, int32_t y, int32_t w,
                          lv_draw_label_dsc_t *dsc, const char *text) {
  LvcBatch &b = lvc_batch();
  if (b.canvas == canvas) {
    b.texts.push_back(LvcText{x, y, w, *dsc, std::string(text ? text : "")});
    return;
  }
  lv_layer_t own_layer;
  lv_canvas_init_layer(canvas, &own_layer);
  lvc_label_on(&own_layer, x, y, w, dsc, text);
  lv_canvas_finish_layer(canvas, &own_layer);
}

// =============================================================================
// Outline text (GlucoCraft's setting text_outline, off by default): a 1-pixel edge around the graph labels, the
// glucose value, LO / HI and every other text in a zone color, the same as on the display card. The edge is the text
// drawn in the edge color one pixel left, right, up and down, then the text itself on top. The edge color is the
// opposite of the text's own lightness: black around a light text, white around a dark one (whichever has the higher
// WCAG 2.2 contrast ratio with the text).
//   - lvc_outline_text(): the setting in use (the panel sets it from the configuration attribute text_outline).
//   - lvc_draw_text_edged(): canvas text (the graphs' labels), plain while the setting is off.
//   - lvc_label_edge(label): gives an LVGL label the edge. LVGL 9's text outline style (text_outline_stroke_*) works
//     only with vector fonts, and its labels have no text shadow; the panels' fonts are bitmap fonts. So one event
//     callback draws the edge in the label's own draw pass (LV_EVENT_DRAW_MAIN_BEGIN, before the label draws its text
//     at LV_EVENT_DRAW_MAIN): no second label and no extra buffer, one small event entry per label. A label already
//     draws 1/4 of its line height beyond its area (LV_EVENT_REFR_EXT_DRAW_SIZE), so the 1-pixel edge is never cut.
// =============================================================================
inline bool &lvc_outline_text() {
  static bool on = false;
  return on;
}
inline float lvc_channel(uint8_t c8) {
  const float c = c8 / 255.0f;
  return c <= 0.04045f ? c / 12.92f : powf((c + 0.055f) / 1.055f, 2.4f);
}
// the edge color of a text color: white when white contrasts at least as much with it (a dark text), else black (the
// same rule as gc_units.h text_on and the display card)
inline lv_color_t lvc_edge_color(lv_color_t text) {
  const float l = 0.2126f * lvc_channel(text.red) + 0.7152f * lvc_channel(text.green) + 0.0722f * lvc_channel(text.blue);
  return 1.05f / (l + 0.05f) >= (l + 0.05f) / 0.05f ? lv_color_hex(0xFFFFFF) : lv_color_hex(0x000000);
}
// the four one-pixel offsets of the edge
constexpr int8_t LVC_EDGE[4][2] = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};

// Canvas text with the edge of Outline text (plain while it is off)
inline void lvc_draw_text_edged(lv_obj_t *canvas, int32_t x, int32_t y, int32_t w, lv_draw_label_dsc_t *dsc,
                                const char *text) {
  if (lvc_outline_text()) {
    const lv_color_t color = dsc->color;
    dsc->color = lvc_edge_color(color);
    for (const auto &o : LVC_EDGE) lvc_draw_text(canvas, x + o[0], y + o[1], w, dsc, text);
    dsc->color = color;
  }
  lvc_draw_text(canvas, x, y, w, dsc, text);
}

// The edge of a label, drawn in its draw pass while Outline text is on: the label's text, font, alignment and area as
// the label itself draws them (lv_label.c draw_main), in the edge color of its current text color. A label marked
// LV_OBJ_FLAG_USER_1 is drawn without it (a value in gray, not in a zone color: the gray reaches 4.5:1 by itself, and
// the display card edges only zone colors).
inline void lvc_label_edge_cb(lv_event_t *e) {
  if (!lvc_outline_text()) return;
  lv_obj_t *label = lv_event_get_current_target_obj(e);
  if (lv_obj_has_flag(label, LV_OBJ_FLAG_USER_1)) return;
  const char *text = lv_label_get_text(label);
  if (text == nullptr || text[0] == '\0') return;
  lv_layer_t *layer = lv_event_get_layer(e);
  lv_area_t area;
  lv_obj_get_content_coords(label, &area);
  lv_draw_label_dsc_t dsc;
  lv_draw_label_dsc_init(&dsc);
  lv_obj_init_draw_label_dsc(label, LV_PART_MAIN, &dsc);
  dsc.text = text;
  dsc.base.layer = layer;
  if (lv_obj_get_style_width(label, LV_PART_MAIN) == LV_SIZE_CONTENT &&
      lv_obj_get_style_max_width(label, LV_PART_MAIN) == LV_COORD_MAX) {
    dsc.flag = static_cast<lv_text_flag_t>(dsc.flag | LV_TEXT_FLAG_FIT);   // as the label's own text (one line)
  }
  dsc.color = lvc_edge_color(dsc.color);
  for (const auto &o : LVC_EDGE) {
    lv_area_t at = area;
    lv_area_move(&at, o[0], o[1]);
    lv_draw_label(layer, &dsc, &at);
  }
}
// The font for a label's text: big where the text fits the label's width at it, else small (the zone word under the
// value: a long word of a translation takes the smaller size), measured as LVGL draws it
inline const lv_font_t *lvc_font_that_fits(lv_obj_t *label, const char *text, const lv_font_t *small,
                                           const lv_font_t *big) {
  lv_point_t size;
  lv_text_get_size(&size, text, big, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
  return size.x > lv_obj_get_style_width(label, LV_PART_MAIN) ? small : big;
}
// Give a label the edge of Outline text (once per label, at boot)
inline void lvc_label_edge(lv_obj_t *label) { lv_obj_add_event_cb(label, lvc_label_edge_cb, LV_EVENT_DRAW_MAIN_BEGIN, nullptr); }

// =============================================================================
// The throb of the words in the value's place (GlucoCraft's setting "Fade the words when there is no current reading",
// on by default): while there is no current reading, the words ("Sensor warming up", "Sensor expired", "No current
// data") fade smoothly from full brightness to about 30 % and back, one
// cycle every 3 seconds (about 0.33 Hz, far below the three flashes per second of WCAG 2.3.1), the same as on the display
// card; it never disappears. One LVGL animation of the label's text opacity (an ease-in-out fade down in 1.5 s and back
// up in 1 s, repeated), which redraws only the label's own area at LVGL's refresh rate. The Outline text edge follows,
// because lvc_label_edge_cb draws with the label's own text opacity. lvc_throb(label, false) stops it and restores
// full opacity; calling it again with the same state changes nothing.
// =============================================================================
constexpr uint32_t LVC_THROB_HALF_MS = 1500;   // full -> dim, and dim -> full again
constexpr lv_opa_t LVC_THROB_MIN_OPA = 77;      // about 30 % of LV_OPA_COVER (255)
inline void lvc_throb_exec(void *label, int32_t opa) {
  lv_obj_set_style_text_opa(static_cast<lv_obj_t *>(label), static_cast<lv_opa_t>(opa), LV_PART_MAIN);
}
inline void lvc_throb(lv_obj_t *label, bool on) {
  const bool running = lv_anim_get(label, lvc_throb_exec) != nullptr;
  if (on == running) return;
  if (!on) {
    lv_anim_delete(label, lvc_throb_exec);
    lv_obj_set_style_text_opa(label, LV_OPA_COVER, LV_PART_MAIN);
    return;
  }
  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, label);
  lv_anim_set_exec_cb(&a, lvc_throb_exec);
  lv_anim_set_values(&a, LV_OPA_COVER, LVC_THROB_MIN_OPA);
  lv_anim_set_duration(&a, LVC_THROB_HALF_MS);
  lv_anim_set_reverse_duration(&a, LVC_THROB_HALF_MS);
  lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
  lv_anim_start(&a);
}
