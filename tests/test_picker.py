"""The picker engine: keys, cursor, the two loops, and stepping back.

Every screen is driven through its injected key source, so selecting,
toggling, stepping back and abandoning are all exercised headlessly. Key
decoding is tested with the literal byte sequences both platforms emit - note
that this proves the DECODER on both, not the raw-mode setup that produces
those bytes, which only a real console on each platform can exercise.
"""

from __future__ import annotations

import pytest

from biosextract import picker
from biosextract.picker import ALL, BACK, DOWN, OTHER, QUIT, SELECT, TOGGLE, UP


def _keys(*seq):
    """A read() that plays a scripted sequence, then abandons."""
    it = iter(seq)

    def read():
        return next(it, b"q")

    return read


def _sink():
    out: list[str] = []
    return out, out.append


def _rows(*names, unselectable=()):
    return [
        picker.Row(
            text=name,
            value=name,
            selectable=name not in unselectable,
            reason=f"{name} is not available",
        )
        for name in names
    ]


# --------------------------------------------------------------------------
# decoding - both platforms' byte sequences
# --------------------------------------------------------------------------


def test_windows_arrow_sequences():
    assert picker.decode_key(b"\xe0H") == UP
    assert picker.decode_key(b"\xe0P") == DOWN
    assert picker.decode_key(b"\x00H") == UP        # the other lead-in byte
    assert picker.decode_key(b"\x00P") == DOWN


def test_posix_arrow_sequences():
    assert picker.decode_key(b"\x1b[A") == UP
    assert picker.decode_key(b"\x1b[B") == DOWN


def test_back_and_quit_are_different_keys():
    """The divergence from cudem-extract, asserted rather than commented."""
    assert picker.decode_key(b"\x1b") == BACK       # bare escape steps back
    assert picker.decode_key(b"q") == QUIT
    assert picker.decode_key(b"Q") == QUIT
    assert picker.decode_key(b"\x03") == QUIT       # Ctrl-C abandons


def test_select_and_toggle_keys():
    assert picker.decode_key(b"\r") == SELECT
    assert picker.decode_key(b"\n") == SELECT
    assert picker.decode_key(b" ") == TOGGLE
    assert picker.decode_key(b"a") == ALL
    assert picker.decode_key(b"A") == ALL


def test_lead_in_byte_alone_is_not_an_arrow():
    assert picker.decode_key(b"\xe0") == OTHER
    assert picker.decode_key(b"\x00") == OTHER


def test_unknown_and_empty_keys():
    assert picker.decode_key(b"z") == OTHER
    assert picker.decode_key(b"\x1b[C") == OTHER    # left/right: ignored
    # Exhausted input abandons rather than stepping back, which would loop.
    assert picker.decode_key(b"") == QUIT


# --------------------------------------------------------------------------
# cursor movement
# --------------------------------------------------------------------------


def test_move_wraps_at_both_ends():
    assert picker.move(0, 3, UP) == 2
    assert picker.move(2, 3, DOWN) == 0
    assert picker.move(0, 3, DOWN) == 1
    assert picker.move(2, 3, UP) == 1


def test_move_ignores_other_actions():
    assert picker.move(1, 3, OTHER) == 1
    assert picker.move(1, 3, SELECT) == 1


def test_move_on_an_empty_or_short_list():
    assert picker.move(0, 0, DOWN) == 0
    assert picker.move(5, 2, OTHER) == 1            # clamped back in range
    assert picker.move(0, 1, DOWN) == 0             # single item, no movement


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def test_cursor_marks_exactly_one_row():
    lines = picker.render_rows(_rows("a", "b", "c"), 1)
    assert [line.startswith(">") for line in lines] == [False, True, False]


def test_multi_select_boxes_distinguish_unchosen_from_unchoosable():
    lines = picker.render_rows(_rows("a", "b", "c", unselectable=("c",)), 0, {0})
    assert "[x] a" in lines[0]
    assert "[ ] b" in lines[1]
    assert "[-] c" in lines[2], "an unselectable row must not look merely unchosen"


def test_single_select_draws_no_boxes():
    assert picker.render_rows(_rows("a"), 0) == ["> a"]


# --------------------------------------------------------------------------
# choose_one
# --------------------------------------------------------------------------


def test_enter_returns_the_highlighted_row():
    _, write = _sink()
    got = picker.choose_one(
        _rows("a", "b", "c"), "pick", read=_keys(b"\xe0P", b"\xe0P", b"\r"),
        write=write, redraw=False,
    )
    assert got.is_picked and got.value == "c"


def test_wraparound_through_the_loop():
    """Up from the first row lands on the last."""
    _, write = _sink()
    got = picker.choose_one(
        _rows("a", "b", "c"), "pick", read=_keys(b"\x1b[A", b"\r"),
        write=write, redraw=False,
    )
    assert got.value == "c"


def test_unknown_keys_do_not_move_or_select():
    _, write = _sink()
    got = picker.choose_one(
        _rows("a", "b"), "pick", read=_keys(b"z", b"\xe0", b"\r"),
        write=write, redraw=False,
    )
    assert got.value == "a"


def test_escape_steps_back_and_q_abandons():
    _, write = _sink()
    back = picker.choose_one(_rows("a"), "pick", read=_keys(b"\x1b"), write=write, redraw=False)
    quit_ = picker.choose_one(_rows("a"), "pick", read=_keys(b"q"), write=write, redraw=False)
    assert back.is_back and not back.is_quit
    assert quit_.is_quit and not quit_.is_back


def test_selecting_an_unselectable_row_explains_and_stays_open():
    out, write = _sink()
    got = picker.choose_one(
        _rows("broken", "fine", unselectable=("broken",)),
        "pick",
        read=_keys(b"\r", b"\xe0P", b"\r"),
        write=write,
        redraw=False,
    )
    assert got.value == "fine", "must not return a row it refused"
    assert any("broken is not available" in line for line in out)


def test_exhausted_input_abandons_rather_than_hanging():
    _, write = _sink()
    got = picker.choose_one(_rows("a"), "pick", read=lambda: b"", write=write, redraw=False)
    assert got.is_quit


def test_an_empty_screen_is_an_error():
    with pytest.raises(picker.PickerError):
        picker.choose_one([], "pick", read=_keys(b"\r"), write=lambda s: None, redraw=False)


def test_instructions_are_drawn_on_the_screen():
    out, write = _sink()
    picker.choose_one(_rows("a"), "pick a thing", read=_keys(b"\r"), write=write, redraw=False)
    text = "".join(out)
    assert "pick a thing" in text
    assert "enter select" in text and "esc back" in text and "q quit" in text


def test_redraw_emits_cursor_escapes_only_when_enabled():
    plain, write_plain = _sink()
    picker.choose_one(_rows("a", "b"), "pick", read=_keys(b"\xe0P", b"\r"),
                      write=write_plain, redraw=False)
    assert not any("\x1b[" in line for line in plain)

    ansi, write_ansi = _sink()
    picker.choose_one(_rows("a", "b"), "pick", read=_keys(b"\xe0P", b"\r"),
                      write=write_ansi, redraw=True)
    assert any("\x1b[" in line for line in ansi)


# --------------------------------------------------------------------------
# choose_many
# --------------------------------------------------------------------------


def test_space_toggles_on_and_off():
    _, write = _sink()
    got = picker.choose_many(
        _rows("a", "b", "c"),
        "pick some",
        # select a, move to b, select b, move back to a, unselect a
        read=_keys(b" ", b"\xe0P", b" ", b"\x1b[A", b" ", b"\r"),
        write=write,
        redraw=False,
    )
    assert got.value == ["b"]


def test_nothing_is_preselected():
    out, write = _sink()
    got = picker.choose_many(
        _rows("a", "b"), "pick some", read=_keys(b"\r", b" ", b"\r"),
        write=write, redraw=False,
    )
    assert got.value == ["a"], "enter with nothing toggled must not confirm a default"
    assert any("nothing selected" in line for line in out)


def test_select_all_picks_exactly_the_runnable_rows():
    _, write = _sink()
    got = picker.choose_many(
        _rows("a", "gated", "b", unselectable=("gated",)),
        "pick some",
        read=_keys(b"a", b"\r"),
        write=write,
        redraw=False,
    )
    assert got.value == ["a", "b"]


def test_select_all_again_clears_the_selection():
    out, write = _sink()
    got = picker.choose_many(
        _rows("a", "b"), "pick some", read=_keys(b"a", b"a", b"\r", b" ", b"\r"),
        write=write, redraw=False,
    )
    assert got.value == ["a"]
    assert any("nothing selected" in line for line in out)


def test_toggling_an_unselectable_row_prints_its_reason():
    out, write = _sink()
    got = picker.choose_many(
        _rows("gated", "a", unselectable=("gated",)),
        "pick some",
        read=_keys(b" ", b"\xe0P", b" ", b"\r"),
        write=write,
        redraw=False,
    )
    assert got.value == ["a"]
    assert any("gated is not available" in line for line in out)


def test_multi_select_instructions_are_drawn():
    out, write = _sink()
    picker.choose_many(_rows("a"), "pick some", read=_keys(b" ", b"\r"),
                       write=write, redraw=False)
    text = "".join(out)
    assert "space toggle" in text and "enter confirm" in text


def test_multi_select_returns_rows_in_list_order():
    _, write = _sink()
    got = picker.choose_many(
        _rows("a", "b", "c"),
        "pick some",
        # toggle c first, then a: the answer still reads down the screen
        read=_keys(b"\x1b[A", b" ", b"\xe0P", b" ", b"\r"),
        write=write,
        redraw=False,
    )
    assert got.value == ["a", "c"]


# --------------------------------------------------------------------------
# sequencing
# --------------------------------------------------------------------------


def _step(*choices):
    """A step that returns the given choices in turn, once per visit."""
    it = iter(choices)
    visits: list[list] = []

    def step(answers):
        visits.append(list(answers))
        return next(it)

    step.visits = visits
    return step


def test_walk_collects_one_answer_per_screen():
    steps = [_step(picker.Choice.picked("study")), _step(picker.Choice.picked(["mpa"]))]
    assert picker.walk(steps) == ["study", ["mpa"]]


def test_escape_on_a_later_screen_steps_back_into_the_previous_one():
    first = _step(picker.Choice.picked("a"), picker.Choice.picked("b"))
    second = _step(picker.Choice.back(), picker.Choice.picked("z"))
    assert picker.walk([first, second]) == ["b", "z"]
    assert len(first.visits) == 2, "stepping back must redraw the earlier screen"


def test_a_screen_stepped_back_into_sees_the_answers_so_far():
    first = _step(picker.Choice.picked("a"), picker.Choice.picked("a"))
    second = _step(picker.Choice.back(), picker.Choice.picked("z"))
    picker.walk([first, second])
    assert second.visits[0] == ["a", None]


def test_escape_on_the_first_screen_exits():
    assert picker.walk([_step(picker.Choice.back())]) is None


def test_the_quit_key_abandons_from_any_screen():
    first = _step(picker.Choice.picked("a"))
    second = _step(picker.Choice.abandoned())
    assert picker.walk([first, second]) is None


# --------------------------------------------------------------------------
# the TTY guard
# --------------------------------------------------------------------------


class _Stream:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


def test_can_pick_needs_both_streams():
    assert picker.can_pick(_Stream(True), _Stream(True))
    assert not picker.can_pick(_Stream(True), _Stream(False))
    assert not picker.can_pick(_Stream(False), _Stream(True))
    assert not picker.can_pick(object(), object())      # no isatty at all
