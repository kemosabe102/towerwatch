"""Tests for GrafanaClient — no patch, all collaborators injected."""
import base64
import sys
from pathlib import Path

import requests

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeEvents, FakeLoki, FakeResponse, FakeSession


class RecordingPost:
    """Callable stand-in for requests.post."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self._responses:
            raise AssertionError(f"no response queued for {url}")
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _build_client(
    *,
    push_responses=None,
    annotation_responses=None,
    annotation_token="tok",
    compress=True,
    loki=None,
    events=None,
):
    """Helper to assemble a GrafanaClient with injected collaborators."""
    from towerwatch.clients.grafana import GrafanaClient

    session = FakeSession(post_responses=push_responses or [])
    annotation_post = RecordingPost(annotation_responses or [])

    client = GrafanaClient(
        push_url="http://fake-push/write",
        annotations_url="http://fake-annotations/api/annotations",
        instance_id="12345",
        api_key="secret",
        annotation_token=annotation_token,
        session_factory=lambda: session,
        annotation_post=annotation_post,
        push_timeout=5,
        annotations_timeout=3,
        compress=compress,
        loki=loki if loki is not None else FakeLoki(),
        events=events if events is not None else FakeEvents(),
    )
    return client, session, annotation_post


# ---------------------------------------------------------------------------
# Session reuse + auth header
# ---------------------------------------------------------------------------
def test_session_reused_across_calls():
    client, session, _ = _build_client(
        push_responses=[FakeResponse(status_code=204),
                        FakeResponse(status_code=204)])
    client.push_metrics(["line1"])
    client.push_metrics(["line2"])
    assert len(session.post_calls) == 2


def test_auth_header_baked_into_session():
    client, session, _ = _build_client(
        push_responses=[FakeResponse(status_code=204)])
    client.push_metrics(["l1"])
    expected = "Basic " + base64.b64encode(b"12345:secret").decode()
    assert session.headers.get("Authorization") == expected
    assert session.headers.get("Content-Type") == "text/plain"


# ---------------------------------------------------------------------------
# HTTP status handling
# ---------------------------------------------------------------------------
def test_push_metrics_2xx_returns_true():
    client, _, _ = _build_client(
        push_responses=[FakeResponse(status_code=204)])
    assert client.push_metrics(["x connected=1 1700000000"]) is True


def test_push_metrics_5xx_returns_false():
    client, _, _ = _build_client(
        push_responses=[FakeResponse(status_code=500)])
    assert client.push_metrics(["line"]) is False


def test_push_metrics_401_invalidates_session():
    client, _, _ = _build_client(
        push_responses=[FakeResponse(status_code=401)])
    client.push_metrics(["line"])
    assert client._session is None


def test_push_metrics_403_invalidates_session():
    client, _, _ = _build_client(
        push_responses=[FakeResponse(status_code=403)])
    client.push_metrics(["line"])
    assert client._session is None


def test_push_metrics_5xx_does_not_invalidate_session():
    client, session, _ = _build_client(
        push_responses=[FakeResponse(status_code=500)])
    client.push_metrics(["line"])
    assert client._session is session


def test_push_metrics_connection_error_invalidates_and_returns_false():
    client, _, _ = _build_client(
        push_responses=[requests.ConnectionError("network down")])
    result = client.push_metrics(["line"])
    assert result is False
    assert client._session is None


def test_push_metrics_error_emits_metrics_push_failed_event():
    events = FakeEvents()
    client, _, _ = _build_client(
        push_responses=[FakeResponse(status_code=500)],
        events=events,
    )
    client.push_metrics(["line"])
    assert events.called("metrics_push_failed")


# ---------------------------------------------------------------------------
# Gzip toggle
# ---------------------------------------------------------------------------
def test_push_metrics_gzip_compressed():
    client, session, _ = _build_client(
        push_responses=[FakeResponse(status_code=204)], compress=True)
    client.push_metrics(["towerwatch,host=x f=1 1700000000"])
    kwargs = session.post_calls[0][1]
    body = kwargs.get("data", b"")
    assert body[:2] == b"\x1f\x8b"


def test_push_metrics_no_gzip_when_disabled():
    client, session, _ = _build_client(
        push_responses=[FakeResponse(status_code=204)], compress=False)
    client.push_metrics(["towerwatch,host=x f=1 1700000000"])
    kwargs = session.post_calls[0][1]
    body = kwargs.get("data", b"")
    assert body[:2] != b"\x1f\x8b"


# ---------------------------------------------------------------------------
# Annotation path
# ---------------------------------------------------------------------------
def test_annotation_payload_shape():
    client, _, annotation_post = _build_client(
        annotation_responses=[FakeResponse(status_code=200)])
    client.push_annotation(
        time_ms=1_700_000_000_000,
        time_end_ms=1_700_001_000_000,
        text="Outage: 10 min — process_restart (v abc1234)",
        reason="process_restart",
        version="abc1234",
    )
    _, kwargs = annotation_post.calls[0]
    payload = kwargs["json"]
    assert payload["time"] == 1_700_000_000_000
    assert payload["timeEnd"] == 1_700_001_000_000
    assert "reason:process_restart" in payload["tags"]
    assert "version:abc1234" in payload["tags"]


def test_annotation_skipped_when_no_token():
    client, _, annotation_post = _build_client(annotation_token="")
    client.push_annotation(1000, 2000, "text")
    assert annotation_post.calls == []


def test_annotation_non_2xx_emits_annotation_failed():
    events = FakeEvents()
    client, _, _ = _build_client(
        annotation_responses=[FakeResponse(status_code=500)],
        events=events,
    )
    client.push_annotation(1000, 2000, "text", reason="r", version="v")
    assert events.called("annotation_failed")


def test_annotation_exception_emits_annotation_failed():
    events = FakeEvents()
    client, _, _ = _build_client(
        annotation_responses=[requests.Timeout("slow")],
        events=events,
    )
    client.push_annotation(1000, 2000, "text", reason="r", version="v")
    assert events.called("annotation_failed")


def test_annotation_dev_version_omits_version_tag():
    client, _, annotation_post = _build_client(
        annotation_responses=[FakeResponse(status_code=200)])
    client.push_annotation(1000, 2000, "text", reason="r", version="dev")
    tags = annotation_post.calls[0][1]["json"]["tags"]
    assert not any(t.startswith("version:") for t in tags)
