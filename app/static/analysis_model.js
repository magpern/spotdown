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
    "provenance.created_at", "signal.duration_s", "signal.content_start_s", "signal.content_end_s",
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
  ]
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

function buildModel(d) {
  const doc = at(d, "document");
  if (!doc) throw new Error("The analysis has no document (no tempo was found).");
  const labels = {};
  for (const l of docAt(doc, "labels") || []) labels[l.segment_id] = l;
  const offset = docAt(doc, "alignment.offset_s");
  const aligned = offset !== undefined;
  const sections = docAt(doc, "structure.segments").map((s) => {
    const l = labels[s.id];
    return {
      id: s.id, start: s.start_s, end: s.end_s, boundaryConf: s.boundary_confidence,
      label: l ? l.label : "unlabelled", conf: l ? l.confidence : null,
      method: l ? l.method : null,
      refStart: aligned ? Math.max(s.start_s + offset, 0) : null,
      refEnd: aligned ? Math.max(s.end_s + offset, 0) : null,
    };
  });
  const events = docAt(doc, "events");
  const step = docAt(doc, "signal.energy.full.step_s");
  const rec = docAt(doc, "provenance.alignment.result");
  return {
    duration: docAt(doc, "signal.duration_s"),
    contentStart: docAt(doc, "signal.content_start_s"),
    contentEnd: docAt(doc, "signal.content_end_s"),
    silences: docAt(doc, "signal.silence_regions"),
    fadeIn: docAt(doc, "signal.fade_in"),
    fadeOut: docAt(doc, "signal.fade_out"),
    energy: {
      step,
      full: docAt(doc, "signal.energy.full.values"),
      perc: docAt(doc, "signal.energy.percussive.values"),
      low: docAt(doc, "signal.energy.low_band.values"),
    },
    beats: docAt(doc, "grid.beats.times_s"),
    downbeats: docAt(doc, "grid.downbeats.times_s") || null,
    downbeatConf: docAt(doc, "grid.downbeats.confidence"),
    meter: docAt(doc, "grid.meter") || null,
    phrases: docAt(doc, "grid.phrases") || [],
    sections,
    events,
    drops: events.filter((e) => e.kind === "drop"),
    tempo: {
      bpm: docAt(doc, "tempo.bpm"), conf: docAt(doc, "tempo.confidence"),
      stability: docAt(doc, "tempo.stability"),
      candidates: docAt(doc, "tempo.half_double_candidates"),
    },
    alignment: rec === undefined ? null : {
      result: rec, offset, residual: docAt(doc, "alignment.residual_s"),
      conf: docAt(doc, "alignment.confidence"),
      refDuration: docAt(doc, "alignment.reference.duration_s"),
      hypotheses: docAt(doc, "provenance.alignment.hypotheses"),
    },
    prov: {
      analyzer: docAt(doc, "provenance.analyzer.name"),
      version: docAt(doc, "provenance.analyzer.analyzer_version"),
      schema: docAt(doc, "schema_version"), created: docAt(doc, "provenance.created_at"),
    },
  };
}
