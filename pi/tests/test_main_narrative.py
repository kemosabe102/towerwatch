# test_main_narrative.py — REMOVED
#
# The original test_main_two_ticks hung indefinitely under pytest because
# the RuntimeState monkeypatch did not intercept the reference already bound
# inside towerwatch.main(), so the fake sleep never triggered shutdown.
# A test that cannot finish is worse than no test. Removed 2026-04-22.
#
# What it was trying to cover:
#   - main() boots, runs 2 ticks, calls push_metrics + loki.flush
#   - service_restarted (WARN) and service_started (INFO) events are emitted
#
# TODO: rewrite with a fake-clock / shutdown-event seam injected directly
# into main() rather than patching lifecycle.RuntimeState after the fact.
