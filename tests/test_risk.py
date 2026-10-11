import pytest
from bot.risk import risk_budget, max_shares, daily_limit_hit

def test_risk():
    assert risk_budget(500) == 5
    assert risk_budget(500, .02) == 10
    assert max_shares(500, 100, 99) == 5
    assert max_shares(500, 600, 590) == 0
    assert daily_limit_hit(500, 485)
    assert not daily_limit_hit(500, 490)

def test_bad_stop():
    with pytest.raises(ValueError):
        max_shares(500, 100, 101)
