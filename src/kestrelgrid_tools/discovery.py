"""Parse the observed nginx per-WMU directory listings."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class RemoteFile:
    """A discovered source partition with WMU, URL, filename and UTC start time."""

    wmu: str
    name: str
    url: str
    start: datetime
    size_label: str

    @property
    def kind(self):
        """Return the lower-case raw, 7z or Parquet extension."""
        return self.name.rsplit(".", 1)[-1].lower()

    @property
    def end(self):
        """Return the partition end, respecting Pacific DST for daily filenames."""
        from datetime import timedelta

        zone = ZoneInfo("America/Los_Angeles")
        hourly = len(self.name.split(".")[0]) > 10
        if hourly:
            return self.start + timedelta(hours=1)
        return (self.start.astimezone(zone) + timedelta(days=1)).astimezone(timezone.utc)


def parse_listing(
    html: str, base_url: str, wmu: str, partition_timezone="America/Los_Angeles"
) -> list[RemoteFile]:
    """Parse safe same-host date/hour filenames into sorted recording partitions."""
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
            start = (
                datetime.fromisoformat(f"{date}T{hour or '00'}:{minute or '00'}:00")
                .replace(tzinfo=ZoneInfo(partition_timezone))
                .astimezone(timezone.utc)
            )
        except ValueError:
            continue
        label = str(link.next_sibling or "").strip().split()
        result.append(RemoteFile(wmu, name, url, start, label[-1] if label else "unknown"))
    return sorted(result, key=lambda item: (item.start, item.name))


def select_files(files, kinds, start, end):
    """Select overlapping daily/hourly partitions, preferring raw to duplicate 7z files."""
    candidates = [item for item in files if item.kind in kinds]
    candidates = [item for item in candidates if item.start < end and item.end > start]
    raw_starts = {item.start for item in candidates if item.kind == "raw"}
    return [item for item in candidates if item.kind != "7z" or item.start not in raw_starts]
