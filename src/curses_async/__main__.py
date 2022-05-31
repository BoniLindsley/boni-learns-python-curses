# /usr/bin/env python3

# Standard libraries.
import collections
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


class Typeahead:
    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        self._cache = collections.deque[int | str]()

    def getch(self) -> curses_async.Coroutine[int]:
        try:
            return self.popleft()
        except IndexError:
            pass
        return (yield from curses_async.get_running_loop().getch())

    def popleft(self) -> int:
        """
        :return: Next cached input character.
        :raise IndexError: If cache is empty.
        """
        next_entry = self._cache.popleft()
        if isinstance(next_entry, int):
            return next_entry
        first, remaining = next_entry[0], next_entry[1:]
        if remaining:
            self.appendleft(remaining)
        return ord(first)

    def appendleft(self, next_entry: int | str) -> None:
        self._cache.appendleft(next_entry)


def handle_in_command_line_mode(
    *, message_area: MessageArea, typeahead: Typeahead
) -> curses_async.Coroutine[None]:
    stdscr = curses_async.get_running_loop().open()
    textbox = message_area.textbox
    window = message_area.window
    window.clear()
    while True:
        curses.doupdate()
        next_key = yield from typeahead.getch()
        if next_key == 7:
            break
        if next_key == 10:
            break
        textbox.do_command(next_key)
        window.noutrefresh()
    command = textbox.gather()
    command_map.get(command[1:-1], noop)()


def async_main() -> curses_async.Coroutine[None]:
    loop = curses_async.get_running_loop()
    stdscr = loop.open()
    stdscr.clear()
    message_area = MessageArea(parent=stdscr)
    typeahead = Typeahead()
    for counter in range(3):
        stdscr.addstr(0, 0, str(counter))
        stdscr.noutrefresh()
        curses.doupdate()
        next_char = yield from typeahead.getch()
        if next_char == ord(":"):
            typeahead.appendleft(next_char)
            yield from handle_in_command_line_mode(
                message_area=message_area,
                typeahead=typeahead,
            )


def main() -> int:
    curses_async.run(async_main())
    return 0


if __name__ == "__main__":
    sys.exit(main())
