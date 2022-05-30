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

NullaryCallable = typing.Callable[[], typing.Any]


def noop() -> None:
    pass


def stop_running_loop() -> None:
    curses_async.get_running_loop().stop()


class MessageArea:
    def __init__(
        self, *args: typing.Any, parent: curses.window, **kwargs: typing.Any
    ) -> None:
        super().__init__(*args, **kwargs)
        height, width = parent.getmaxyx()
        self.window = parent.derwin(1, width, height - 1, 0)
        self.textbox = curses.textpad.Textbox(self.window)


command_map: dict[str, NullaryCallable] = {
    "q": stop_running_loop,
    "quit": stop_running_loop,
}


def handle_in_command_line_mode(
    *, message_area: MessageArea, next_key: int
) -> None:
    def echo(next_char: str) -> str:
        stdscr = curses_async.get_running_loop().open()
        stdscr.addstr(0, 0, message_area.textbox.gather() + "==")
        stdscr.noutrefresh()
        return next_char

    message_area.window.clear()
    message_area.textbox.do_command(next_key)
    command = message_area.textbox.edit(echo)[1:-1]
    command_map.get(command, noop)()


def async_main() -> curses_async.Coroutine[None]:
    loop = curses_async.get_running_loop()
    stdscr = loop.open()
    stdscr.clear()
    message_area = MessageArea(parent=stdscr)
    for counter in range(3):
        stdscr.addstr(0, 0, str(counter))
        stdscr.noutrefresh()
        curses.doupdate()
        next_char = yield from loop.getch()
        if next_char == ord(":"):
            handle_in_command_line_mode(
                message_area=message_area, next_key=next_char
            )


def main() -> int:
    curses_async.run(async_main())
    return 0


if __name__ == "__main__":
    sys.exit(main())
