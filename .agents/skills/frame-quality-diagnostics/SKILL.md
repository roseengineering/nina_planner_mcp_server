---
name: frame-quality-diagnostics
description: Diagnose autofocus failures and read per-frame quality telemetry (HFR, HFR σ, detected stars, Mean ADU, StDev) during an acquisition run. Use when an AF run fails or its curve looks flat, when HFR or star counts drift/spike, when ASTAP solving fails, or when deciding whether frames are degraded and whether to escalate or stop a run.
---

# Frame-quality diagnostics

Reads NINA's per-frame telemetry to separate a real fault from a measurement artifact, and to tell a harmless autofocus failure from one that costs focus. Derived from the 2026-10-21→22 night (11 targets, 10 AF runs, 457 frames); every claim below was verified against that run's logs and the on-disk frame metadata.

Per-frame fields come from the `IMAGE-SAVE` event's `ImageStatistics`: `HFR`, `HFRStDev` (σ), `Stars`, `Mean`, `StDev`, plus `Temperature`. AF points arrive as `AUTOFOCUS-POINT-ADDED` with `Position` and `HFR`; `Detected Stars` and σ per point are only in `get_logs`.

## 1. Autofocus failures: sweep width first, star count second

An AF failure logs `Auto Focus Failed! R² ... below threshold` or `AutoFocus did not complete successfully`, then `restoring the focuser position to <N>`. Measure the **sweep span** (highest position − lowest position in the point list) and the star count in the AF frames:

| sweep span | stars | outcome (n) |
|---|---|---|
| 80–100 steps | ≥40 | **fits** (6/6) |
| 40–60 steps | any | **fails** (3/3) |
| 90 steps | **8** | **fails** (1/1, R² −0.59) |

- **Sweep span is the primary cause.** A sweep starting close to the current focus position leaves walls too short for the hyperbolic regression to pin its asymptotes. The curve still *looks* like a textbook V — NGC 2232's ran 1261→1.614 … **1231→1.303 floor** … 1201→1.988, monotonic both walls — and still failed. **Do not judge an AF fit by eye; measure the span.**
- **Star count is secondary and only bites on a wide sweep.** 8 stars broke a 90-step sweep (R² −0.59, a *negative* R² = worse than a flat line). 35–57 stars with a 60-step sweep failed for width, not count.
- The 60–80 span gap is **untested**. Treat ≥80 as safe and ≤60 as doomed; investigate anything between rather than assuming.

**Most failures are harmless — verify, don't react.** On a failed fit NINA restores the pre-sweep position, and that position was correct in all four cases (M39→1234, NGC 1981→1239, NGC 2264→1221, NGC 2232→1221). **Read the next science frame to confirm**: HFR 1.5″ at σ 0.13 means focus is fine and no action is needed. A *genuinely* bad focus shows HFR ≥2.5″ at σ ~0.2 (uniformly bloated), not σ inflated.

Practical rule: an AF failure costs a few minutes and almost never costs the target. Do not stop or re-run a sequence over one.

## 2. Star count and HFR are decoupled

**Star count and background move together; HFR tracks only focus and scatter.** Never read a star-count dip as an HFR problem, or vice versa. Verified signatures:

| signature | HFR | σ | stars | Mean ADU | cause |
|---|---|---|---|---|---|
| scatter / haze | **way up** (5.7–6.9″) | **way up** (4.2–4.6) | may hold | rises | thin cirrus halos |
| extinction | **falls** | low | **falls** | rises | faint stars lost into skyglow |
| dither settle | up | **low** (uniform) | unchanged | unchanged | frame caught mid-dither |
| post-recenter settle | up | low–mid | dips 1 frame | unchanged | first frame after a `GUIDER-STOP`→`GUIDER-START` cycle |
| star-count artifact | **higher on high-star frames** | higher | — | — | low-SNR faint stars inflate the mean |
| skyglow drift | unchanged | unchanged | — | moves | background gradient |

- **σ is the discriminator for scatter.** Clean nights run σ 0.13–0.20; σ ≥4 with HFR ≥5 is scatter halos. Uniformly bloated stars (defocus, trailing) keep σ low while HFR doubles.
- **The star-count artifact inverts the intuition**: the *worst* HFR frames of NGC 1981 were its *highest*-star frames (3.63″ at 35★, 3.60″ at 30★ vs 1.3–1.6″ at 14–17★). More faint stars detected → lower SNR → inflated mean HFR and σ. A rising HFR with a rising star count is not degradation.
- **Settles are single-frame and self-correcting.** One frame after a recenter read 2.15″/σ0.755 with stars 221→84, then the next frame was 1.585″/σ0.138/219★. Two other post-recenter frames were clean — so a settle is *not* systematic. Check whether the following frame recovered before counting a frame as bad.
- **HFR can improve while the sky worsens.** As extinction bites, only the brightest (sharpest) stars survive detection, so HFR *drops* while star count halves. Star count is the honest transparency signal; HFR alone will mislead you.

## 3. ASTAP failures: frame parseability, not the solver

`ASTAP - Plate solve failed.` on both the normal solve and the blind failover means **the frame could not be matched**, and it correlates with the same two causes as AF failures:

| field | stars in frame | result |
|---|---|---|
| NGC 884 / M45 / M42 / M44 / M41 / M47 | 100–769 | solves first try |
| M39 | 42–51, scatter-bloated | **8 consecutive failures** |
| NGC 1981 | **8** | **3+ failures** |
| NGC 2264 | 14–35 | 2 failures, then success |

- Do **not** attribute it to a broken solver, a stale star database, or "first solve after stow" — the stow theory was tested and disproven when NGC 1981 failed after a 2h45m stow while NGC 884/M45 solved first-try from a tracking mount.
- Under `ContinueOnError` centering is skipped and the target still images; only framing precision is lost. Mount goto proved arcmin-accurate across 150 uncentered frames on a prior night, so it is safe to let it proceed.
- **A 45–65° false-positive sync is a separate, recurring quirk**: solve #1 returns a solution far from the true pointing (e.g. 64.9° off), NINA syncs and re-slews, and solves #2–3 converge (18′58″ → 7′21″). Self-correcting; costs 2–3 solves. Not a reason to intervene.

## 4. Escalation triggers

Standing thresholds. Any one sustained (not a single frame) means stop and ask the user whether to continue:

- **HFR > 3″ sustained** (clean baseline is ~1.5″)
- **detected stars < 10 sustained** — note this is field-dependent, so compare against the target's own early frames, not an absolute
- **hard Mean ADU drift** — a monotonic climb across a target, with star counts falling alongside it

If a trigger fires, report the numbers and ask; do not stop a sequence unilaterally. When HFR recovers, say so explicitly — the M39 decision was "continue" precisely because HFR fell from ~6.0 to 1.5–2.6 even though star counts stayed depressed.

## 5. Verifying what actually landed

`get_plan_progress` reports `complete: true` from **attributed** counts, which can mask a shortfall on a `mode=full` re-shoot: M42 reported `acquired_count 137, complete: true` at 17/120 because it summed a prior night's 120 with tonight's 17.

- Use `audit_plans` for the true shape (`expected` / `on_disk` / `attributed` / `status`).
- For definitive proof, count files per date folder on the drive mount — date folders are NINA **observing** days (post-19:01 rollover), so a 02:17 clock-time capture lands in the *previous* date's folder.
- **Always verify a `mode=full` re-shoot this way; never trust `complete: true`.**

Calibration (`flat`/`dark`/`bias`) is pool-credited by type+filter across plans, so a plan can show those complete without its own frames ever having been shot. Trust `audit_plans`'s `POOL_CREDITED` status, not the raw counts.

Full night-by-night provenance lives in `PROGRESS.md`; the run loop is the `night-loop` skill.
