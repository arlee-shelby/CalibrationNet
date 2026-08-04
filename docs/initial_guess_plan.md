# Initial-guess redesign — discussion draft (2026-08-05)

The second-pass "computed starting guesses" idea works in principle
(8718 p95: both windows recovered) but AS's verification round showed
its prediction step is systematically inaccurate, and that several
failure classes need different treatment entirely. This document
collects the evidence, lays out candidate strategies — AS's three plus
mine — and proposes an order. Nothing here is implemented; it is the
basis for discussion.

## What the verification round established

| diagnosis | evidence |
|---|---|
| D1 — the one-ratio prediction is biased in both directions | predictions visibly TOO LOW (8631 p1067, 8637 p1087, 8718 p1027 Auger) and TOO HIGH (8637 p77 CE — sometimes fully outside the peak, even for the strongest line; 8718 p1030 Auger). A single scale factor through zero cannot represent pixels that differ in both gain and offset. |
| D2 — good predictions are not sufficient | 8631 p21 Auger: two peaks clearly visible, prediction lines good, yet the second-chance fit converged with a degenerate covariance (cen1 error ~9e7). Fit conditioning (shape/background parameters) fails independently of starting positions. |
| D3 — LDET Auger region is a BLEND, and bigger than we thought | AS: both Auger lines sit inside the dominant 100–180 ADC peak (8631 p1067, 8637 p1091, 8715 p1043, 8718 p1018); the 20–45 and ~62 ADC structures are NOT the Auger lines (they decouple — 8637 p1091 has the ~20 peak but no 62). Verify against the short-trap data (label short-trap-Fall2025), where resolution separates them. Arithmetic supports an even stronger reading: at nominal gain the Augers predict to ~82/120 ADC and the Pb K X-rays (72.8/75.0/84.9 keV, not in our line tables) to ~134/140/171 ADC — the 100–180 blob is plausibly Augers + Pb X-rays merged, which is exactly why Auger-only predictions sit "at the low end" of it. |
| D4 — some pixels should not be fitted at all | 8718 p84, p85, p109: statistics too poor (no intermediate CE peaks visible). Fitting them wastes attempts and produces junk to review. |
| D5 — hardware threshold hides lines | threshold at ~20 ADC; for low-gain pixels (8718 p95, CE fine at 0.44x) the Auger lines predict BELOW threshold — unfindable by construction, and the visible low bump is the threshold function cut by the hardware threshold. |
| D6 — pixel classes beyond low-gain | 8626 p91 has essentially ZERO gain — its own category, to be treated separately (or excluded). |
| D7 — X-ray lines are missing from our tables | every source emits X-rays (e.g. Pb K from Bi-207) that appear in the spectra but are absent from isotope_decay_energies — they confuse the peak finder, the predictions, and the matching, and are candidates for the unexplained low-energy structures (~62 ADC?). |

Also: 8718 p99 STILL misses its weak 566 line — its find_peaks fit
passes every health check, so by design no second chance runs. Fixing
this class requires the quality-retry (formerly "4.3"), now clearly
wanted.

## Candidate strategies

**AS-1 — partial find_peaks + fill-in (AS's idea 1).** Run find_peaks
as today. If it finds fewer peaks than required, KEEP the found ones,
match them to lines (by order + tolerance), and construct starting
guesses ONLY for the missing peaks — positions interpolated from the
matched neighbors' actual ADC positions and the known energies,
amplitudes scaled by intensity ratios. Strengths: keeps the proven
finder as the backbone; local interpolation between real peaks is far
more accurate than any global relation (directly fixes 8637 p77's 5-of-
6 and p99's 566). Needs: reliable identification of WHICH lines the
found peaks are (the matching step, which the two-anchor logic already
does at extraction time).

**AS-2 — purpose-built peak finder (AS's idea 2).** Start from scipy's
find_peaks source and build a finder suited to our spectra (width-aware
prominence, expected-count scaling, threshold-region exclusion). Most
work, addresses the root cause; best held until AS-1/C-1 show their
limits.

**AS-3 — alternative finders (AS's idea 3).** Candidates to trial
cheaply on the reference pixels: scipy.signal.find_peaks_cwt (wavelet-
based, width-tuned, already in scipy); a matched filter (correlate the
spectrum with a gaussian of the expected width, find correlation
maxima); approaches borrowed from hdtv. A one-day comparative trial on
the reference pixels would rank them.

**C-1 — per-pixel two-anchor relation (mine).** Replace the one-ratio
scaling with the same two-anchor logic extraction already uses: locate
the two strongest CE peaks in the full histogram, identify them as the
482/976 K lines, and derive THIS pixel's gain AND offset. Every other
prediction follows from that two-point line. Fixes D1's both-direction
bias; cheap; reuses validated code.

**C-2 — blend-aware expected peaks (mine).** Before predicting, merge
lines whose predicted separation is below k x expected width into ONE
expected blended peak at the intensity-weighted position. The predicted
list then matches what is physically observable (D3); pairs naturally
feed 4.4's constrained blend fitting later.

**C-3 — statistics gate (mine).** Before any fitting: estimate the
signal in the strongest expected line's region vs background; below a
threshold (AS to set), skip the window entirely with "insufficient
statistics" recorded and a data-only figure (D4).

**C-4 — threshold awareness (mine).** Measure each pixel's hardware
threshold from its histogram (first populated bin); expected lines
below it are marked unfindable-by-hardware and never fitted for (D5);
failure figures now show from 0 ADC so the threshold peak is always
visible (done).

**C-5 — second-chance fit conditioning (mine).** Diagnose the p21-class
degeneracy: likely the per-peak shape parameters (n, h) or background
terms wander when a peak is weak. Candidate remedies (all initial-value
/ bounds level, model untouched): initialize slope/intercept from the
window edges; tighter bounds on shape parameters during the rescue.

**C-6 — X-ray lines into the tables (with AS).** Add each isotope's
X-ray lines (AS supplies NNDC energies/intensities) as their own group
in isotope_decay_energies. Predictions and matching then expect them
(D7); the short-trap data can confirm which observed structures they
explain.

## Proposed order (strawman)

1. C-3 + C-4 (stop fitting the unfittable; no fit-logic changes),
2. C-1 (fix the prediction backbone) + C-2 (predict blends, not lines),
3. AS-1 (fill-in as the second pass proper, using C-1/C-2 predictions),
4. quality-retry for healthy-but-incomplete fits (the p99 class),
5. AS-3 quick trial on the reference pixels; AS-2 only if still needed,
6. C-5 conditioning fixes as encountered,
7. C-6 X-rays as soon as AS provides the values (verifiable against
   short-trap spectra).

Each step: same-numbers check on the reference pixels + AS plot review
before the next.

## Questions for AS

1. X-ray lines: can you provide NNDC energies/intensities per isotope?
2. Statistics gate: what minimum counts (or signal/background) makes a
   window worth fitting?
3. Pixel 91 (zero gain): exclude from fitting entirely, or is there a
   treatment worth attempting?
4. Do you agree with the proposed order — and specifically with running
   AS-1 (your fill-in idea) as the main second pass, with AS-2/AS-3
   held as follow-ups?
