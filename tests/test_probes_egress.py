"""Tests for EgressProbe — parses Cloudflare's /cdn-cgi/trace for egress IP + colo,
flags CGNAT. No patching: session injected.
"""

from tests.fakes import FakeResponse, FakeSession

# A representative /cdn-cgi/trace body (key=value lines).
_TRACE_BODY = (
    "fl=123abc\n"
    "h=cloudflare.com\n"
    "ip=203.0.113.45\n"
    "ts=1720000000.123\n"
    "visit_scheme=https\n"
    "uag=curl/8.0\n"
    "colo=SEA\n"
    "http=http/2\n"
    "loc=US\n"
)

_TRACE_BODY_CGNAT = _TRACE_BODY.replace("ip=203.0.113.45", "ip=100.64.12.9")


def _build(responses):
    from towerwatch.probes.egress import EgressProbe

    session = FakeSession(get_responses=responses)
    probe = EgressProbe(session=session, url="http://fake/trace", timeout_s=5)
    return probe, session


def test_parses_ip_and_colo():
    probe, _ = _build([FakeResponse(status_code=200, text=_TRACE_BODY)])
    fields = probe.measure()
    assert fields["egress_ip"] == "203.0.113.45"
    assert fields["egress_colo"] == "SEA"
    assert fields["egress_cgnat"] == 0


def test_flags_cgnat_ip():
    probe, _ = _build([FakeResponse(status_code=200, text=_TRACE_BODY_CGNAT)])
    fields = probe.measure()
    assert fields["egress_ip"] == "100.64.12.9"
    assert fields["egress_cgnat"] == 1


def test_returns_empty_on_fetch_exception():
    """A transient fetch failure returns {} — no fields — so it can't fabricate a
    change (the handler treats absent as 'no reading', not 'IP disappeared')."""
    probe, _ = _build([RuntimeError("connection reset")])
    assert probe.measure() == {}


def test_returns_empty_when_ip_missing_from_body():
    """A 200 with no ip= line (unexpected body) yields {} rather than a bogus IP."""
    probe, _ = _build([FakeResponse(status_code=200, text="colo=SEA\nloc=US\n")])
    assert probe.measure() == {}


def test_hits_configured_url():
    probe, session = _build([FakeResponse(status_code=200, text=_TRACE_BODY)])
    probe.measure()
    assert session.get_calls[0][0] == "http://fake/trace"
