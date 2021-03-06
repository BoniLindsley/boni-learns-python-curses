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

key_map: dict[str, str] = {
    "ZZ": ":q\n",
}


class Typeahead:
    def __init__(
        self,
        *args: typing.Any,
        getch: typing.Callable[[], curses_async.Coroutine[int]],
        **kwargs: typing.Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._cache = collections.deque[int | str]()
        self._cache_repeat_count = 0
        self._getch = getch

    def getch(self) -> curses_async.Coroutine[int]:
        """
        :return: Cached input character or retrieve from ``curses``.
        :raise RecursionError: See ``popleft``.
        """
        try:
            return self.popleft()
        except IndexError:
            pass
        self._cache_repeat_count = 0
        curses.doupdate()
        return (yield from self._getch())

    def popleft(self) -> int:
        """
        :return: Next cached input character.
        :raise IndexError: If cache is empty.
        :raise RecursionError:
            If popleft is used a certain number of times
            without getch redirecting to user input.
            This is to prevent map recursion causing infinite loops.
            The limit is currently set as ``sys.getrecursionlimit()``.
        """
        self._cache_repeat_count += 1
        if self._cache_repeat_count > sys.getrecursionlimit():
            raise RecursionError(
                "Too many uses of Typeahead cache without user input.",
            )
        next_entry = self._cache.popleft()
        if isinstance(next_entry, int):
            return next_entry
        first, remaining = next_entry[0], next_entry[1:]
        if remaining:
            self.appendleft(remaining)
        return ord(first)

    def appendleft(self, next_entry: int | str) -> None:
        self._cache.appendleft(next_entry)


def get_command_in_command_line_mode(
    *, message_area: MessageArea, typeahead: Typeahead
) -> curses_async.Coroutine[str]:
    textbox = message_area.textbox
    window = message_area.window
    window.clear()
    while True:
        next_key = yield from typeahead.getch()
        if next_key == 7:
            window.clear()
            break
        if next_key == 10:
            break
        textbox.do_command(next_key)
        window.noutrefresh()
    window.noutrefresh()
    return textbox.gather()


def process_command_in_normal_mode(
    *, typeahead: Typeahead
) -> curses_async.Coroutine[None]:
    sequences = list(key_map.keys())
    buffer = ""
    while sequences:
        next_ch = yield from typeahead.getch()
        buffer += chr(next_ch)
        new_typeahead_entry = key_map.get(buffer)
        if new_typeahead_entry is not None:
            typeahead.appendleft(new_typeahead_entry)
            break
        sequences = [seq for seq in sequences if seq.startswith(buffer)]
    else:
        curses.beep()


def refresh(*, window: curses.window) -> collections.abc.Generator[None, None, None]:
    window.clear()
    for counter in range(3):
        window.addstr(0, 0, str(counter))
        window.noutrefresh()
        yield


def async_main() -> curses_async.Coroutine[int]:
    loop = curses_async.get_running_loop()
    stdscr = loop.open()
    message_area = MessageArea(parent=stdscr)
    typeahead = Typeahead(getch=loop.getch)
    for _ in refresh(window=stdscr):
        next_char = yield from typeahead.getch()
        typeahead.appendleft(next_char)
        if next_char == ord(":"):
            command = yield from get_command_in_command_line_mode(
                message_area=message_area,
                typeahead=typeahead,
            )
            command_map.get(command[1:-1], noop)()
        else:
            yield from process_command_in_normal_mode(
                typeahead=typeahead,
            )
    return 0


def main() -> int:
    return_value = curses_async.run(async_main())
    return return_value if return_value is not None else 1


if __name__ == "__main__":
    sys.exit(main())
