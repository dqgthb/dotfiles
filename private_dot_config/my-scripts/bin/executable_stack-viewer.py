#!/usr/bin/env python3
import curses
import os
import re
import subprocess
import sys

from pygments import lex
from pygments.lexers.c_cpp import CppLexer
from pygments.token import Token

# ---------------------------------------------------------------------------
# Constants and Regular Expressions
# ---------------------------------------------------------------------------

SEPARATOR = "Stack trace (most recent call first):"
DEFAULT_EDITOR = os.environ.get("EDITOR", "nvim")  # Default editor if not set.

# Regex to remove ANSI escape sequences (used in file parsing only)
ANSI_ESCAPE_REGEX = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

# ---------------------------------------------------------------------------
# Trace Processing Functions
# ---------------------------------------------------------------------------


def load_traces(filename: str) -> list[str]:
    with open(filename, "r") as f:
        content = f.read()
    parts = [part.strip() for part in content.split(SEPARATOR) if part.strip()]
    traces = [f"{SEPARATOR}\n{part}" for part in parts]
    return traces


def simplify_trace(lines: list[str]) -> list[str]:
    simplified = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("#"):
            header_line = line
            j = i + 1
            snippet_found = False
            while j < len(lines) and not lines[j].lstrip().startswith("#"):
                snippet_found = True
                j += 1
            if snippet_found:
                simplified.append(header_line + " ....")
            else:
                simplified.append(header_line)
            i = j
        else:
            i += 1
    return simplified


# ---------------------------------------------------------------------------
# Curses Rendering Helper Functions
# ---------------------------------------------------------------------------


def get_curses_attr_for_token(ttype, token_colors):
    """
    Walk up the token type hierarchy until we find a matching entry in our token_colors dict.
    """
    while ttype not in token_colors and ttype.parent is not None:
        ttype = ttype.parent
    return token_colors.get(ttype, curses.A_NORMAL)


def render_line(window, y, x, line, lexer, token_colors, base_attr=0):
    """
    Render a line of text at position (y, x) by tokenizing it using Pygments and applying
    the corresponding curses color attribute for each token. The base_attr (e.g. curses.A_REVERSE)
    can be used to add extra styling such as for a selected line.
    """
    pos = x
    for ttype, token in lex(line, lexer):
        attr = get_curses_attr_for_token(ttype, token_colors)
        combined_attr = attr | base_attr
        try:
            window.addstr(y, pos, token, combined_attr)
        except curses.error:
            # Likely ran off the screen.
            break
        pos += len(token)


def parse_file_and_line(text: str) -> tuple[str, int] | None:
    """
    Parse a file path and a line number from a stack frame header.
    It expects a pattern like 'at <filename>:<line>' in the text.
    Returns (filename, line_number) if successful, or None otherwise.
    """
    clean_text = ANSI_ESCAPE_REGEX.sub("", text)
    match = re.search(r"\bat\s+(?P<file>\S+?)(?=:\d+\b):(?P<line>\d+)\b", clean_text)
    if match:
        return match.group("file"), int(match.group("line"))
    return None


def open_file_in_editor(stdscr: curses.window, filename: str, line_number: int) -> None:
    """
    Exit curses mode, open the specified file in the configured editor at the given line,
    and then reinitialize curses.
    """
    curses.endwin()
    try:
        subprocess.call([DEFAULT_EDITOR, f"+{line_number}", filename])
    except Exception as e:
        print("Failed to open editor:", e)
    stdscr.clear()
    curses.doupdate()


# ---------------------------------------------------------------------------
# Main Curses Loop
# ---------------------------------------------------------------------------


def main(stdscr: curses.window, traces: list[str]) -> None:
    curses.curs_set(0)
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
        except Exception:
            pass

        # Initialize color pairs for syntax highlighting.
        curses.init_pair(1, curses.COLOR_GREEN, -1)  # Comments
        curses.init_pair(2, curses.COLOR_BLUE, -1)  # Keywords
        curses.init_pair(3, curses.COLOR_WHITE, -1)  # Names (identifiers)
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)  # Strings
        curses.init_pair(5, curses.COLOR_CYAN, -1)  # Numbers
        curses.init_pair(6, curses.COLOR_RED, -1)  # Operators
        curses.init_pair(7, curses.COLOR_YELLOW, -1)  # Punctuation
        curses.init_pair(8, curses.COLOR_WHITE, -1)  # Generic

        token_colors = {
            Token.Comment: curses.color_pair(1),
            Token.Keyword: curses.color_pair(2),
            Token.Name: curses.color_pair(3),
            Token.String: curses.color_pair(4),
            Token.Number: curses.color_pair(5),
            Token.Operator: curses.color_pair(6),
            Token.Punctuation: curses.color_pair(7),
            Token.Generic: curses.color_pair(8),
        }
    else:
        token_colors = {}

    lexer = CppLexer()
    current_trace_idx = 0
    selected_frame = 0
    frame_scroll_offset = 0
    show_full_trace = False  # Toggle state for full/simplified trace

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()

        trace_lines = traces[current_trace_idx].splitlines()
        frames = trace_lines if show_full_trace else simplify_trace(trace_lines)
        total_frames = len(frames)

        if total_frames == 0:
            selected_frame = 0
        elif selected_frame >= total_frames:
            selected_frame = total_frames - 1

        available_lines = height - 1
        if selected_frame < frame_scroll_offset:
            frame_scroll_offset = selected_frame
        elif selected_frame >= frame_scroll_offset + available_lines:
            frame_scroll_offset = selected_frame - available_lines + 1

        header = (
            f"Stack trace {current_trace_idx + 1}/{len(traces)} "
            f"(Frame {selected_frame + 1}/{total_frames}) "
            f"(←/h: prev trace, →/l: next trace, ↑/↓: select frame, Enter: open file, t: toggle full, q/ESC: quit)"
        )
        try:
            stdscr.addstr(0, 0, header[:width], curses.A_BOLD)
        except curses.error:
            pass

        # Render each frame line with syntax highlighting.
        for idx in range(
            frame_scroll_offset, min(len(frames), frame_scroll_offset + available_lines)
        ):
            y = 1 + idx - frame_scroll_offset
            base_attr = curses.A_REVERSE if idx == selected_frame else 0
            render_line(stdscr, y, 0, frames[idx], lexer, token_colors, base_attr)

        stdscr.refresh()
        key = stdscr.getch()

        if key in (ord("q"), 27):
            break
        elif key in (curses.KEY_LEFT, ord("h")):
            if current_trace_idx > 0:
                current_trace_idx -= 1
                selected_frame = 0
                frame_scroll_offset = 0
        elif key in (curses.KEY_RIGHT, ord("l")):
            if current_trace_idx < len(traces) - 1:
                current_trace_idx += 1
                selected_frame = 0
                frame_scroll_offset = 0
        elif key == curses.KEY_UP:
            if selected_frame > 0:
                selected_frame -= 1
        elif key == curses.KEY_DOWN:
            if selected_frame < total_frames - 1:
                selected_frame += 1
        elif key in (curses.KEY_ENTER, 10, 13):
            # Attempt to open the file in the editor based on the selected frame.
            if total_frames > 0:
                selected_line = frames[selected_frame]
                file_line_info = parse_file_and_line(selected_line)
                if file_line_info:
                    filename, line_number = file_line_info
                    open_file_in_editor(stdscr, filename, line_number)
                else:
                    try:
                        stdscr.addstr(
                            height - 1,
                            0,
                            "No file information found for the selected frame. Press any key.",
                            curses.A_BOLD,
                        )
                    except curses.error:
                        pass
                    stdscr.getch()
        elif key == ord("t"):
            show_full_trace = not show_full_trace
            frame_scroll_offset = 0
            selected_frame = 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python stack_viewer.py <log_file>")
        sys.exit(1)

    log_filename = sys.argv[1]
    traces = load_traces(log_filename)
    if not traces:
        print("No stack traces found using the separator.")
        sys.exit(1)

    curses.wrapper(main, traces)
