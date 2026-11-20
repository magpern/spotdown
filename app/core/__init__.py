"""Core analyser: an audio signal in, features on the file's own clock out.

It knows no source, no reference clock, no consumer and no user. The
sources (``app.service``) bring the file, and the alignment layer maps its clock to a reference.
"""

# Bump when thresholds/features change so the caches keyed on it refresh.
ANALYZER_VERSION = 6
SR = 22050
HOP = 512
