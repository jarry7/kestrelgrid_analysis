"""Serial HTTPS discovery/downloads with a shared transfer budget and bounded ranges."""

from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pandas as pd
import requests
import truststore
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .common import sha256, write_json
from .credentials import load_credentials
from .discovery import parse_listing
from .raw_validation import MAX_FRAME_BYTES, align_frames, header_at, validate_raw

BASE_URL = "https://data.kestrelgrid.com/"


class KGridClient:
    def __init__(self, credentials=None, max_download_mib=100, session=None):
        truststore.inject_into_ssl()
        self.session = session or requests.Session()
        self.session.headers.update({"Accept-Encoding": "identity"})
        if session is None:
            self.session.auth = load_credentials(credentials)
            retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 502, 503, 504])
            self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.max_bytes = int(max_download_mib * 1024**2)
        self.transferred = 0
        self.listings = {}

    @property
    def remaining(self):
        return self.max_bytes - self.transferred

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https" or parsed.netloc != urlsplit(BASE_URL).netloc
            or parsed.query or parsed.fragment
        ):
            raise ValueError("Only the approved KGRID HTTPS host is allowed")
        response = self.session.request(
            method, url, timeout=(15, 60), allow_redirects=False, stream=True, **kwargs
        )
        if response.status_code == 401:
            response.close()
            raise PermissionError("KGRID authentication failed; check the local credential file")
        if 300 <= response.status_code < 400:
            response.close()
            raise ValueError("Unexpected redirect; credentials were not forwarded")
        try:
            response.raise_for_status()
        except requests.HTTPError:
            status = response.status_code
            response.close()
            raise ValueError(f"KGRID returned HTTP {status}") from None
        return response

    def body(self, response, limit):
        try:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > min(limit, self.remaining):
                raise ValueError("Response exceeds remaining download budget")
            chunks = []
            size = 0
            for chunk in response.iter_content(64 * 1024):
                self.transferred += len(chunk)
                size += len(chunk)
                if size > limit or self.remaining < 0:
                    raise ValueError("Download budget exceeded")
                chunks.append(chunk)
            if declared and size != int(declared):
                raise ValueError("Incomplete HTTP response")
            return b"".join(chunks)
        finally:
            response.close()

    def discover(self, wmu):
        if not str(wmu).isdigit():
            raise ValueError("WMU IDs must be numeric")
        if wmu not in self.listings:
            url = urljoin(BASE_URL, f"{wmu}/")
            html = self.body(self.request("GET", url), 2 * 1024**2).decode()
            self.listings[wmu] = parse_listing(html, url, str(wmu))
        return self.listings[wmu]

    def metadata(self, url):
        with self.request("HEAD", url) as response:
            if not response.headers.get("Content-Length"):
                raise ValueError("Server does not expose file size")
            return {
                "size": int(response.headers["Content-Length"]),
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            }

    def range(self, url, start, end, metadata):
        if not 0 <= start <= end < metadata["size"]:
            raise ValueError("Invalid byte range")
        length = end - start + 1
        if length > self.remaining:
            raise ValueError("Selected range exceeds remaining download budget")
        headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
        if metadata["etag"]:
            headers["If-Match"] = metadata["etag"]
        response = self.request("GET", url, headers=headers)
        if response.status_code != 206:
            response.close()
            raise ValueError("Server ignored byte range; full archive was not downloaded")
        expected = f"bytes {start}-{end}/{metadata['size']}"
        if response.headers.get("Content-Range") != expected:
            response.close()
            raise ValueError("Unexpected Content-Range or changed source size")
        if metadata["etag"] and response.headers.get("ETag") != metadata["etag"]:
            response.close()
            raise ValueError("Source changed during download")
        return self.body(response, length)

    def download(self, remote, destination: Path):
        metadata = self.metadata(remote.url)
        sidecar = destination.with_suffix(destination.suffix + ".json")
        if destination.exists() and sidecar.exists():
            import json

            record = json.loads(sidecar.read_text())
            if (
                record.get("remote") == metadata and record.get("url") == remote.url
                and record.get("sha256") == sha256(destination)
            ):
                return record
        if metadata["size"] > self.remaining:
            raise ValueError(
                f"{remote.name}: {metadata['size'] / 1024**2:.1f} MiB exceeds budget; "
                "increase explicitly or use raw range sampling"
            )
        response = self.request("GET", remote.url)
        if "text/html" in response.headers.get("Content-Type", ""):
            response.close()
            raise ValueError("Expected data, received HTML")
        data = self.body(response, metadata["size"])
        if len(data) != metadata["size"] or self.metadata(remote.url) != metadata:
            raise ValueError("Source changed or download incomplete")
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_suffix(destination.suffix + ".part")
        part.write_bytes(data)
        part.replace(destination)
        record = {"url": remote.url, "remote": metadata, "sha256": sha256(destination)}
        write_json(sidecar, record)
        return record

    def raw_sample(self, remote, destination, duration=60, start=None, end=None, max_samples=5_000_000):
        """Locate frame bounds by bounded probes; fetch only complete selected frames."""
        metadata = self.metadata(remote.url)
        size = metadata["size"]
        if size < 2 * 88:
            raise ValueError("Raw archive is empty or too small")
        probe_size = min(3 * MAX_FRAME_BYTES, size)
        tail = self.range(remote.url, size - probe_size, size - 1, metadata)
        aligned, prefix, tail_frames = align_frames(tail, remote.wmu)
        if size - probe_size + prefix + len(aligned) != size:
            raise ValueError("Archive has an incomplete trailing frame")
        first_data = self.range(remote.url, 0, min(size - 1, 87), metadata)
        first = header_at(first_data)
        if start is None:
            end = tail_frames[-1].end
            start = end - duration
        else:
            start, end = float(start), float(end)
            start = max(start, first.timestamp)
            end = min(end, tail_frames[-1].end)
        if end <= start:
            raise ValueError("End must be after start")
        if (end - start) * max(frame.rate_hz for frame in tail_frames) > max_samples:
            raise ValueError("Requested waveform interval exceeds sample-memory limit")

        def lower_bound(target):
            lo, hi = 0, size
            for _ in range(40):
                if hi - lo <= probe_size:
                    if lo == size:
                        return size
                    data = self.range(remote.url, lo, min(size - 1, hi + probe_size - 1), metadata)
                    if lo == 0:
                        header_at(data)
                    _, skip, frames = align_frames(data, remote.wmu)
                    for frame in frames:
                        if frame.timestamp >= target:
                            return lo + skip + frame.offset
                    return size
                mid = (lo + hi) // 2
                data = self.range(remote.url, mid, min(size - 1, mid + probe_size - 1), metadata)
                _, skip, frames = align_frames(data, remote.wmu)
                absolute = mid + skip
                if frames[0].timestamp >= target:
                    hi = absolute
                else:
                    lo = absolute + frames[0].n_bytes
            raise ValueError("Could not locate recording interval")

        # Include one preceding frame when start falls inside a frame.
        byte_start = lower_bound(start)
        preceding_start = max(0, byte_start - probe_size)
        if byte_start > 0:
            data = self.range(remote.url, preceding_start, min(size - 1, byte_start + probe_size - 1), metadata)
            _, skip, frames = align_frames(data, remote.wmu)
            overlapping = [frame for frame in frames if frame.timestamp < start < frame.end]
            if overlapping:
                byte_start = preceding_start + skip + overlapping[-1].offset
        byte_end = lower_bound(end)
        if byte_start >= byte_end:
            raise ValueError("No raw frames in requested interval")
        data = self.range(remote.url, byte_start, byte_end - 1, metadata)
        frames = validate_raw(data, remote.wmu)
        if sum(frame.n_samples for frame in frames) > max_samples:
            raise ValueError("Decoded frame count exceeds sample-memory limit")
        if self.metadata(remote.url) != metadata:
            raise ValueError("Source changed during range sampling")
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_suffix(".raw.part")
        part.write_bytes(data)
        part.replace(destination)
        return {
            "url": remote.url, "remote": metadata, "byte_range": [byte_start, byte_end - 1],
            "sha256": sha256(destination), "requested_start": start, "requested_end": end,
            "frame_start_utc": pd.to_datetime(frames[0].timestamp, unit="s", utc=True).isoformat(),
            "frame_end_utc": pd.to_datetime(frames[-1].end, unit="s", utc=True).isoformat(),
            "partial_source_download": True,
        }
