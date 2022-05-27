# /usr/bin/env python3

# Standard libraries.
import curses
import logging
import sys

# Internal dependencies.
import curses_async

# In Windows native, need windows-curses

# In MSYS2, might need
# export TERMINFO=$MSYSTEM_PREFIX/share/terminfo

_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


def print_state() -> curses_async.Coroutine[None]:
    loop = curses_async.get_running_loop()
    stdscr = loop.open()
    stdscr.clear()
    for counter in range(10):
        if counter:
            stdscr.addstr(0, 0, str(counter))
            stdscr.noutrefresh()
        curses.doupdate()
        yield from loop.getch()


def main() -> int:
    curses_async.run(print_state())
    return 0


if __name__ == "__main__":
    sys.exit(main())
