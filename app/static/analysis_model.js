"use strict";
// The analysis view model: reads the analysis-v1 document the standalone endpoint returns as
// `document`, and nothing else. Pure (no DOM). Every path the page reads is listed here and
// tests/test_ui_fields.py checks the list against a real document, so a renamed field fails the
// test rather than rendering a guess. "optional" groups are the ones the analyser withholds
// until their gates pass (meter, downbeats, phrases, labels) or that need a reference (alignment).
/*fields:begin*/
const DOC_FIELDS = {
  "required": [
    "schema_version", "provenance.analyzer.name", "provenance.analyzer.analyzer_version",
    "provenance.created_at", "provenance.source.kind", "signal.duration_s", "signal.content_start_s", "signal.content_end_s",
    "signal.silence_regions", "signal.fade_in", "signal.fade_out", "signal.energy.full.step_s",
    "signal.energy.full.values", "signal.energy.percussive.values", "signal.energy.low_band.values",
    "tempo.bpm", "tempo.confidence", "tempo.stability", "tempo.half_double_candidates",
    "grid.beats.times_s", "structure.segments", "events"
  ],
  "optional": [
    "grid.downbeats.times_s", "grid.downbeats.confidence", "grid.meter", "grid.phrases", "labels",
    "alignment.offset_s", "alignment.residual_s", "alignment.confidence",
    "alignment.reference.duration_s", "provenance.alignment.result",
    "provenance.alignment.hypotheses"
  ],
  "report_only": ["content.explicit"],
  // Fields read inside array items and nested objects, as `it(item, "key")`.
  "items": {
    "structure.segments": ["id", "start_s", "end_s", "boundary_confidence"],
    "labels": ["segment_id", "label", "confidence", "method"],
    "events": ["kind", "start_s", "end_s", "strength", "confidence"],
    "grid.phrases": ["start_s", "end_s", "bars", "confidence", "method"],
    "grid.meter": ["beats_per_bar", "confidence", "method"],
    "tempo.half_double_candidates": ["bpm", "confidence"],
    "signal.silence_regions": ["start_s", "end_s"],
    "signal.fade_in": ["start_s", "end_s", "confidence"],
    "signal.fade_out": ["start_s", "end_s", "confidence"],
    "provenance.alignment.hypotheses": ["name", "residual_s"],
    "content.explicit": ["value", "confidence", "method", "evidence"],
    "content.explicit.evidence": ["source", "value"]
  }
};
const V1_FIELDS = ["document"];
/*fields:end*/

function at(obj, path) {
  let cur = obj;
  for (const k of path.split(".")) {
    if (cur === null || cur === undefined) return undefined;
    cur = cur[k];
  }
  return cur;
}
const docAt = (doc, path) => at(doc, path);
// A key of an array item or nested object (the test lists them under "items").
const it = (obj, key) => obj[key];

function buildModel(d) {
  const doc = at(d, "document");
  if (!doc) throw new Error("The analysis response has no document.");
  const labels = {};
  for (const l of docAt(doc, "labels") || []) labels[it(l, "segment_id")] = l;
  const offset = docAt(doc, "alignment.offset_s");
  const aligned = offset != null;
  const sections = docAt(doc, "structure.segments").map((s) => {
    const l = labels[it(s, "id")], start = it(s, "start_s"), end = it(s, "end_s");
    return {
      id: it(s, "id"), start, end, boundaryConf: it(s, "boundary_confidence"),
      label: l ? it(l, "label") : "unlabelled", conf: l ? it(l, "confidence") : null,
      method: l ? it(l, "method") : null,
      refStart: aligned ? Math.max(start + offset, 0) : null,
      refEnd: aligned ? Math.max(end + offset, 0) : null,
    };
  });
  const events = docAt(doc, "events").map((e) => ({
    kind: it(e, "kind"), start: it(e, "start_s"), end: it(e, "end_s") ?? null,
    strength: it(e, "strength"), conf: it(e, "confidence"),
  }));
  const span = (r) => ({ start: it(r, "start_s"), end: it(r, "end_s") });
  const fade = (f) => (f ? { ...span(f), conf: it(f, "confidence") } : null);
  const meter = docAt(doc, "grid.meter");
  const explicit = docAt(doc, "content.explicit");
  const step = docAt(doc, "signal.energy.full.step_s");
  const rec = docAt(doc, "provenance.alignment.result");
  return {
    duration: docAt(doc, "signal.duration_s"),
    contentStart: docAt(doc, "signal.content_start_s"),
    contentEnd: docAt(doc, "signal.content_end_s"),
    silences: docAt(doc, "signal.silence_regions").map(span),
    fadeIn: fade(docAt(doc, "signal.fade_in")),
    fadeOut: fade(docAt(doc, "signal.fade_out")),
    energy: {
      step,
      full: docAt(doc, "signal.energy.full.values"),
      perc: docAt(doc, "signal.energy.percussive.values"),
      low: docAt(doc, "signal.energy.low_band.values"),
    },
    beats: docAt(doc, "grid.beats.times_s"),
    downbeats: docAt(doc, "grid.downbeats.times_s") || null,
    downbeatConf: docAt(doc, "grid.downbeats.confidence"),
    meter: meter ? { bpb: it(meter, "beats_per_bar"), conf: it(meter, "confidence"), method: it(meter, "method") } : null,
    phrases: (docAt(doc, "grid.phrases") || []).map((p) => ({
      ...span(p), bars: it(p, "bars"), conf: it(p, "confidence"), method: it(p, "method") })),
    sections,
    events,
    drops: events.filter((e) => e.kind === "drop"),
    tempo: {
      bpm: docAt(doc, "tempo.bpm"), conf: docAt(doc, "tempo.confidence"),
      stability: docAt(doc, "tempo.stability"),
      candidates: docAt(doc, "tempo.half_double_candidates").map((c) => ({ bpm: it(c, "bpm"), conf: it(c, "confidence") })),
    },
    alignment: rec == null ? null : {
      result: rec, offset, residual: docAt(doc, "alignment.residual_s"),
      conf: docAt(doc, "alignment.confidence"),
      refDuration: docAt(doc, "alignment.reference.duration_s"),
      hypotheses: (docAt(doc, "provenance.alignment.hypotheses") || []).map((h) => ({
        name: it(h, "name"), residual: it(h, "residual_s") })),
    },
    // Report-only (spotdown#36): optional, never changes how a track is shown.
    explicit: explicit ? {
      value: it(explicit, "value"), conf: it(explicit, "confidence"), method: it(explicit, "method"),
      evidence: (it(explicit, "evidence") || []).map((v) => ({ source: it(v, "source"), value: it(v, "value") })),
    } : null,
    prov: {
      analyzer: docAt(doc, "provenance.analyzer.name"),
      version: docAt(doc, "provenance.analyzer.analyzer_version"),
      source: docAt(doc, "provenance.source.kind"), schema: docAt(doc, "schema_version"), created: docAt(doc, "provenance.created_at"),
    },
  };
}
