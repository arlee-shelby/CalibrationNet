"""Ready-made queries for the questions the analysis actually asks —
"pixel 60 across every run", optionally at one trap setting or one set
of run conditions — so nobody has to rebuild the join chains by hand.

Every helper takes a session plus filters and returns ORM objects (with
enough joined context to plot from directly). All filters are optional
and combine. The underlying chain, for reference:

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

from sqlalchemy import select

from .models import (ADCPeak, Calibration, Isotope, IsotopeDecayEnergy,
                     KeVPeak, Run, RunPixel, SpectrumFit, TrapFilterOutput)


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
