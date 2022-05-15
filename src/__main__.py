# /usr/bin/env python3

from __future__ import annotations

# Standard libraries.
import collections.abc
import curses
import functools
import logging
import os
import signal
import sys
import threading
import typing

# In Windows native, need windows-curses

# In MSYS2, might need
# export TERMINFO=$MSYSTEM_PREFIX/share/terminfo

_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

NullaryCallable = typing.Callable[[], typing.Any]
DoneCallback = typing.Callable[["Future"], typing.Any]

_T = typing.TypeVar("_T")
_Copyable = typing.TypeVar("_Copyable", bound="Copyable")

Coroutine = collections.abc.Generator[
    "Future[typing.Any]",  # Yield type
    typing.Any,  # Send type
    _T,  # Return type
]


class Copyable(typing.Protocol):
    def copy(self: _T) -> _T:
        ...

    def clear(self) -> None:
        ...


class CancelledError(RuntimeError):
    pass


class InvalidStateError(RuntimeError):
    pass


class _FutureResultSentinel:
    pass


class _FutureCancelled:
    pass


class _FutureResult(typing.Generic[_T]):
    result: _T


_FutureDone = _FutureCancelled | Exception | _FutureResult[_T]


def cut(source: _Copyable) -> _Copyable:
    try:
        return source.copy()
    finally:
        source.clear()


class Future(typing.Generic[_T]):

    _return_sentinel = _FutureResultSentinel()

    def __init__(
        self,
        *args: typing.Any,
        loop: "EventLoop" | None = None,
        **kwargs: typing.Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._done_callbacks: list[DoneCallback] = []
        self._done_state: _FutureDone[_T] | None = None
        if loop is None:
            loop = get_running_loop()
        self._loop: "EventLoop" = loop

    def __next__(self: _T) -> _T:
        return self

    def result(self) -> _T:
        done_state = self._done_state
        if isinstance(done_state, _FutureResult):
            return done_state.result
        if isinstance(done_state, Exception):
            raise done_state
        if isinstance(done_state, _FutureCancelled):
            raise CancelledError()
        raise InvalidStateError()

    def set_result(self, result: _T) -> None:
        if self.done():
            raise InvalidStateError()
        _done_state = self._done_state = _FutureResult()
        _done_state.result = result
        self._call_done_callbacks()

    def set_exception(self, exception: Exception) -> None:
        if self.done():
            raise InvalidStateError()
        _done_state = exception
        self._call_done_callbacks()

    def done(self) -> bool:
        return self._done_state is not None

    def cancelled(self) -> bool:
        return isinstance(self._done_state, _FutureCancelled)

    def add_done_callback(self, callback: DoneCallback) -> None:
        if self.done():
            self.get_loop().call_soon(callback)
            return
        self._done_callbacks.append(callback)

    def remove_done_callback(self, callback: DoneCallback) -> int:
        callbacks = self._done_callbacks
        original_len = len(callbacks)
        callbacks = self._done_callbacks = [
            entry for entry in callbacks if entry is not callback
        ]
        return len(callbacks) - original_len

    def cancel(self) -> bool:
        if self.done():
            return False
        self._done_state = _FutureCancelled()
        self._call_done_callbacks()
        return True

    def _call_done_callbacks(self) -> None:
        loop = self.get_loop()
        for callback in self._done_callbacks.copy():
            loop.call_soon(callback, self)

    def exception(self) -> Exception | None:
        done_state = self._done_state
        if isinstance(done_state, _FutureResult):
            return None
        if isinstance(done_state, Exception):
            return done_state
        if isinstance(done_state, _FutureCancelled):
            raise CancelledError()
        raise InvalidStateError()

    def get_loop(self) -> "EventLoop":
        return self._loop


class Task(Future[_T]):
    def __init__(
        self,
        coro: Coroutine[_T],
        *args: typing.Any,
        **kwargs: typing.Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._coro = coro
        self._awaited_on: Future[typing.Any] | None = None

    def cancel(self) -> bool:
        if self.done():
            return False
        self._done_state = _FutureCancelled()
        self._call_done_callbacks()
        return True

    def send(
        self,
        future: Future[typing.Any] | None,
    ) -> Future[typing.Any]:
        result = None
        if future is not None:
            assert future.done(), "Cannot send result that is not done."
            try:
                result = future.result()
            except Exception as error:  # pylint: disable=broad-except
                # Forwarding all exceptions.
                self.throw(error)
        return self._step_coro(
            functools.partial(
                self.get_coro().send,
                result,
            )
        )

    def throw(self, exception: Exception) -> Future[typing.Any]:
        return self._step_coro(
            functools.partial(
                self.get_coro().throw,
                exception,
            )
        )

    def close(self) -> None:
        self.throw(CancelledError())

    def _step_coro(
        self,
        stepper: typing.Callable[[], Future[typing.Any]],
    ) -> Future[typing.Any]:
        if self.done():
            raise StopIteration()

        try:
            return stepper()
        except StopIteration as error:
            self.set_result(error.value)
            raise
        except CancelledError as error:
            super().cancel()
            raise StopIteration() from error
        except Exception as error:
            self.set_exception(error)
            raise StopIteration() from error

    def get_coro(self) -> Coroutine[_T]:
        return self._coro


def yield_forever() -> collections.abc.Generator[None, typing.Any, None]:
    while True:
        yield


class EventLoop:

    unpause_signal = signal.SIGINT if os.name != "nt" else signal.CTRL_C_EVENT
    __instance = None

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        assert self.__instance is None
        EventLoop.__instance = self
        self._getch_future = None
        self._is_stopping = False
        self._is_waiting = False
        self._pid = os.getpid()
        self._ready_tasks = []
        self._await_pairs = []
        self._stdscr = None

    def open(self) -> curses.window:
        stdscr = self._stdscr
        if stdscr is not None:
            return stdscr
        stdscr = self._stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(1)
        curses.start_color()
        return stdscr

    def run_forever(self) -> None:
        self.run_until_complete(self.create_future())

    def run_until_complete(self, future):
        try:
            while True:
                self._ready_completed_await()
                if not self._ready_tasks:
                    self._set_getch_result()
                else:
                    self._step_ready_tasks()
                if future.done():
                    return future.result()
                if self._is_stopping:
                    break
        finally:
            self._is_stopping = False

    def _ready_completed_await(self) -> None:
        await_pairs = self._await_pairs
        ready_tasks = self._ready_tasks
        for task, awaited_on in cut(self._await_pairs):
            target = ready_tasks if awaited_on.done() else await_pairs
            target.append((task, awaited_on))

    def _step_ready_tasks(self) -> None:
        await_pairs = self._await_pairs
        for task, awaited_on in cut(self._ready_tasks):
            assert awaited_on.done()
            try:
                new_awaited_on.yield_to_coro(task.get_coro())
                waited_on = next(callback.get_coro())
            except StopIteration as error:
                task.set_result(error.value)
            else:
                await_pairs.append((task, new_waited_on))

    def stop(self) -> None:
        self._is_stopping = True

    def is_closed(self) -> None:
        return self._stdscr is None

    def close(self) -> None:
        stdscr = self._stdscr
        if stdscr is None:
            return
        stdscr.keypad(0)
        curses.nocbreak()
        curses.echo()
        curses.endwin()
        self._stdscr = None

    def call_soon(self, callback: typing.Callable[..., typing.Any], *args: typing.Any):
        def wrapped_callback():
            callback(*args)
            yield

        self._ready_tasks.append(self.create_task(wrapped_callback))

    def create_future(self) -> Future:
        return Future(loop=self)

    def create_task(self, coro) -> Task:
        return Task(coro, loop=self)

    def call_soon_threadsafe(
        self, callback: typing.Callable[..., typing.Any], *args: typing.Any
    ):
        self.call_soon(callback)
        if self._is_waiting:
            os.kill(self._pid, self.unpause_signal)

    def ch(self):
        future = self._getch_future
        if future is None:
            future = self._getch_future = self.create_future()
        return future

    def _set_getch_result(self):
        ch = None
        try:
            self._is_waiting = True
            ch = self._stdscr.getch()
        except KeyboardInterrupt:
            return
        finally:
            self._is_waiting = False

        future = self._getch_future
        if future is None:
            return

        future.set_result(ch)


def get_running_loop() -> EventLoop:
    return EventLoop.__instance


def print_keys():
    loop = get_running_loop()
    stdscr = loop.open()
    stdscr.clear()
    for i in range(5):
        next_char = yield loop.getch()
        stdscr.addstr(i, 0, str(next_char))
        stdscr.noutrefresh()


def main() -> int:
    loop = EventLoop()
    try:
        task = loop.create_task(print_keys())
        stdscr = loop.open()
        t = threading.Timer(2, loop.unpause_getch)
        t.start()
        stdscr.clear()
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        for i in range(5):
            next_char = stdscr.getch()
            stdscr.addstr(i, 0, str(next_char))
            stdscr.noutrefresh()
        t.join()
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
