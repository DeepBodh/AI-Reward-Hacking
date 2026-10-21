"""Run the offline smoke test in CI: the site, the hints, the log, and the guard."""
from harness.smoke_test import run_offline_smoke


def test_offline_smoke_passes():
    problems = run_offline_smoke(port=8031)
    assert problems == [], problems
