# /usr/bin/env python3

# Standard libraries.
import curses
import curses.textpad
import logging
import sys
import typing

# Internal dependencies.
import curses_async

# In Windows native, need windows-curses

# In MSYS2, might need
# export TERMINFO=$MSYSTEM_PREFIX/share/terminfo

_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class MessageArea:
    def __init__(
        self, *args: typing.Any, parent: curses.window, **kwargs: typing.Any
    ) -> None:
        super().__init__(*args, **kwargs)
        height, width = parent.getmaxyx()
        self.window = parent.derwin(1, width, height - 1, 0)
        self.textbox = curses.textpad.Textbox(self.window)


def print_state() -> curses_async.Coroutine[None]:
    loop = curses_async.get_running_loop()
    stdscr = loop.open()
    stdscr.clear()
    message_area = MessageArea(parent=stdscr)
    for counter in range(3):
        if counter:
            stdscr.addstr(0, 0, str(counter))
            stdscr.noutrefresh()
        curses.doupdate()
        next_key = yield from loop.getch()
        if next_key == ord(":"):
            message_area.window.clear()
            message_area.textbox.do_command(":")
            message_area.textbox.edit()


def main() -> int:
    curses_async.run(print_state())
    return 0


if __name__ == "__main__":
    sys.exit(main())
