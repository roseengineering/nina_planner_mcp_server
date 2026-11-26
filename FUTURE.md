# Future questions

## Does output normalization shift mosaic stack backgrounds?

When a real multi-pointing mosaic is available, test whether Siril's
`-output_norm` has the same stack-specific background-level effect in
`GPS_Preprocess.py`'s `seqapplyreg -framing=max` mosaic path. Compare the
background p50, extrema, and output coverage/dimensions with output
normalization enabled and disabled, using distinct output names. Decide
whether the mosaic path should preserve or remove `-output_norm`.

This remains unverified because no mosaic dataset was available during the
M42 investigation.

## Section 9: validate the framing policy

The chosen policy is ``framing=min`` by default for known single-pointing
``OSC_Preprocessing.ssf`` runs; callers can pass ``framing="current"`` to opt
out. Mosaics keep their script-defined framing and never receive automatic
``min`` across pointings. Validate this policy against both real data shapes:

1. **Single-pointing alt-az run with measurable rotation:** compare the current
   framing with `framing=min` on identical registered inputs and distinct
   output names. Record output dimensions, common-footprint/coverage fraction,
   background p50, and how much legitimate coverage is lost to dithers or
   translations—not just rotation wedges.
2. **Real multi-pointing mosaic:** use a target identified as mosaic from its
   manifest/plan `pointing_count`. Verify the chosen mosaic framing policy and
   dimensions; do not apply `min` across pointings automatically. If
   `GPS_Preprocess.py` uses `-output_norm`, compare its background p50 and
   extrema with that option disabled as described above.
3. **Keep cropping roles separate:** verify `Autocrop.py` removes actual
   empty borders from `framing=max` output, while `framing=min` handles
   common-overlap framing; do not treat content-based autocrop as a substitute
   for overlap framing.

Use the results to confirm or revise the chosen default and document explicit
mosaic behavior. M42's `framing=min` result retained 86% of the frame despite
no measurable coverage problem; the operator accepts that crop for the desired
composition, but the rotation-bearing and mosaic tests are still needed to
measure their coverage semantics and verify the mosaic guard.
