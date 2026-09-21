# progress.md

Shared observatory progress file. Both the active session and automated worker sessions read this on start and append a short timestamped entry after each significant action (status checks, plan writes, sequence loads/starts/stops, errors, interventions).

---

## Created

- 2026-09-21T00:00:00Z: Enriched `get_imaging_metadata` — each session directory's `AcquisitionDetails.csv` (TargetName, FocalLength, telescope/camera/observer fields) is injected into every `ImageMetaData.csv` row unless the image row already has a non-empty value for that field. Missing/empty acquisition files are ignored. Added `tests/test_imaging.py` (8 cases); ruff, mypy, and unittest all pass.
