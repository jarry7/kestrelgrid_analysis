import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from kestrelgrid_tools.client import BASE_URL, KGridClient
from kestrelgrid_tools.credentials import load_credentials
from kestrelgrid_tools.discovery import RemoteFile, parse_listing, select_files


class Response:
    def __init__(self, status=200, data=b"", headers=None):
        self.status_code, self.data = status, data
        self.headers = headers or {"Content-Length": str(len(data))}
        self.closed = False

    def iter_content(self, _):
        yield self.data

    def close(self):
        self.closed = True

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def client_with(response, budget=1):
    session = Mock()
    session.headers = {}
    session.request.return_value = response
    return KGridClient(session=session, max_download_mib=budget)


def test_credentials_and_permissions(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"username": "user", "password": "fake secret"}))
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        load_credentials(path)
    path.chmod(0o600)
    assert load_credentials(path) == ("user", "fake secret")
    path.write_text('{"username":"u"}')
    with pytest.raises(ValueError):
        load_credentials(path)


def test_directory_and_pacific_partition_selection():
    html = '''<a href="../">parent</a><a href="2026-11-04.raw">raw</a> 661M
    <a href="2026-11-04.7z">zip</a> 600M
    <a href="2026-11-05.raw">raw</a> 661M
    <a href="https://elsewhere.test/2026-11-04.raw">external</a>'''
    files = parse_listing(html, BASE_URL + "4021/", "4021")
    assert len(files) == 3
    assert files[0].start.isoformat() == "2026-11-04T08:00:00+00:00"
    import pandas as pd

    chosen = select_files(files, {"raw", "7z"}, pd.Timestamp("2026-11-05T07:59:00Z"), pd.Timestamp("2026-11-05T08:00:00Z"))
    assert [item.name for item in chosen] == ["2026-11-04.raw"]


def test_auth_host_redirect_and_range_fallback():
    for status, error in ((401, PermissionError), (302, ValueError)):
        client = client_with(Response(status))
        with pytest.raises(error):
            client.request("GET", BASE_URL)
    client = client_with(Response(200, data=b"too large"))
    with pytest.raises(ValueError, match="approved"):
        client.request("GET", "https://other.test/")
    with pytest.raises(ValueError, match="ignored byte range"):
        client.range(BASE_URL + "a.raw", 0, 9, {"size": 100, "etag": None})
    assert client.transferred == 0


def test_budget_and_short_response():
    client = client_with(Response(data=b"abcd", headers={"Content-Length": "5"}))
    with pytest.raises(ValueError, match="Incomplete"):
        client.body(client.request("GET", BASE_URL), 10)
    response = Response(data=b"abcd", headers={})
    client = client_with(response, budget=3 / 1024**2)
    with pytest.raises(ValueError, match="budget"):
        client.body(response, 10)
    assert response.closed


def test_range_frame_search_exact_interval(tmp_path, raw_bytes):
    data = raw_bytes(count=200)
    metadata = {"size": len(data), "etag": '"fixed"', "last_modified": None}
    client = client_with(Response())
    client.metadata = lambda _: metadata
    requested = []

    def get_range(url, start, end, meta):
        requested.append((start, end))
        return data[start:end + 1]

    client.range = get_range
    from datetime import datetime, timezone

    remote = RemoteFile("4021", "2023-11-14.raw", BASE_URL + "4021/file.raw", datetime.now(timezone.utc), "1M")
    output = tmp_path / "sample.raw"
    record = client.raw_sample(remote, output, start=1_700_000_130.25, end=1_700_000_132.75)
    import pandas as pd

    from kestrelgrid_tools.interpretation import read_waveform

    table, _ = read_waveform(output, pd.to_datetime(record["requested_start"], unit="s", utc=True), pd.to_datetime(record["requested_end"], unit="s", utc=True))
    assert len(table) == 10000
    assert output.stat().st_size < len(data)
    assert all(end - start < len(data) for start, end in requested)


def test_changed_source_rejected(tmp_path, raw_bytes):
    data = raw_bytes(count=8)
    client = client_with(Response())
    metadata = {"size": len(data), "etag": '"fixed"', "last_modified": None}
    client.metadata = Mock(side_effect=[metadata, {**metadata, "etag": '"changed"'}])
    client.range = lambda _, start, end, meta: data[start:end + 1]
    from datetime import datetime, timezone

    remote = RemoteFile("4021", "sample.raw", BASE_URL + "4021/sample.raw", datetime.now(timezone.utc), "1M")
    with pytest.raises(ValueError, match="changed"):
        client.raw_sample(remote, Path(tmp_path) / "sample.raw", duration=2)
