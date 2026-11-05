"""Explicit, segment-aware phase-angle frequency conversion."""

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class PhaseConvention:
    reference_hz: float = 60.0
    units: str = "radians"
    sign: int = 1

    def __post_init__(self):
        if self.units not in {"radians", "degrees"} or self.sign not in {-1, 1}:
            raise ValueError("Phase units must be radians/degrees and sign must be +1/-1")
        if not np.isfinite(self.reference_hz) or self.reference_hz <= 0:
            raise ValueError("Phase reference must be a positive frequency")

    def to_dict(self):
        return asdict(self)


def phase_frequency(times, angles, convention: PhaseConvention, max_gap=None):
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
    breaks = np.flatnonzero(
        (dt <= 0) | (dt > max_gap) | ~valid[:-1] | ~valid[1:]
    ) + 1
    for indices in np.split(np.arange(len(t)), breaks):
        if len(indices) < 3 or not valid[indices].all():
            continue
        phase = np.unwrap(theta[indices])
        estimate = convention.reference_hz + convention.sign * np.gradient(
            phase, t[indices]
        ) / (2 * np.pi)
        # Boundary derivatives are intentionally unavailable. Large phase increments
        # are suspicious, but aliasing can also produce small increments undetectably.
        suspicious = np.flatnonzero(np.abs(np.diff(phase)) >= 0.9 * np.pi)
        estimate[[0, -1]] = np.nan
        estimate[suspicious] = np.nan
        estimate[suspicious + 1] = np.nan
        result[indices] = estimate
    return result
