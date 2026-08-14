"""Ready-made queries for the questions the analysis actually asks —
"pixel 60 across every run", optionally at one trap setting or one set
of run conditions — so nobody has to rebuild the join chains by hand.

TWO LAYERS:

1. ORM helpers (the original layer): take a session plus filters,
   return ORM objects with enough joined context to plot from.
2. NOTEBOOK layer (added 2026-08-14 at AS's request): functions that
   open their own session and return pandas DATAFRAMES, so a Jupyter
   notebook needs only

       from calibrationnet.queries import runs_overview, gain_map, spectrum
       runs_overview()                 # what is in the database
       gain_map(run_numbers=[9469])    # per-pixel gain vs nominal
       x, y = spectrum(9469, 40, segment=0)   # plt.stairs(y, x)

   Every notebook function also accepts session= to compose with an
   open session. This layer GROWS ON REQUEST: tell the session (or AS)
   what you want to plot and the query gets added here.

The underlying chain, for reference:

    runs -> run_segments -> run_pixels -> trap_filter_outputs
         -> spectrum_fits -> adc_peaks        (calibrations hang off
                                               run_pixels AND their
                                               trap_filter_output)

Examples (see also README "Usage"):

    from calibrationnet.queries import peaks_for_pixel

    # CE 976 centroid vs run for pixel 60, standard trap setting:
    for rp, peak in peaks_for_pixel(session, 60, line_label="CE 976",
                                    trap=(1250, 50, 1250)):
        print(rp.run_number, peak.centroid_adc, peak.centroid_error_adc)

    # every calibration for pixel 60 from runs at -300 V bias:
    calibrations_for_pixel(session, 60, udet_bias=-300.0)
"""

import numpy as np
from sqlalchemy import func, select

from .db import get_session
from .models import (ADCPeak, Calibration, CalibrationPoint, Isotope,
                     IsotopeDecayEnergy, KeVPeak, Run, RunPixel,
                     SpectrumFit, TrapFilterOutput)


def line_energies(session, isotope_name):
    """{'CE': [keV ascending], 'Auger': [...]} for one isotope, using
    each line's newest generic NNDC value. Used by the fit workflow's
    prediction-seeded initializer and by peak matching."""
    lines = session.execute(
        select(IsotopeDecayEnergy)
        .join(IsotopeDecayEnergy.isotope)
        .where(Isotope.name == isotope_name)
    ).scalars().all()
    groups = {}
    for line in lines:
        generic = [p for p in line.kev_peaks
                   if p.source_id is None and p.origin == "nndc"]
        if not generic:
            continue
        energy = max(generic, key=lambda p: p.created_at).energy_kev
        prefix = line.label.split()[0]
        groups.setdefault(prefix, []).append(energy)
    return {prefix: sorted(values) for prefix, values in groups.items()}


def _trap_filter(stmt, trap, tf_label):
    if trap is not None:
        rise, flattop, fall = trap
        stmt = stmt.where(TrapFilterOutput.trap_rise == rise,
                          TrapFilterOutput.trap_flattop == flattop,
                          TrapFilterOutput.trap_falltime == fall)
    if tf_label is not None:
        stmt = stmt.where(TrapFilterOutput.label == tf_label)
    return stmt


def _run_pixel_filter(stmt, pixel_number, run_numbers, segment_index):
    stmt = stmt.where(RunPixel.pixel_number == pixel_number)
    if run_numbers is not None:
        stmt = stmt.where(RunPixel.run_number.in_(run_numbers))
    if segment_index is not None:
        stmt = stmt.where(RunPixel.segment_index == segment_index)
    return stmt.order_by(RunPixel.run_number, RunPixel.segment_index)


def fits_for_pixel(session, pixel_number, *, run_numbers=None,
                   segment_index=None, trap=None, tf_label=None,
                   fit_label=None):
    """[(RunPixel, SpectrumFit)] for one pixel across runs.

    trap: (rise, flattop, falltime) to pin the filter setting;
    fit_label: e.g. "ce-6peak"."""
    stmt = (
        select(RunPixel, SpectrumFit)
        .join(TrapFilterOutput,
              TrapFilterOutput.run_pixel_id == RunPixel.id)
        .join(SpectrumFit,
              SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
    )
    stmt = _run_pixel_filter(stmt, pixel_number, run_numbers, segment_index)
    stmt = _trap_filter(stmt, trap, tf_label)
    if fit_label is not None:
        stmt = stmt.where(SpectrumFit.label == fit_label)
    return session.execute(stmt).all()


def peaks_for_pixel(session, pixel_number, *, line_label=None,
                    run_numbers=None, segment_index=None, trap=None,
                    tf_label=None, matched_only=None):
    """[(RunPixel, ADCPeak)] for one pixel across runs — e.g. the
    CE 976 centroid as a function of run.

    line_label: restrict to peaks matched to that decay line;
    matched_only=True: any matched peak; =False: only unmatched ones."""
    stmt = (
        select(RunPixel, ADCPeak)
        .join(TrapFilterOutput,
              TrapFilterOutput.run_pixel_id == RunPixel.id)
        .join(SpectrumFit,
              SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
        .join(ADCPeak, ADCPeak.spectrum_fit_id == SpectrumFit.id)
    )
    stmt = _run_pixel_filter(stmt, pixel_number, run_numbers, segment_index)
    stmt = _trap_filter(stmt, trap, tf_label)
    if line_label is not None:
        stmt = (stmt.join(IsotopeDecayEnergy,
                          ADCPeak.isotope_decay_energy_id
                          == IsotopeDecayEnergy.id)
                .where(IsotopeDecayEnergy.label == line_label))
    elif matched_only is True:
        stmt = stmt.where(ADCPeak.isotope_decay_energy_id.is_not(None))
    elif matched_only is False:
        stmt = stmt.where(ADCPeak.isotope_decay_energy_id.is_(None))
    return session.execute(stmt).all()


def calibrations_for_pixel(session, pixel_number, *, run_numbers=None,
                           segment_index=None, trap=None, tf_label=None,
                           calibration_type=None, current_only=False,
                           **run_settings):
    """[(Run, RunPixel, Calibration)] for one pixel across runs.

    Any Run column can be passed as a keyword to filter on the run's
    conditions, e.g. udet_bias=-300.0, main=110.0, hv=0.0.
    trap pins the filter setting the calibration was built from."""
    stmt = (
        select(Run, RunPixel, Calibration)
        .join(RunPixel, RunPixel.run_number == Run.run_number)
        .join(Calibration, Calibration.run_pixel_id == RunPixel.id)
        .join(TrapFilterOutput,
              TrapFilterOutput.id == Calibration.trap_filter_output_id)
    )
    stmt = _run_pixel_filter(stmt, pixel_number, run_numbers, segment_index)
    stmt = _trap_filter(stmt, trap, tf_label)
    if calibration_type is not None:
        stmt = stmt.where(Calibration.calibration_type == calibration_type)
    if current_only:
        stmt = stmt.where(Calibration.is_current)
    for column, value in run_settings.items():
        stmt = stmt.where(getattr(Run, column) == value)
    return session.execute(stmt).all()


# ---------------------------------------------------------------------
# NOTEBOOK layer: self-contained functions returning pandas DataFrames
# (or plain numpy arrays where that plots more directly). Every function
# accepts session=None and opens/closes its own when not given.
# ---------------------------------------------------------------------

def _with_session(fn, session, *args, **kwargs):
    if session is not None:
        return fn(session, *args, **kwargs)
    with get_session() as fresh:
        return fn(fresh, *args, **kwargs)


def _detector_of(pixel_number):
    return "upper" if pixel_number < 1000 else "lower"


def runs_overview(session=None):
    """One row per run in the database: conditions and content counts —
    the "what do we have" table (run, HV, times, segments, filter
    outputs, stored fits, calibrations)."""
    import pandas as pd

    def _query(s):
        seg_counts = dict(s.execute(
            select(RunPixel.run_number,
                   func.count(func.distinct(RunPixel.segment_index)))
            .group_by(RunPixel.run_number)).all())
        tfo_counts = dict(s.execute(
            select(RunPixel.run_number, func.count(TrapFilterOutput.id))
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .group_by(RunPixel.run_number)).all())
        fit_counts = dict(s.execute(
            select(RunPixel.run_number, func.count(SpectrumFit.id))
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .join(SpectrumFit,
                  SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
            .group_by(RunPixel.run_number)).all())
        cal_counts = dict(s.execute(
            select(RunPixel.run_number, func.count(Calibration.id))
            .join(Calibration, Calibration.run_pixel_id == RunPixel.id)
            .group_by(RunPixel.run_number)).all())
        rows = []
        for run in s.execute(select(Run).order_by(Run.run_number)).scalars():
            rows.append({
                "run": run.run_number, "hv": run.hv,
                "start": run.start_time, "end": run.end_time,
                "segments": seg_counts.get(run.run_number, 0),
                "filter_outputs": tfo_counts.get(run.run_number, 0),
                "fits": fit_counts.get(run.run_number, 0),
                "calibrations": cal_counts.get(run.run_number, 0),
            })
        return pd.DataFrame(rows)
    return _with_session(_query, session)


def fit_overview(run_numbers=None, tf_label=None, session=None):
    """One row per STORED fit: run, segment, pixel, detector, trap
    label, recipe, reduced chi2, the attempt that won, and the window
    pass it won on — the acceptance picture of a campaign."""
    import pandas as pd

    def _query(s):
        stmt = (
            select(RunPixel.run_number, RunPixel.segment_index,
                   RunPixel.pixel_number, TrapFilterOutput.label,
                   SpectrumFit)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .join(SpectrumFit,
                  SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
            .order_by(RunPixel.run_number, RunPixel.segment_index,
                      RunPixel.pixel_number))
        if run_numbers is not None:
            stmt = stmt.where(RunPixel.run_number.in_(run_numbers))
        if tf_label is not None:
            stmt = stmt.where(TrapFilterOutput.label == tf_label)
        rows = []
        for run, seg, pix, label, fit in s.execute(stmt).all():
            config = fit.config or {}
            rows.append({
                "run": run, "segment": seg, "pixel": pix,
                "detector": _detector_of(pix), "tf_label": label,
                "recipe": fit.label, "reduced_chi2": fit.reduced_chi2,
                "attempt": config.get("attempt"),
                "window": config.get("window"),
                "fit_lo": fit.fit_range_low, "fit_hi": fit.fit_range_high,
            })
        return pd.DataFrame(rows)
    return _with_session(_query, session)


def gain_map(run_numbers=None, tf_label=None, session=None):
    """One row per stored CE fit: the pixel's gain ratio vs nominal,
    measured from the fitted CE 482/976 anchor centroids (robust — see
    scripts/low_gain_report.py). Columns: run, segment, pixel,
    detector, tf_label, gain_ratio. Feed a threshold to find low-gain
    pixels; pivot on (pixel) for a detector map."""
    import pandas as pd
    from .fit_recipes import NOMINAL_RELATION, SCOUT_ANCHORS

    e_lo, e_hi = SCOUT_ANCHORS["Bi-207"]["anchor_energies"]
    nominal = 1.0 / NOMINAL_RELATION["Bi-207"]["gain_kev_per_adc"]

    def _query(s):
        stmt = (
            select(RunPixel.run_number, RunPixel.segment_index,
                   RunPixel.pixel_number, TrapFilterOutput.label,
                   SpectrumFit.pars)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .join(SpectrumFit,
                  SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
            .where(SpectrumFit.label == "ce-6peak"))
        if run_numbers is not None:
            stmt = stmt.where(RunPixel.run_number.in_(run_numbers))
        if tf_label is not None:
            stmt = stmt.where(TrapFilterOutput.label == tf_label)
        rows = []
        for run, seg, pix, label, pars in s.execute(stmt).all():
            cen_lo, cen_hi = pars.get("cen1"), pars.get("cen4")
            if not cen_lo or not cen_hi or cen_hi <= cen_lo:
                continue
            ratio = ((cen_hi - cen_lo) / (e_hi - e_lo)) / nominal
            rows.append({"run": run, "segment": seg, "pixel": pix,
                         "detector": _detector_of(pix), "tf_label": label,
                         "gain_ratio": ratio})
        return pd.DataFrame(rows)
    return _with_session(_query, session)


def spectrum(run, pixel, segment=0, tf_label="nabpy-standard",
             bins=None, session=None):
    """(bin_edges, counts) for one pixel's stored trap filter output —
    plt.stairs(counts, bin_edges) is the spectrum plot. bins defaults
    to 1-ADC bins over (0, 4500), the same binning every fit uses."""
    def _query(s):
        tfo = s.execute(
            select(TrapFilterOutput)
            .join(RunPixel, TrapFilterOutput.run_pixel_id == RunPixel.id)
            .where(RunPixel.run_number == run,
                   RunPixel.segment_index == segment,
                   RunPixel.pixel_number == pixel,
                   TrapFilterOutput.label == tf_label)).scalar_one()
        edges = bins if bins is not None else np.arange(0, 4500)
        counts, edges = np.histogram(np.asarray(tfo.energies), bins=edges)
        return edges, counts
    return _with_session(_query, session)


def stored_fit_curve(run, pixel, recipe="ce-6peak", segment=0,
                     tf_label="nabpy-standard", session=None):
    """(x, y) of a STORED fit's model curve over its own window —
    overlay on spectrum() to reproduce any fit figure in a notebook.
    Evaluates the frozen fit model at the stored parameters."""
    from lmfit import Parameters
    from . import fit_functions

    def _query(s):
        fit = s.execute(
            select(SpectrumFit)
            .join(TrapFilterOutput,
                  SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
            .join(RunPixel, TrapFilterOutput.run_pixel_id == RunPixel.id)
            .where(RunPixel.run_number == run,
                   RunPixel.segment_index == segment,
                   RunPixel.pixel_number == pixel,
                   TrapFilterOutput.label == tf_label,
                   SpectrumFit.label == recipe)).scalars().first()
        if fit is None:
            raise LookupError(
                f"no stored {recipe!r} fit for run {run} "
                f"segment {segment} pixel {pixel} ({tf_label}) — "
                "fit_overview() lists what exists")
        params = Parameters()
        if "num_peaks" not in (fit.pars or {}):
            params.add("num_peaks", value=fit.n_peaks, vary=False)
        for name, value in fit.pars.items():
            params.add(name, value=value, vary=False)
        x = np.arange(fit.fit_range_low, fit.fit_range_high)
        return x, fit_functions.fit_model(params, x)
    return _with_session(_query, session)


def centroid_trend(pixel, line_label, run_numbers=None,
                   tf_label="nabpy-standard", session=None):
    """One pixel's matched line position across runs/segments (needs
    adc_peaks — stage 2): run, segment, start time, run HV, centroid
    +- error. THE stability/trending plot (e.g. CE 976 vs time)."""
    import pandas as pd

    def _query(s):
        stmt = (
            select(Run, RunPixel.segment_index, ADCPeak)
            .join(RunPixel, RunPixel.run_number == Run.run_number)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .join(SpectrumFit,
                  SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
            .join(ADCPeak, ADCPeak.spectrum_fit_id == SpectrumFit.id)
            .join(IsotopeDecayEnergy,
                  ADCPeak.isotope_decay_energy_id == IsotopeDecayEnergy.id)
            .where(RunPixel.pixel_number == pixel,
                   TrapFilterOutput.label == tf_label,
                   IsotopeDecayEnergy.label == line_label)
            .order_by(Run.run_number, RunPixel.segment_index))
        if run_numbers is not None:
            stmt = stmt.where(Run.run_number.in_(run_numbers))
        rows = []
        for r, seg, peak in s.execute(stmt).all():
            rows.append({"run": r.run_number, "segment": seg,
                         "start": r.start_time, "hv": r.hv,
                         "centroid_adc": peak.centroid_adc,
                         "centroid_error_adc": peak.centroid_error_adc})
        return pd.DataFrame(rows)
    return _with_session(_query, session)


def calibration_map(run_numbers=None, calibration_type="linear",
                    current_only=True, label=None, session=None):
    """One row per stored calibration: run, segment, pixel, detector,
    constant +- err (keV), gain +- err (keV/ADC), quadratic when
    present, reduced chi2, n points. Pivot on pixel for detector maps
    of gain/offset; compare runs for stability."""
    import pandas as pd

    def _query(s):
        stmt = (
            select(RunPixel.run_number, RunPixel.segment_index,
                   RunPixel.pixel_number, Calibration)
            .join(Calibration, Calibration.run_pixel_id == RunPixel.id)
            .order_by(RunPixel.run_number, RunPixel.segment_index,
                      RunPixel.pixel_number))
        if run_numbers is not None:
            stmt = stmt.where(RunPixel.run_number.in_(run_numbers))
        if calibration_type is not None:
            stmt = stmt.where(
                Calibration.calibration_type == calibration_type)
        if current_only:
            stmt = stmt.where(Calibration.is_current)
        if label is not None:
            stmt = stmt.where(Calibration.label == label)
        rows = []
        for run, seg, pix, cal in s.execute(stmt).all():
            rows.append({
                "run": run, "segment": seg, "pixel": pix,
                "detector": _detector_of(pix), "type":
                    cal.calibration_type, "label": cal.label,
                "constant_kev": cal.constant_term,
                "constant_error": cal.constant_error,
                "gain_kev_per_adc": cal.linear_term,
                "gain_error": cal.linear_error,
                "quadratic": cal.quadratic_term,
                "reduced_chi2": cal.reduced_chi2,
                "n_points": len(cal.points),
                "run_hv_kv": (cal.config or {}).get("run_hv_kv"),
            })
        return pd.DataFrame(rows)
    return _with_session(_query, session)


def calibration_points_table(run, pixel, segment=0,
                             calibration_type="linear", label=None,
                             session=None):
    """The points behind one pixel's calibration, with residuals: line,
    ADC +- err, target keV +- err, fitted keV, residual (keV). THE
    calibration QA plot (points + residuals vs energy)."""
    import pandas as pd

    def _query(s):
        stmt = (
            select(Calibration)
            .join(RunPixel, Calibration.run_pixel_id == RunPixel.id)
            .where(RunPixel.run_number == run,
                   RunPixel.segment_index == segment,
                   RunPixel.pixel_number == pixel,
                   Calibration.calibration_type == calibration_type))
        if label is not None:
            stmt = stmt.where(Calibration.label == label)
        cal = s.execute(stmt).scalars().first()
        if cal is None:
            raise LookupError(f"no {calibration_type} calibration for "
                              f"run {run} s{segment} p{pixel}")
        shift_list = (cal.config or {}).get("hv_shift_kev") or [0]
        shift = shift_list[0] if len(set(shift_list)) == 1 else None
        rows = []
        for point in cal.points:
            adc = point.adc_peak.centroid_adc
            target = point.kev_peak.energy_kev + (shift or 0)
            fitted = (cal.constant_term + cal.linear_term * adc
                      + (cal.quadratic_term or 0) * adc * adc)
            rows.append({
                "line": point.adc_peak.isotope_decay_energy.label,
                "adc": adc,
                "adc_error": point.adc_peak.centroid_error_adc,
                "target_kev": target,
                "target_error_kev": point.kev_peak.energy_error_kev,
                "fitted_kev": fitted,
                "residual_kev": fitted - target,
            })
        return pd.DataFrame(sorted(rows, key=lambda r: r["adc"]))
    return _with_session(_query, session)


def source_map(run, segment=0, session=None):
    """Which source/isotope each pixel is assigned to in one segment:
    pixel, detector, isotope, source label — the claim map behind a
    hitmap."""
    import pandas as pd

    def _query(s):
        rows = []
        stmt = (select(RunPixel)
                .where(RunPixel.run_number == run,
                       RunPixel.segment_index == segment,
                       RunPixel.source_id.is_not(None))
                .order_by(RunPixel.pixel_number))
        for rp in s.execute(stmt).scalars():
            rows.append({"pixel": rp.pixel_number,
                         "detector": _detector_of(rp.pixel_number),
                         "isotope": rp.source.isotope.name,
                         "source": rp.source.label})
        return pd.DataFrame(rows)
    return _with_session(_query, session)
