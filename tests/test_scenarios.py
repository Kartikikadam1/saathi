"""Runs every labelled scenario (TC1–TC12) against the real agent and asserts all checks pass."""
import pytest

from app.testing import scenarios


@pytest.mark.parametrize("tc", scenarios.SCENARIOS, ids=[t["id"] for t in scenarios.SCENARIOS])
def test_scenario(tc):
    r = scenarios.run_one(tc, "PYTEST")
    failed = [c for c in r["checks"] if not c["passed"]]
    assert not failed, f"{tc['id']} failed checks: {failed}\nagent said: {r['actual']['evidence']['agent_lines']}"
