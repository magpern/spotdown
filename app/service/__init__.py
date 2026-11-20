"""Service mode: the v2 API (docs/contract-v2.md) behind one shared service key.

Layers: sources (``upload``, ``url``) bring the bytes, the sandbox runs the core analyser
(``app.core``) on them, and ``analysis_doc`` formats the file-clock result as analysis-v1. The
alignment layer and the caches plug in later (spotdown#11, spotdown#5).
"""
