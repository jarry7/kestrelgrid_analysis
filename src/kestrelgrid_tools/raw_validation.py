"""Frame validation and alignment around the pinned upstream RawHeader parser."""

import io
from dataclasses import dataclass

import numpy as np
from kestrelgrid.raw_reader.types import HEADER_LENGTH_BYTES, DataCorruptionError, RawHeader

MAX_FRAME_BYTES = 32767  # Upstream format uses a signed int16 length.


@dataclass
class FrameInfo:
    offset: int
    n_bytes: int
    n_samples: int
    rate_hz: int
    timestamp: float
    wmu: str
    gps_locked: bool
    boot_count: int

    @property
    def end(self):
        return self.timestamp + self.n_samples / self.rate_hz


def header_at(data: bytes, offset: int = 0) -> FrameInfo:
    header = RawHeader(io.BytesIO(data[offset : offset + HEADER_LENGTH_BYTES]))
    if (
        header.n_samples <= 0
        or header.sample_rate <= 0
        or header.n_bytes != HEADER_LENGTH_BYTES + 2 * header.n_samples
        or not np.isfinite(header.timestamp)
        or not np.isfinite(header.scale_factor)
        or header.scale_factor <= 0
    ):
        raise ValueError("Invalid raw frame dimensions, timestamp, or scale")
    return FrameInfo(
        offset, header.n_bytes, header.n_samples, header.sample_rate * 1000,
        header.timestamp, str(header.pmu_id), header.gps_locked, header.boot_count,
    )


def validate_raw(data: bytes, expected_wmu: str | None = None) -> list[FrameInfo]:
    frames = []
    offset = 0
    while offset < len(data):
        try:
            frame = header_at(data, offset)
        except (EOFError, DataCorruptionError, ValueError):
            raise ValueError(f"Invalid or truncated raw header at byte {offset}") from None
        if offset + frame.n_bytes > len(data):
            raise ValueError(f"Truncated raw payload at byte {offset}")
        if expected_wmu is not None and frame.wmu != str(expected_wmu):
            raise ValueError("Raw sensor ID does not match selected WMU")
        if frames and (frame.wmu != frames[0].wmu or frame.timestamp <= frames[-1].timestamp):
            raise ValueError("Mixed sensors or unordered raw frame timestamps")
        frames.append(frame)
        offset += frame.n_bytes
    if not frames:
        raise ValueError("No complete raw frames")
    return frames


def align_frames(data: bytes, expected_wmu: str) -> tuple[bytes, int, list[FrameInfo]]:
    """Find a confirmed consecutive-frame chain inside a bounded byte-range probe."""
    # The little-endian representation of upstream's signed BEEF magic is EF BE.
    cursor = 0
    while True:
        magic = data.find(b"\xef\xbe", cursor)
        if magic < 0:
            raise ValueError("No verifiable raw frame boundary in range response")
        cursor = magic + 1
        offset = magic - 26
        if offset < 0:
            continue
        try:
            first = header_at(data, offset)
            if first.wmu != expected_wmu:
                continue
            end = offset
            count = 0
            while end + HEADER_LENGTH_BYTES <= len(data):
                frame = header_at(data, end)
                if frame.wmu != expected_wmu:
                    raise ValueError("Sensor mismatch")
                if end + frame.n_bytes > len(data):
                    break
                count += 1
                end += frame.n_bytes
            if count < 2:
                continue
            aligned = data[offset:end]
            return aligned, offset, validate_raw(aligned, expected_wmu)
        except (EOFError, DataCorruptionError, ValueError):
            continue
