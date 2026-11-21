# Real-music trial, 2026-11-21 (informal, owner-observed)

**Status: not a quality measurement.** One reviewer listened to spotdown's own output on a handful of
tracks and said whether it matched what they heard. Nothing here is ground truth, nothing is a gate
pass or fail, and none of it may be copied into `quality/fixtures/cc/catalog.json` as truth. The owner
(not a musician) decided on 2026-11-21 to stop this effort and tune on the finished product instead
(classbeat#1298 stays open for a later corpus decision).

## What was done
- **Synthetic runner (Phase 0):** all 23 gates pass, run in a Linux container (native macOS cannot run
  the sandbox; see `quality-harness.md`).
- **CC corpus:** 24 Kevin MacLeod tracks (CC-BY, 73 to 160 BPM, 213 MB) were downloaded locally and
  hashed. Not committed, no catalog entries written, no annotations made. The licence version
  (3.0 or 4.0) of the 2006 to 2008 tracks still needs checking per track before any catalog entry.
- **Listen-and-check pages** (local only, not in this repository) played spotdown's own analysis with
  clicks on its beats and asked yes/no/not sure per item. Run on one CC track (`cc01`) and three
  vocal songs the owner picked from Spotify (`p02`, `p03`, `p04`; titles and files stay off this
  repository). The analyser was v6 with the structure document's labels (`label_calc`), the ones the UI shows.

## What the owner observed
- **Beats:** 5 of 5 spot checks matched on `cc01` and `p02`; the tempo (158 BPM, with 79 as the other
  candidate) was not double-time. Not checked on `p03` and `p04`.
- **`cc01` (instrumental dance track):** all 10 section changes, 4 drops, start and end matched.
- **Section boundaries are mostly close.** On `p03` all four boundaries the owner heard in the first
  94 s were found within 0.6 s. On `p04` three of seven in the first 88 s were found (the others lay inside one
  55 s part). On `p02` 7 of 10 reported changes were heard; the missed one was the vocal entry about
  9 s before spotdown's intro ended.
- **Segmentation is uneven.** `p03` was split into 21 parts (extra boundaries inside single
  verses and choruses). `p04`, a steady-energy older recording, was split too coarsely.
- **Labels are the weak part.** On `p03` the first chorus was mostly labelled verse and a second verse
  was labelled bridge. On `p04` seven of nine labelled parts were "chorus" and none "verse", and
  there is no pre-chorus label (the owner hears verse, pre-chorus, chorus). `p02` had no label the owner said was wrong,
  but three were not sure. The core analyser still returns an older label vocabulary
  (`chorus/drop`, `breakdown/quiet`) next to the structure document's; tools should read one.
- **No waveform in the standalone UI**, and no way to analyse a local file there; it takes a Spotify URL.

## Not measured
Drop timing on vocal songs (spotdown reported none on `p02`; one on `p03`), edges, calibration (ECE),
robustness, time and memory on real audio, and any per-gate number. n is 4 tracks and one reviewer.

## Candidate follow-ups (the Architect's call; none is scheduled)
1. Merge over-fine segments before labelling, and re-check the chorus rule on tracks with steady energy.
2. Decide whether a pre-chorus label is wanted, or document that it is not.
3. Expose one label vocabulary and drop the older one from the core result.
4. Show the analysis on a waveform in the UI and accept a local file in service mode (the cloud agent's UI work).
