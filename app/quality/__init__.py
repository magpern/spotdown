"""The quality harness (plan section 7): synthetic fixtures with exact ground truth, mir_eval-style
metrics, the versioned ``quality/quality_gates.json`` and a runner that fails on a gate regression.

Pure modules (``metrics_calc``, ``gates_calc``, ``scoring_calc``, ``synth``) import no I/O beyond
numpy; the I/O sits in ``fixtures_io`` and ``runner``. Nothing here touches the network or keeps
audio after a run.
"""
