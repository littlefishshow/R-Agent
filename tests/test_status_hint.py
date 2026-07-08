from main import INTERRUPT_STATUS_HINT, _with_interrupt_status_hint


def test_interrupt_status_hint_is_appended_once():
    message = "[bold cyan]thinking[/bold cyan]"

    hinted = _with_interrupt_status_hint(message)

    assert hinted == f"{message} {INTERRUPT_STATUS_HINT}"
    assert _with_interrupt_status_hint(hinted) == hinted


def test_interrupt_status_hint_handles_empty_message():
    assert _with_interrupt_status_hint("") == INTERRUPT_STATUS_HINT
