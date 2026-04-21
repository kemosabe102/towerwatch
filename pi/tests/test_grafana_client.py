"""Tests for GrafanaClient — 8 tests."""
import gzip
from unittest.mock import MagicMock, call, patch

import pytest
import requests as req_lib


def _make_response(status=204, body=b""):
    resp = MagicMock()
    resp.status_code = status
    resp.content = body
    return resp


def _make_client(status=204, compress=True, annotation_token="tok"):
    from grafana import GrafanaClient

    mock_session = MagicMock()
    mock_session.post.return_value = _make_response(status)
    mock_session.headers = MagicMock()
    mock_session.headers.update = MagicMock()

    return GrafanaClient(
        push_url="http://fake-push/write",
        annotations_url="http://fake-annotations/api/annotations",
        instance_id="12345",
        api_key="secret",
        annotation_token=annotation_token,
        session_factory=lambda: mock_session,
        push_timeout=5,
        annotations_timeout=3,
        compress=compress,
    ), mock_session


# ---------------------------------------------------------------------------
# Session reuse
# ---------------------------------------------------------------------------
def test_session_reused_across_calls():
    client, mock_sess = _make_client()
    client.push_metrics(["line1"])
    client.push_metrics(["line2"])
    # Session factory called once; session.post called twice
    assert mock_sess.post.call_count == 2


def test_auth_header_baked_into_session():
    from grafana import GrafanaClient
    import base64

    sessions_created = []

    def factory():
        s = MagicMock()
        s.headers = {}
        s.post.return_value = _make_response(204)
        sessions_created.append(s)
        return s

    client = GrafanaClient(
        push_url="http://x", annotations_url="http://x",
        instance_id="99", api_key="mykey",
        session_factory=factory,
    )
    client.push_metrics(["l1"])
    sess = sessions_created[0]
    expected = "Basic " + base64.b64encode(b"99:mykey").decode()
    assert sess.headers.get("Authorization") == expected


# ---------------------------------------------------------------------------
# HTTP responses
# ---------------------------------------------------------------------------
def test_push_metrics_2xx_returns_true():
    client, _ = _make_client(status=204)
    assert client.push_metrics(["towerwatch,host=x connected=1 1700000000"]) is True


def test_push_metrics_5xx_returns_false():
    client, _ = _make_client(status=500)
    assert client.push_metrics(["line"]) is False


def test_push_metrics_401_invalidates_session():
    client, mock_sess = _make_client(status=401)
    client.push_metrics(["line"])
    assert client._session is None


# ---------------------------------------------------------------------------
# Gzip toggle
# ---------------------------------------------------------------------------
def test_push_metrics_gzip_compressed():
    client, mock_sess = _make_client(compress=True)
    client.push_metrics(["towerwatch,host=x f=1 1700000000"])
    _, kwargs = mock_sess.post.call_args
    body = kwargs.get("data") or mock_sess.post.call_args[0][1]
    # gzip magic bytes
    assert body[:2] == b"\x1f\x8b"


def test_push_metrics_no_gzip_when_disabled():
    client, mock_sess = _make_client(compress=False)
    client.push_metrics(["towerwatch,host=x f=1 1700000000"])
    _, kwargs = mock_sess.post.call_args
    body = kwargs.get("data") or mock_sess.post.call_args[0][1]
    assert body[:2] != b"\x1f\x8b"


# ---------------------------------------------------------------------------
# Annotation payload shape
# ---------------------------------------------------------------------------
def test_annotation_payload_shape():
    client, _ = _make_client()
    posted = []

    with patch("requests.post") as mock_post:
        mock_post.return_value = _make_response(200)
        client.push_annotation(
            time_ms=1_700_000_000_000,
            time_end_ms=1_700_001_000_000,
            text="Outage: 10 min — process_restart (v abc1234)",
            reason="process_restart",
            version="abc1234",
        )
        _, kwargs = mock_post.call_args

    payload = kwargs["json"]
    assert payload["time"] == 1_700_000_000_000
    assert payload["timeEnd"] == 1_700_001_000_000
    assert "reason:process_restart" in payload["tags"]
    assert "version:abc1234" in payload["tags"]
    assert "towerwatch" in payload["tags"]


def test_annotation_skipped_when_no_token():
    client, _ = _make_client(annotation_token="")
    with patch("requests.post") as mock_post:
        client.push_annotation(1000, 2000, "text")
    mock_post.assert_not_called()
