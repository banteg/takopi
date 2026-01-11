from takopi.ralph import (
    RALPH_COMPLETE_PATTERN,
    RalphLoopState,
    check_ralph_complete,
    strip_ralph_complete,
)


def test_ralph_complete_pattern_matches() -> None:
    text = "Done!\nRALPH_COMPLETE: finished the task"
    match = RALPH_COMPLETE_PATTERN.search(text)
    assert match is not None
    assert match.group(1) == "finished the task"


def test_ralph_complete_pattern_multiline() -> None:
    text = """Some output here.
More work done.
RALPH_COMPLETE: all done with the refactor
"""
    match = RALPH_COMPLETE_PATTERN.search(text)
    assert match is not None
    assert match.group(1) == "all done with the refactor"


def test_check_ralph_complete_true() -> None:
    text = "RALPH_COMPLETE: success"
    assert check_ralph_complete(text) is True


def test_check_ralph_complete_false() -> None:
    text = "Still working on it..."
    assert check_ralph_complete(text) is False


def test_check_ralph_complete_in_middle() -> None:
    text = "Done!\nRALPH_COMPLETE: finished\nMore text"
    assert check_ralph_complete(text) is True


def test_strip_ralph_complete_removes_line() -> None:
    text = "Here is the answer\nRALPH_COMPLETE: done"
    result = strip_ralph_complete(text)
    assert result == "Here is the answer"


def test_strip_ralph_complete_preserves_surrounding() -> None:
    text = "Before\nRALPH_COMPLETE: summary here\nAfter"
    result = strip_ralph_complete(text)
    assert result == "Before\n\nAfter"


def test_strip_ralph_complete_no_match() -> None:
    text = "Just regular text"
    result = strip_ralph_complete(text)
    assert result == "Just regular text"


def test_ralph_loop_state_initial() -> None:
    state = RalphLoopState(max_iterations=3)
    assert state.max_iterations == 3
    assert state.current_iteration == 0
    assert state.completed is False


def test_ralph_loop_state_update() -> None:
    state = RalphLoopState(max_iterations=5, current_iteration=2, completed=False)
    assert state.max_iterations == 5
    assert state.current_iteration == 2
    assert state.completed is False
