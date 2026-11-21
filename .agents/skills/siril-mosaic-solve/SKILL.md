---
name: siril-mosaic-solve
description: Detect a mosaic in the archive and stitch it with Siril through siril-mcp — run GPS_Preprocess.py headlessly (plate-solve → framing=max registration → maximize stack), verify the panes actually stitched, clean up. Use when the user asks to solve, stitch, process or stack a mosaic, when a plan.json carries a `mosaic` block or multiple `rN_cN` pointings, or when manifest.json reports pointing_count > 1.
---

# Solving mosaics with siril-mcp

Siril's star-pair `register` (what `OSC_Preprocessing.ssf` runs) aligns every
frame against one reference image, so panes that do not overlap the reference
die at "Not enough star pairs". Mosaics need **astrometric** registration
instead: plate-solve each frame, then apply the WCS shifts with
`-framing=max` and stack with `-maximize`. Siril's repository already ships
exactly that pipeline — **`GPS_Preprocess.py`** (`seqplatesolve` →
`seqapplyreg -framing=max` → `stack -maximize -feather`) — and it runs
headless *if and only if* it receives CLI arguments; with none it opens a Qt
dialog and blocks until the tool's timeout. `run_osc_preprocessing`'s
`script_args` parameter is what passes them.

## 1. Find the mosaic

`siril-mcp` does **not** expose pane information — no tool reports
`pointing_count`. Read it from disk:

```bash
# per directory under $ARCHIVE_ROOT (~/landing/<obs>/<target>/)
python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['pointing_count'])" <dir>/manifest.json
grep -o '"mosaic"' <dir>/plan.json        # mosaic block, or:
grep -o '"label": "r[0-9]*_c[0-9]*"' <dir>/plan.json | head   # expanded panes
```

`pointing_count > 1` (or any `rN_cN` label) = mosaic. Key facts about the
archive shape:

- **One folder per plan**, not per pane: `nina-archive` unions every pane's
  lights into a single `lights/` directory. All panes are solved from one
  `run_osc_preprocessing` call — no per-pane splitting, no per-pane runs.
- Folder name: `<plan-stem>__<plan-id>/` under `<archive>/<observatory>/`.
- Pane identity survives only in `plan.json` / `manifest.json`, not in the
  archived filenames.

## 2. Preflight

1. `get_siril_status()` — `siril_cli.found` and `python_venv.present` (the
   venv is what makes `pyscript` work).
2. `check_directory_status(directory=…)` and keep the result:
   - `input_folders_found` decides the script (step 3). All four of
     `biases`/`darks`/`flats`/`lights` → GPS; **lights only** → use
     `DSA-Seestar_Mosaic_Preprocessing.ssf` instead (GPS `cd`s into every
     calibration folder and fails without them).
   - `light_quality.median_detected_stars` → `min_stars` (use 30; Siril's
     plate solver needs stars too, and NINA's peak count runs 4–6× Siril's
     Gaussian count).
   - Save `pipeline_stages` — if `process/` already holds a **failed**
     standard-script run, its calibrated frames are *debayered*, and GPS
     would skip recalibration and drizzle RGB data. The `-c` flag below wipes
     them; capture the old stages first so the diagnosis is not lost.
3. `list_scripts(query="GPS")` → confirm `GPS_Preprocess.py` resolves (it
   lives in the Get Scripts repository cache; pass the bare name —
   `_resolve_script` finds it).

## 3. Run it

```python
run_osc_preprocessing(
    directory="<mosaic folder>",
    script="GPS_Preprocess.py",
    script_args="-c -ps -bg -z 1 -f 20",
    timeout=7200,          # per-frame solve + drizzle; a 40-frame target ran in 18s with local catalogues, mosaics are much larger
    min_stars=30,
    force=<True if a prior run left a root result_*.fit>,
)
```

`script_args` is a string or a list of tokens; it is appended to the
`pyscript "<path>" …` line, which Siril hands to the script as its argv.
Never pass `min_pairs` here — it errors on `.py` scripts, and with `-ps` no
star pairing happens at all.

| flag | why |
|---|---|
| `-c` | wipe a stale `process/` first. Without it GPS *skips* calibration when `pp_light_.seq` exists and would drizzle the previous run's already-debayered frames. No-op on a fresh directory. |
| `-ps` | platesolve the sequence and register from the WCS with `-framing=max` — the whole mosaic capability. Without it GPS falls back to star-pair `register`, which fails across panes. |
| `-bg` | per-frame background extraction, polynomial order 1 (`seqsubsky … 1`), as the [mosaic tutorial](https://siril.org/tutorials/mosaics) recommends for individual frames. Optional; omit on nebula-heavy targets if in doubt. |
| `-z 1` | **required for OSC/CFA data**: GPS calibrates *without* `-debayer`, so debayering happens only via RGB drizzle at scale 1. Without it the stack stays a Bayer image. |
| `-f 20` | 20 px feather across pane seams. GPS defaults to 0 (no feather). |

Do **not** pass `-b/-r/-w` frame filters: their defaults are `100%`
(no filtering), and filtering mosaics leaves holes where a pane's frames are
collectively rejected.

Why not the other mosaic scripts in `list_scripts`: every `Naztronomy-*`,
`AMSP`, `Rolandet-*` and `osc-multi-night-stacking` `.py` calls
`QApplication.exec()` unconditionally — headless they pop a window and hang
until timeout. `DSA-Seestar_Mosaic_Preprocessing.ssf` is a fine lights-only
alternative but ignores your calibration frames entirely.

## 4. Verify — a success status is not proof of a stitch

1. **`pipeline_stages`**: `registered_r_pp_lights` should be close to
   `calibrated_pp_lights` (with `-bg` the registered files are named
   `r_bkg_pp_light_*`, which the counter includes). A large gap means frames
   failed the solve or were filtered out.
2. **`visible_outputs`**: GPS writes `<OBJECT…>_b…-z1-f20-<LIVETIME>s.fit`
   to the directory **root** — it never matches `result_*`. If the folder
   also holds an old `result_*.fit`, the auto-preview renders *that* one
   (root `result_*` sorts first), so render the stitch explicitly:

   ```python
   render_preview(directory=…, fits_file="<the GPS-named .fit>")   # → <name>.png
   ```

   then `read` the PNG: check the full requested footprint is present, seams
   are feathered rather than hard-edged, and no pane is missing (compare
   against `cols × rows` in `plan.json`).
3. **`preview_error` ≠ run failure.** Siril cannot display images beyond
   32768 px per side; a very large mosaic stack can exist and still fail the
   PNG render. `visible_outputs` + a file size > 0 is the truth there.

## 5. Failure playbook

| symptom | cause / fix |
|---|---|
| `status: failed`, message `**** ERROR ***` | GPS catches its own exceptions and prints only this marker — the real error is in `log_tail` (e.g. `KeyError: 'FOCALLEN'` means the header lacks focal length; pass `-ps <mm>` to override). |
| `…unable to retrieve the remote catalogue from tapvizier…` / `did not solve` | `seqplatesolve … -catalog=nomad -nocache` queries **remote** NOMAD per frame when no local catalogue is installed — see §6. Install local catalogues and re-run; solved frames show `Fetched … from Tycho-2+NOMAD catalogue` (local) instead of a VizieR URL. |
| `siril-cli timed out` | raise `timeout` (mosaics × per-frame solve × drizzle; 7200+ is normal). With local catalogues a 40-frame target solves in seconds. |
| `script_args applies to .ssf scripts` | you passed args to an `.ssf` — siril-cli's `-s` cannot forward them; only `.py` scripts take `script_args`. |
| `min_pairs applies to .ssf scripts` | same in reverse; with `-ps` pairing is irrelevant anyway. |
| `status: skipped` | a root result already exists → pass `force=True` (and keep `-c` to rebuild intermediates). |
| memory pressure / very slow stack | drizzle + `maximize` holds every pane-sized frame; expected for large grids. Consider processing on the workstation rather than remotely. |

## 6. Install local catalogues once (makes solving offline and fast)

Siril's own error text recommends this, and it is the difference between a
solve that needs the internet per frame and one that does not. The catalog
choice happens *inside the solver*: with `-catalog=nomad` (GPS's hardcoded
flag) Siril redirects to the local KStars catalogue **only if all four
KStars files are readable** (`local_kstars_available()` checks namedstars,
unnamedstars, deepstars, USNO-NOMAD — source: `astrometry_solver.c`
`local catalogues if available`); the automatic/`gaia` path redirects to the
local Gaia file when *that* exists (`catalogue_gaia_astro`). One file without
the others changes nothing — verified: with only USNO-NOMAD installed,
`-catalog=nomad` still went remote.

`Siril_Catalog_Installer.py` is a Qt GUI (opens a dialog, exits 0 without
one) — install headless by downloading the files yourself (checksummed
against the published sums) and pointing Siril's config at them:

```bash
K=~/.local/share/kstars; mkdir -p "$K" && cd "$K"
B=https://free-astro.org/download/kstars-siril-catalogues
for f in namedstars.dat unnamedstars.dat deepstars.dat USNO-NOMAD-1e8.dat; do
  curl -LO "$B/$f.xz" && xz -dk "$f.xz"      # 31KB + 664KB + 27MB + 1GB
done
# Gaia DR3 astrometry extract (2.6 GB decompressed) for the automatic path:
curl -LO https://zenodo.org/records/14692304/files/siril_cat_healpix8_astro.dat.bz2
bunzip2 -k siril_cat_healpix8_astro.dat.bz2
```

Verify each file against its published SHA1
(`free-astro.org/download/kstars-siril-catalogues/sha1sums.txt`; the Zenodo
`.sha256sum` sits beside the archive), then set the four `catalogue_*` keys in
`<support>/siril/config.1.4.ini` to those absolute paths (Siril writes the
same keys itself via `set "core.catalogue_nomad=…"` — edit while Siril is not
running). Restart nothing; siril-cli reads the config per invocation.

**Proof of effect:** before installing, `seqplatesolve -catalog=nomad`
returned `unable to retrieve the remote catalogue`; after all four files were
in place the identical command logged `Fetched 2886 stars from Tycho-2+NOMAD
catalogue` and solved 38/38 frames with the VizieR endpoint still unreachable
(`http=000`).

## 7. After it works

- `clean_working_directory(directory=…)` reclaims the multi-GB `process/`
  tree — safe only once the root stack is verified (it refuses a
  non-succeeded target without `force=True`, and never touches `lights/`,
  `plan.json`, `manifest.json` or the root result).
- Log the run to `PROGRESS.md` with the plan id and pane count, per the
  standing rules.
