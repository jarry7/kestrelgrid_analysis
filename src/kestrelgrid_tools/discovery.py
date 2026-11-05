"""Parse the observed nginx per-WMU directory listings."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class RemoteFile:
    wmu: str
    name: str
    url: str
    start: datetime
    size_label: str

    @property
    def kind(self):
        return self.name.rsplit(".", 1)[-1].lower()

    @property
    def end(self):
        from datetime import timedelta

        zone = ZoneInfo("America/Los_Angeles")
        hourly = len(self.name.split(".")[0]) > 10
        if hourly:
            return self.start + timedelta(hours=1)
        return (self.start.astimezone(zone) + timedelta(days=1)).astimezone(timezone.utc)


def parse_listing(html: str, base_url: str, wmu: str, partition_timezone="America/Los_Angeles") -> list[RemoteFile]:
    result = []
    base = urlsplit(base_url)
    for link in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        url = urljoin(base_url, link["href"])
        parsed = urlsplit(url)
        name = unquote(parsed.path.rsplit("/", 1)[-1])
        match = re.fullmatch(
            r"(\d{4}-\d{2}-\d{2})(?:[T_ -](\d{2})(?:[-_:](\d{2}))?)?\.(raw|7z|parquet)",
            name,
        )
        if (
            not match
            or parsed.scheme != "https"
            or parsed.netloc != base.netloc
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith(base.path)
        ):
            continue
        date, hour, minute, _ = match.groups()
        try:
            start = datetime.fromisoformat(f"{date}T{hour or '00'}:{minute or '00'}:00").replace(
                tzinfo=ZoneInfo(partition_timezone)
            ).astimezone(timezone.utc)
        except ValueError:
            continue
        label = str(link.next_sibling or "").strip().split()
        result.append(RemoteFile(wmu, name, url, start, label[-1] if label else "unknown"))
    return sorted(result, key=lambda item: (item.start, item.name))


def select_files(files, kinds, start=None, end=None):
    """Select daily/hourly partitions. Latest selects a closed date partition."""
    candidates = [item for item in files if item.kind in kinds]
    if start is None:
        local_zone = ZoneInfo("America/Los_Angeles")
        today = datetime.now(local_zone).date()
        candidates = [item for item in candidates if item.start.astimezone(local_zone).date() < today]
        if not candidates:
            raise ValueError("No closed recording partitions found")
        newest = max(item.start for item in candidates)
        candidates = [item for item in candidates if item.start == newest]
    else:
        # Partition width is determined by the observed filename, not assumed cadence.
        candidates = [
            item
            for item in candidates
            if item.start < end
            and item.end > start
        ]
    # Prefer uncompressed raw when both versions of the same partition exist.
    raw_starts = {item.start for item in candidates if item.kind == "raw"}
    return [item for item in candidates if item.kind != "7z" or item.start not in raw_starts]
