import time

import pytest

from core.autoresearch_timeout import AutoResearchTimeoutError, call_with_deadline


def test_call_with_deadline_returns_value():
    assert call_with_deadline(lambda: "ok", timeout_seconds=1.0, label="unit") == "ok"


def test_call_with_deadline_raises_quickly_on_slow_call():
    started = time.time()
    with pytest.raises(AutoResearchTimeoutError):
        call_with_deadline(lambda: time.sleep(1.0), timeout_seconds=0.05, label="slow")
    assert time.time() - started < 0.5
