"""Arrow-key selection screens, in the terminal.

Modelled on ``cudem-extract``'s ``bathy/picker.py``, and for the same reason:
everything that decides anything is a pure function - decoding a keypress,
moving the cursor, formatting a row - and the loop takes its key source as an
argument, so a whole screen can be driven from a test with a scripted sequence
of keystrokes. What is left untested is raw-mode setup: about a dozen lines of
termios and msvcrt that no amount of injection can exercise without a real
console.

There is no TUI library in this environment - no curses, no prompt_toolkit, no
readchar - and none is worth adding for two lists, so this is stdlib only:
msvcrt on Windows, termios on POSIX.

WHAT THIS MODULE IS AND IS NOT
------------------------------
The first half is the *engine*: keys, cursor, rows, the two loops, and the
sequencing that lets a screen step back to the one before it. It knows nothing
about studies or datasets. The screens themselves are built out of :class:`Row`
values and live in the second half.

Every screen returns a :class:`Choice`, which distinguishes three outcomes that
a boolean cannot: something was chosen, the user stepped *back*, or the user
abandoned the run. Collapsing back and quit into one "cancel" is what the
sibling toolkit does, correctly, for its single screen; see the key table.
"""

from __future__ import annotations

import os
import sys
import textwrap
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from . import fetch as fetch_mod, studies
from .bbox import BBox, BBoxError

UP = "up"
DOWN = "down"
SELECT = "select"
TOGGLE = "toggle"
ALL = "all"
BACK = "back"
QUIT = "quit"
OTHER = "other"


class PickerError(RuntimeError):
    """A screen cannot be drawn - normally because it has nothing on it."""


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------

# Windows sends a two-byte sequence for the arrows: a \xe0 or \x00 lead-in
# followed by a scan code. POSIX sends a three-byte CSI escape. Neither is a
# single character, which is the thing that makes naive getch loops wrong.
#
# DIVERGENCE FROM cudem-extract's picker, DELIBERATE
# --------------------------------------------------
# There, escape and `q` are the same action: CANCEL. That is right for a
# one-screen picker, where there is nowhere to step back *to* and the only
# question is whether you meant to leave.
#
# This toolkit's `bios study` is a wizard - a study, then datasets, then (in
# a later slice) four padding stages and a confirm screen - and by the last of
# those a mistyped answer three screens ago is expensive: losing a resolved
# download and several answers to one keypress is not acceptable. So escape
# steps back one screen, `q` abandons the run, and the two are different keys.
# Escape on the *first* screen has nowhere to go and exits; that rule lives in
# `walk`, not here, because it is a fact about the sequence rather than about
# the keyboard.
#
# Ctrl-C is listed because these loops own the terminal in raw mode, where the
# console driver does not raise KeyboardInterrupt for us. It abandons, never
# steps back: an interrupt is not a navigation key.
_KEYS = {
    b"\xe0H": UP,    b"\x00H": UP,    b"\x1b[A": UP,
    b"\xe0P": DOWN,  b"\x00P": DOWN,  b"\x1b[B": DOWN,
    b"\r": SELECT,   b"\n": SELECT,
    b" ": TOGGLE,
    b"a": ALL,       b"A": ALL,
    b"\x1b": BACK,                    # a bare escape, no sequence following
    b"q": QUIT,      b"Q": QUIT,
    b"\x03": QUIT,                    # Ctrl-C
}


def decode_key(raw) -> str:
    """Map a raw key sequence to one of the action constants."""
    if not raw:
        # End of input is not a selection, and it is not a step back either:
        # a screen whose key source has run dry would loop forever on BACK.
        return QUIT
    if isinstance(raw, str):
        raw = raw.encode("latin-1", "replace")
    return _KEYS.get(raw, OTHER)


def move(index: int, count: int, action: str) -> int:
    """Where the cursor goes next. Wraps at both ends."""
    if count <= 0:
        return 0
    if action == UP:
        return (index - 1) % count
    if action == DOWN:
        return (index + 1) % count
    return max(0, min(index, count - 1))


# --------------------------------------------------------------------------
# what a screen is made of
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One line of a screen, and whether it may be picked.

    ``text`` is already formatted - the screen that built it decided its
    columns - and carries neither the cursor nor the checkbox, which belong to
    the loop drawing it.

    A row that cannot be picked is still *shown*. Hiding it answers "why can I
    not have this" by making the question impossible to ask, which is the
    failure mode this repo family works hardest to avoid, so ``reason`` says
    what is wrong and is printed the moment somebody tries.
    """

    text: str
    value: Any = None
    selectable: bool = True
    reason: str = ""


@dataclass(frozen=True)
class Choice:
    """What a screen returned: a value, a step back, or an abandoned run."""

    action: str
    value: Any = None

    @classmethod
    def picked(cls, value) -> "Choice":
        return cls(SELECT, value)

    @classmethod
    def back(cls) -> "Choice":
        return cls(BACK)

    @classmethod
    def abandoned(cls) -> "Choice":
        return cls(QUIT)

    @property
    def is_picked(self) -> bool:
        return self.action == SELECT

    @property
    def is_back(self) -> bool:
        return self.action == BACK

    @property
    def is_quit(self) -> bool:
        return self.action == QUIT


def render_rows(
    rows: Sequence[Row], index: int, selected: Iterable[int] | None = None
) -> list[str]:
    """The whole list, with the cursor on ``index``.

    ``selected`` turns the list into a multi-select: every row grows a box,
    and a row that cannot be picked shows a dash rather than an empty box, so
    "not chosen" and "not choosable" do not look alike.
    """
    chosen = set(selected) if selected is not None else None
    out = []
    for i, row in enumerate(rows):
        cursor = ">" if i == index else " "
        if chosen is None:
            out.append(f"{cursor} {row.text}")
            continue
        if not row.selectable:
            box = "[-]"
        else:
            box = "[x]" if i in chosen else "[ ]"
        out.append(f"{cursor} {box} {row.text}")
    return out


# --------------------------------------------------------------------------
# reading keys from a real terminal
# --------------------------------------------------------------------------


def can_pick(stdin=None, stdout=None) -> bool:
    """Is there a terminal to draw a screen on?

    Both streams must be a TTY. stdin alone is not enough: it is genuinely
    possible to have an interactive stdin while stdout is redirected, and
    drawing there sprays escape codes into a file.

    The same rule, for a different question, is ``studyrun.interactive`` - that
    one decides whether the run may *ask* something, this one whether it may
    *draw*. They are kept apart because a screen is the expensive one to get
    wrong.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        return bool(stdin.isatty() and stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _read_key_windows() -> bytes:  # pragma: no cover - needs a real console
    import msvcrt

    ch = msvcrt.getch()
    if ch in (b"\x00", b"\xe0"):          # lead-in: the scan code follows
        return ch + msvcrt.getch()
    return ch


def _read_key_posix() -> bytes:  # pragma: no cover - needs a real console
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch != b"\x1b":
            return ch
        # Escape may start a sequence or stand alone. Wait briefly: with no
        # timeout a lone Esc would block until the next keypress.
        seq = b""
        while len(seq) < 2 and select.select([fd], [], [], 0.05)[0]:
            seq += os.read(fd, 1)
        return ch + seq
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key() -> bytes:  # pragma: no cover - needs a real console
    """One keypress, as raw bytes."""
    return _read_key_windows() if os.name == "nt" else _read_key_posix()


def _enable_ansi() -> bool:  # pragma: no cover - needs a real console
    """Turn on VT processing so the redraw escapes work on Windows."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


# --------------------------------------------------------------------------
# the loops
# --------------------------------------------------------------------------

#: Drawn on the screen rather than assumed. Nobody has read a manual for a key
#: they are about to press.
ONE_KEYS = "up/down move    enter select    esc back    q quit"
MANY_KEYS = (
    "up/down move    space toggle    a all runnable (again to clear)    "
    "enter confirm    esc back    q quit"
)


def _header(title) -> list[str]:
    lines = [title] if isinstance(title, str) else list(title)
    return [f"  {line}" for line in lines] + [""]


#: Lines reserved at the foot of a screen for the message a refusal prints.
#: Fixed, and not "as many as the message needs": redraw walks the cursor back
#: by the number of lines it drew last time, so a screen that shrinks by a line
#: leaves a stale one behind and every subsequent redraw is off by one. A
#: reason long enough to need three lines is normal here - they name a URL and
#: a flag - so the block is always this tall and mostly blank. Three, because a
#: refusal truncated before it reaches "pass --local-archive KEY=<path>" has
#: told the reader they cannot have the layer and not how to get it, which is
#: the half of the message worth printing.
MESSAGE_LINES = 3

#: Where a message wraps. Narrow enough to survive an 80-column console, since
#: a line the terminal wraps for us breaks the same count.
MESSAGE_WIDTH = 76


def message_block(message: str) -> list[str]:
    """The message, wrapped and padded to exactly :data:`MESSAGE_LINES` lines.

    A message too long for the block is truncated rather than allowed to push
    the screen taller; the reasons that need the room are written to fit.
    """
    wrapped: list[str] = []
    for para in (message or "").splitlines() or [""]:
        # Never inside a word, and never at a hyphen: these messages carry
        # URLs, and `https://prd-` / `tnm.s3...` on two lines is not a URL
        # anybody can copy - it reads as two different hosts. A URL longer
        # than the width overflows its line instead, which at worst costs one
        # cosmetic redraw drift on a narrow console.
        wrapped.extend(
            textwrap.wrap(
                para, MESSAGE_WIDTH, break_long_words=False, break_on_hyphens=False
            )
            or [""]
        )
    wrapped = wrapped[:MESSAGE_LINES]
    wrapped += [""] * (MESSAGE_LINES - len(wrapped))
    return [f"  {line}" for line in wrapped]


def _screen(
    rows: Sequence[Row],
    title,
    multi: bool,
    read: Callable[[], bytes] | None,
    write: Callable[[str], Any] | None,
    redraw: bool | None,
) -> Choice:
    """One list on the screen, until it returns a :class:`Choice`.

    ``read`` supplies raw key bytes and defaults to the real terminal; a test
    passes a scripted sequence instead. ``redraw`` defaults to whether ANSI
    cursor movement is available - without it the list is simply reprinted,
    which scrolls but stays correct.
    """
    rows = list(rows)
    if not rows:
        raise PickerError("nothing to choose from")

    read = read or read_key
    write = write or sys.stdout.write
    if redraw is None:  # pragma: no cover - a real console decides this
        redraw = _enable_ansi()

    index = 0
    selected: set[int] = set()
    message = ""
    drawn = 0
    runnable = [i for i, row in enumerate(rows) if row.selectable]

    while True:
        if drawn and redraw:
            write(f"\x1b[{drawn}A")               # back to the top of the list
        lines = (
            _header(title)
            + render_rows(rows, index, selected if multi else None)
            + ["", f"  {MANY_KEYS if multi else ONE_KEYS}"]
            + message_block(message)
        )
        for line in lines:
            write(("\x1b[2K" if redraw else "") + line + "\n")
        drawn = len(lines)
        message = ""

        action = decode_key(read())
        if action == QUIT:
            return Choice.abandoned()
        if action == BACK:
            return Choice.back()

        if action == TOGGLE and multi:
            row = rows[index]
            if not row.selectable:
                message = row.reason or "this row cannot be selected"
                continue
            selected.symmetric_difference_update({index})
            continue

        if action == ALL and multi:
            # Every runnable row, or none of them if they are already all on.
            # The second press is what makes a mis-hit cheap to undo.
            selected = set() if selected >= set(runnable) else set(runnable)
            continue

        if action == SELECT:
            if multi:
                if not selected:
                    message = (
                        "nothing selected - space toggles the row under the "
                        "cursor, a selects every runnable one"
                    )
                    continue
                return Choice.picked([rows[i].value for i in sorted(selected)])
            row = rows[index]
            if row.selectable:
                return Choice.picked(row.value)
            message = row.reason or "this row cannot be selected"
            continue

        index = move(index, len(rows), action)


def choose_one(rows, title, read=None, write=None, redraw=None) -> Choice:
    """Pick exactly one row. Nothing is pre-selected beyond the cursor."""
    return _screen(rows, title, False, read, write, redraw)


def choose_many(rows, title, read=None, write=None, redraw=None) -> Choice:
    """Pick any number of rows. Nothing is pre-selected."""
    return _screen(rows, title, True, read, write, redraw)


# --------------------------------------------------------------------------
# sequencing
# --------------------------------------------------------------------------

#: A step: given the answers so far, draw a screen and return a Choice.
Step = Callable[[list], Choice]


def walk(steps: Sequence[Step]) -> list | None:
    """Run screens in order, honouring back, and collect their answers.

    Returns one answer per step, or ``None`` when the run was abandoned -
    either by the quit key or by stepping back off the front of the sequence,
    which is where "escape on the first screen exits" actually lives.

    A step is re-invoked when it is stepped back into, so it can redraw itself
    with what is now known. Answers already given are handed to it, which is
    how a later screen defaults to an earlier one's answer.
    """
    answers: list = [None] * len(steps)
    i = 0
    while i < len(steps):
        choice = steps[i](answers)
        if choice.is_quit:
            return None
        if choice.is_back:
            if i == 0:
                return None
            i -= 1
            continue
        answers[i] = choice.value
        i += 1
    return answers


# --------------------------------------------------------------------------
# screen one: which study
# --------------------------------------------------------------------------

#: The padding a later screen will offer, in kilometres. Defined here because
#: the study list has to show a box size *before* anybody has chosen one, and
#: the number it shows must be one of the numbers actually on offer.
PAD_PRESETS_KM = (5.0, 10.0, 20.0)

#: What the study list measures its boxes with. The smallest preset, so no row
#: ever advertises a box larger than the one the smallest answer would give.
#: The raw station envelope is not usable for this: for the reference study it
#: is a sub-kilometre sliver, and every row would read "0.9x0.2 km" - which is
#: true, and tells a reader nothing about the extraction they are about to run.
PREVIEW_PAD_KM = min(PAD_PRESETS_KM)


def study_marker(study) -> str:
    """How this study's existing marine-bios output is advertised, if any."""
    if not study.has_products and study.our_status is None:
        return ""
    if study.our_status == studies.STATUS_OK:
        return f"[{studies.PRODUCER}]"
    if study.our_status:
        # An incomplete or failed run is exactly the row somebody needs to find
        # again, so the status is spelled out rather than reduced to a tick.
        return f"[{studies.PRODUCER}: {study.our_status}]"
    return f"[{studies.PRODUCER}: unrecorded]"


def study_size(study, pad_km: float = PREVIEW_PAD_KM) -> str | None:
    """``'23.4x21.7 km'`` for one study, or ``None`` when there is no box."""
    envelope = study.envelope()
    if envelope is None:
        return None
    try:
        box = BBox.from_envelope(
            envelope, north_km=pad_km, south_km=pad_km, east_km=pad_km, west_km=pad_km
        )
    except BBoxError:
        return None
    return f"{box.width_km:.1f}x{box.height_km:.1f} km"


def study_row(study, label_width: int = 16, pad_km: float = PREVIEW_PAD_KM) -> Row:
    """One study, as a row: label, created, stations, box size, marker.

    A study that cannot produce a box says so *here*, in place of the size,
    rather than looking ordinary until you pick it and are refused.
    """
    label = study.label
    if len(label) > label_width:
        label = label[: label_width - 2] + ".."

    parts = [
        f"{label:<{label_width}}",
        f"{study.created_short:<16}",
        f"{study.station_summary():>7}",
    ]

    reason = study.unusable_reason
    size = None if reason else study_size(study, pad_km)
    if size is None and not reason:
        reason = "the stations give no box to extract for"
    if reason:
        parts.append(f" -- {reason}")
        return Row(text="  ".join(parts), value=study, selectable=False, reason=reason)

    parts.append(f"{size:>13}")
    mark = study_marker(study)
    if mark:
        parts.append(mark)
    return Row(text="  ".join(parts), value=study)


def study_rows(found, pad_km: float = PREVIEW_PAD_KM) -> list[Row]:
    """Every study, in the order given - which ``list_studies`` makes newest first."""
    width = min(24, max([len(s.label) for s in found] + [12]))
    return [study_row(s, width, pad_km) for s in found]


def study_title(root, pad_km: float = PREVIEW_PAD_KM) -> list[str]:
    """The header. It states which padding the box sizes assume."""
    return [
        f"Select a study      {root}",
        f"box sizes assume {pad_km:g} km padding on every side; the padding you "
        "give decides the real box",
    ]


def pick_study(found, root="", pad_km: float = PREVIEW_PAD_KM, read=None, write=None,
               redraw=None) -> Choice:
    """Choose one study. The value of a picked :class:`Choice` is the study.

    A study whose metadata will not parse is listed with its error rather than
    omitted, and a study with no positioned stations is visible but refuses
    selection: handing the caller something it must immediately reject is worse
    than saying why, on the screen where the question occurs.
    """
    found = list(found)
    if not found:
        raise studies.StudyError(
            f"no studies under {root}\nStudies are created by station-data-extract."
        )
    return choose_one(
        study_rows(found, pad_km), study_title(root, pad_km),
        read=read, write=write, redraw=redraw,
    )


# --------------------------------------------------------------------------
# screen two: which datasets
# --------------------------------------------------------------------------

DATASET_TITLE = [
    "Select the layers to extract",
    "every layer this toolkit knows about is listed; the ones it cannot fetch "
    "for you say why",
]


def unblock(dataset) -> str:
    """What a person would have to do to make this dataset runnable.

    "Why can I not have this" is asked on the screen where the layer is
    refused, so it is answered there rather than in a README nobody has open.
    """
    if dataset.status == "manual":
        where = f" Form: {dataset.landing_url}" if dataset.landing_url else ""
        # The flag comes before the URL: a refusal truncated by a narrow
        # console should lose the address before it loses the instruction.
        return (
            "gated behind a registration form. Download it once, then pass "
            f"--local-archive {dataset.key}=<path>.{where}"
        )
    if dataset.status == "unverified":
        reason = dataset.status_reason.strip() or (
            "Its download has not been confirmed against the publisher, so "
            "asking for it raises rather than guessing a URL."
        )
        where = f" See {dataset.landing_url}." if dataset.landing_url else ""
        return f"{reason}{where}"
    return ""  # pragma: no cover - a ready dataset needs no unblocking


def dataset_row(dataset, key_width: int = 18, local: bool = False) -> Row:
    """One registry entry, as a row.

    ``local`` says an archive for it was supplied with ``--local-archive``,
    which makes a gated layer runnable *for this run* - refusing a dataset
    somebody has already handed us the file for would be absurd.
    """
    parts = [
        f"{dataset.key:<{key_width}}",
        f"{dataset.kind:<6}",
        f"{(dataset.dataset_id or '-'):<8}",
        dataset.title,
    ]
    if local:
        return Row(text="  ".join(parts) + "  [local archive]", value=dataset.key)
    if dataset.status == "ready":
        return Row(text="  ".join(parts), value=dataset.key)
    return Row(
        text="  ".join(parts) + f"  [{dataset.status}]",
        value=dataset.key,
        selectable=False,
        reason=f"{dataset.key}: {unblock(dataset)}",
    )


def dataset_rows(registry, local_archives=()) -> list[Row]:
    """Every registry entry, ready ones first and each group in key order.

    Ordering by runnability rather than alphabetically puts the rows somebody
    can act on under the cursor when the screen opens, and keeps the ones they
    cannot together at the bottom where they read as a list of "not yet"
    rather than as scattered failures.
    """
    local = set(local_archives)
    entries = list(registry.values())
    entries.sort(key=lambda d: (d.status != "ready" and d.key not in local, d.key))
    width = min(22, max([len(d.key) for d in entries] + [12]))
    return [dataset_row(d, width, local=d.key in local) for d in entries]


def pick_datasets(registry, local_archives=(), read=None, write=None, redraw=None) -> Choice:
    """Choose any number of datasets. The value is a list of keys.

    Nothing is pre-selected: a run that extracts half a gigabyte because
    somebody pressed enter past a screen is exactly the surprise the sizes
    below exist to prevent.
    """
    return choose_many(
        dataset_rows(registry, local_archives), DATASET_TITLE,
        read=read, write=write, redraw=redraw,
    )


# --------------------------------------------------------------------------
# what the selection costs, before it costs it
# --------------------------------------------------------------------------


def report_sizes(
    keys,
    registry,
    resolve,
    cache_dir=None,
    local_archives=(),
    write=None,
    timeout: int = 60,
    resolved: dict | None = None,
) -> dict:
    """Resolve the selected archives and print what fetching them would cost.

    Resolution is a directory listing and a HEAD per dataset - no payload - so
    this is the last point at which "151 MB" is a number somebody can still act
    on. The results are returned so the run can reuse them: the same two
    requests made twice would make ``bios network``'s per-dataset count a lie.

    Already-cached archives are named as such rather than counted, because the
    question being answered is what this run will *download*.

    A dataset that will not resolve is reported and skipped. Refusing here
    would duplicate the plan stage's refusal, which is better placed and knows
    about --keep-going.
    """
    write = write or (lambda line: print(line))
    resolved = {} if resolved is None else resolved
    local = set(local_archives)

    write("")
    write("Archive sizes, before anything is downloaded:")
    to_fetch = 0
    for key in keys:
        dataset = registry[key]
        if key in local:
            write(f"  {key:<22} supplied with --local-archive")
            continue
        cached = fetch_mod.cached_archive(cache_dir, key) if cache_dir else None
        if key not in resolved:
            try:
                resolved[key] = resolve(dataset, timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - reported, never fatal here
                resolved[key] = None
                write(f"  {key:<22} unavailable - {str(exc).splitlines()[0]}")
                continue
        src = resolved[key]
        if src is None:
            write(f"  {key:<22} unavailable")
            continue
        size = f"{src.bytes / 1e6:.1f} MB" if src.bytes else "size unknown"
        if cached:
            write(f"  {key:<22} {size:>12}  already cached")
        else:
            to_fetch += src.bytes or 0
            write(f"  {key:<22} {size:>12}  {src.last_modified or 'date unknown'}")
    write(f"  {'':<22} {f'{to_fetch / 1e6:.1f} MB':>12}  to download")
    return resolved
