"""Explicit, segment-aware phase-angle frequency conversion."""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PhaseConvention:
    """Units, reference frequency and derivative sign for a WMU's phase-angle product."""

    reference_hz: float = 60.0
    units: str = "radians"
    sign: int = 1

    def __post_init__(self):
        """Validate supported angle units, sign and a finite positive reference frequency."""
        if self.units not in {"radians", "degrees"} or self.sign not in {-1, 1}:
            raise ValueError("Phase units must be radians/degrees and sign must be +1/-1")
        if not np.isfinite(self.reference_hz) or self.reference_hz <= 0:
            raise ValueError("Phase reference must be a positive frequency")

    def to_dict(self):
        """Return JSON-compatible convention parameters."""
        return asdict(self)


def phase_frequency(times, angles, convention: PhaseConvention, max_gap=None):
    """Differentiate unwrapped phase using epoch-second times within continuous segments.

    Return Hz estimates with NaN boundaries, invalid intervals and suspicious jumps.
    Low-rate aliasing is not fully detectable; inputs must use an established convention.
    """
    t = np.asarray(times, dtype="float64")
    theta = np.asarray(angles, dtype="float64")
    if len(t) != len(theta):
        raise ValueError("Phase and timestamp lengths differ")
    if convention.units == "degrees":
        theta = np.deg2rad(theta)
    result = np.full(len(t), np.nan)
    if len(t) < 3:
        return result
    dt = np.diff(t)
    positive = dt[(dt > 0) & np.isfinite(dt)]
    if not len(positive):
        return result
    max_gap = max_gap or 1.5 * np.median(positive)
    valid = np.isfinite(t) & np.isfinite(theta)
    breaks = np.flatnonzero((dt <= 0) | (dt > max_gap) | ~valid[:-1] | ~valid[1:]) + 1
    for indices in np.split(np.arange(len(t)), breaks):
        if len(indices) < 3 or not valid[indices].all():
            continue
        phase = np.unwrap(theta[indices])
        estimate = convention.reference_hz + convention.sign * np.gradient(phase, t[indices]) / (
            2 * np.pi
        )
        # Boundary derivatives are intentionally unavailable. Large phase increments
        # are suspicious, but aliasing can also produce small increments undetectably.
        suspicious = np.flatnonzero(np.abs(np.diff(phase)) >= 0.9 * np.pi)
        estimate[[0, -1]] = np.nan
        estimate[suspicious] = np.nan
        estimate[suspicious + 1] = np.nan
        result[indices] = estimate
    return result


def waveform_frequency(waveform, window_seconds=1.0, step_seconds=0.5):
    """Windowed least-squares cycle count from interpolated positive zero crossings.

    Read batches (or a DataFrame), carry a boundary sample, split at timing/data gaps.
    Only complete one-second windows and consistent 45–65 Hz cycles are accepted.
    This is a waveform estimate, not a standards-compliant PMU frequency algorithm.
    """
    batches = waveform.iter_tables() if hasattr(waveform, "iter_tables") else [waveform]
    segments = []
    current = []
    previous = None
    previous_segment = None
    segment_start = segment_end = None
    locked = True

    def finish():
        """Finalize a contiguous crossing segment and reset estimator state."""
        nonlocal current, segment_start, segment_end, locked
        if current and segment_start is not None:
            segments.append((np.asarray(current), segment_start, segment_end, locked))
        current, segment_start, segment_end, locked = [], None, None, True

    for table in batches:
        if table.empty:
            continue
        times = table.time_utc.astype("int64").to_numpy(dtype="float64") / 1e9
        voltage = table.voltage_v.to_numpy(dtype="float64")
        identifiers = table.segment.to_numpy() if "segment" in table else np.zeros(len(table))
        gps = (
            table.gps_locked.fillna(False).to_numpy(dtype=bool)
            if "gps_locked" in table
            else np.zeros(len(table), dtype=bool)
        )
        if previous is not None:
            times = np.r_[previous[0], times]
            voltage = np.r_[previous[1], voltage]
            identifiers = np.r_[previous_segment, identifiers]
            gps = np.r_[previous[2], gps]
        dt = np.diff(times)
        positive = dt[dt > 0]
        cadence = np.median(positive) if len(positive) else np.inf
        valid = np.isfinite(voltage) & np.isfinite(times)
        breaks = (
            np.flatnonzero(
                (dt <= 0)
                | (dt > 1.5 * cadence)
                | (np.diff(identifiers) != 0)
                | ~valid[:-1]
                | ~valid[1:]
            )
            + 1
        )
        pieces = np.split(np.arange(len(times)), breaks)
        for number, indices in enumerate(pieces):
            if number:
                finish()
            if len(indices) < 2 or not valid[indices].all():
                continue
            t, v = times[indices], voltage[indices]
            segment_start = t[0] if segment_start is None else segment_start
            segment_end = t[-1]
            locked &= bool(gps[indices].all())
            crossings = np.flatnonzero((v[:-1] <= 0) & (v[1:] > 0))
            interpolated = t[crossings] - v[crossings] * (t[crossings + 1] - t[crossings]) / (
                v[crossings + 1] - v[crossings]
            )
            current.extend(interpolated.tolist())
        previous = (times[-1], voltage[-1], gps[-1])
        previous_segment = identifiers[-1]
    finish()

    rows = []
    for crossings, left, right, gps_locked in segments:
        # Align output centers to UTC half-second boundaries, with full window support.
        first_center = np.ceil((left + window_seconds / 2) / step_seconds) * step_seconds
        for center in np.arange(first_center, right - window_seconds / 2, step_seconds):
            begin, finish_index = np.searchsorted(
                crossings, [center - window_seconds / 2, center + window_seconds / 2]
            )
            x = crossings[begin:finish_index]
            frequency, residual = np.nan, np.nan
            accepted = False
            if len(x) >= int(45 * window_seconds) - 2:
                periods = np.diff(x)
                typical = np.median(periods)
                consistent = (
                    (periods >= 1 / 65)
                    & (periods <= 1 / 45)
                    & (np.abs(periods - typical) <= 0.08 * typical)
                ).all()
                if consistent:
                    relative = x - x.mean()
                    cycles = np.arange(len(x), dtype=float)
                    slope = np.dot(relative, cycles - cycles.mean()) / np.dot(relative, relative)
                    residual = float(
                        np.sqrt(np.mean((cycles - cycles.mean() - slope * relative) ** 2))
                    )
                    if 45 <= slope <= 65 and residual < 0.03:
                        frequency, accepted = float(slope), True
            rows.append(
                {
                    "time_utc": pd.to_datetime(center, unit="s", utc=True),
                    "frequency_hz": frequency,
                    "gps_locked": gps_locked,
                    "frequency_valid": accepted,
                    "fit_residual_cycles": residual,
                }
            )
    columns = ["time_utc", "frequency_hz", "gps_locked", "frequency_valid", "fit_residual_cycles"]
    result = pd.DataFrame(rows, columns=columns)
    if not result.empty:
        result = result.sort_values("time_utc").drop_duplicates("time_utc").reset_index(drop=True)
    result.attrs["frequency_source"] = (
        "waveform zero-crossing regression (1 s window, 2 estimates/s)"
    )
    return result


def verify_phase_convention(measurements, raw_frequency):
    """Compare candidate conventions against independent waveform measurements.

    Require signal variation, enough overlap, strong agreement, and a distinct winner.
    Return an evidence-backed result or None; never infer from location alone.
    """
    if "theta" not in measurements or raw_frequency.empty or len(measurements) < 30:
        return None
    t = measurements.time_utc.astype("int64").to_numpy() / 1e9
    raw = raw_frequency[np.isfinite(raw_frequency.frequency_hz)].copy()
    if len(raw) < 30:
        return None
    candidates = []
    for reference in (50.0, 60.0):
        for units in ("radians", "degrees"):
            for sign in (-1, 1):
                convention = PhaseConvention(reference, units, sign)
                estimate = phase_frequency(t, measurements.theta.to_numpy(), convention)
                phase = pd.DataFrame(
                    {"time_utc": measurements.time_utc, "theta_frequency": estimate}
                )
                matched = pd.merge_asof(
                    phase.sort_values("time_utc"),
                    raw.sort_values("time_utc"),
                    on="time_utc",
                    tolerance=pd.Timedelta(milliseconds=250),
                    direction="nearest",
                ).dropna(subset=["theta_frequency", "frequency_hz"])
                if len(matched) < 30:
                    continue
                difference = matched.theta_frequency.to_numpy() - matched.frequency_hz.to_numpy()
                standard_deviation = float(matched.frequency_hz.std())
                correlation = (
                    float(matched.theta_frequency.corr(matched.frequency_hz))
                    if standard_deviation > 1e-6
                    else 0.0
                )
                candidates.append(
                    {
                        "convention": convention.to_dict(),
                        "rmse_hz": float(np.sqrt(np.mean(difference**2))),
                        "median_absolute_error_hz": float(np.median(np.abs(difference))),
                        "correlation": correlation,
                        "comparison_samples": len(matched),
                        "waveform_frequency_std_hz": standard_deviation,
                        "validation_start_utc": matched.time_utc.min().isoformat(),
                        "validation_end_utc": matched.time_utc.max().isoformat(),
                    }
                )
    candidates.sort(key=lambda value: value["rmse_hz"])
    if len(candidates) < 2:
        return None
    best, next_best = candidates[:2]
    if (
        best["rmse_hz"] >= 0.005
        or best["correlation"] < 0.8
        or best["waveform_frequency_std_hz"] < 0.003
        or next_best["rmse_hz"] < max(0.003, 3 * best["rmse_hz"])
    ):
        return None
    return {
        **best,
        "runner_up_rmse_hz": next_best["rmse_hz"],
        "method": "candidate phase derivatives compared to waveform zero-crossing regression",
    }
