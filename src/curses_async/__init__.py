# /usr/bin/env python3

from __future__ import annotations

# Standard libraries.
import collections.abc
import curses
import functools
import logging
import os
import signal
import types
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
    None,  # Send type
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


class _FutureWaiting:
    pass


class _FutureCancelled:
    pass


class _FutureException:
    exception: BaseException


class _FutureResult(typing.Generic[_T]):
    result: _T


_FutureState = _FutureWaiting | _FutureCancelled | _FutureException | _FutureResult[_T]


def cut(source: _Copyable) -> _Copyable:
    try:
        return source.copy()
    finally:
        source.clear()


class Future(Coroutine[_T]):

    __Self = typing.TypeVar("__Self", bound="Future[_T]")

    _state: _FutureState[_T] = _FutureWaiting()

    def __init__(
        self,
        *args: typing.Any,
        loop: "EventLoop" | None = None,
        **kwargs: typing.Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._done_callbacks: list[DoneCallback] = []
        if loop is None:
            loop = get_running_loop()
        self._loop: "EventLoop" = loop

    def result(self) -> _T:
        state = self._state
        if isinstance(state, _FutureResult):
            return state.result
        if isinstance(state, _FutureException):
            raise state.exception
        if isinstance(state, _FutureCancelled):
            raise CancelledError()
        raise InvalidStateError()

    def set_result(self, result: _T) -> None:
        if self.done():
            raise InvalidStateError()
        _state = self._state = _FutureResult()
        _state.result = result
        self._call_done_callbacks()

    def set_exception(self, exception: BaseException) -> None:
        if self.done():
            raise InvalidStateError()
        _state = self._state = _FutureException()
        _state.exception = exception
        self._call_done_callbacks()

    def done(self) -> bool:
        return isinstance(
            self._state,
            _FutureCancelled | _FutureException | _FutureResult,
        )

    def cancelled(self) -> bool:
        return isinstance(self._state, _FutureCancelled)

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
        self._state = _FutureCancelled()
        self._call_done_callbacks()
        return True

    def _call_done_callbacks(self) -> None:
        loop = self.get_loop()
        for callback in self._done_callbacks.copy():
            loop.call_soon(callback, self)

    def exception(self) -> BaseException | None:
        state = self._state
        if isinstance(state, _FutureResult):
            return None
        if isinstance(state, _FutureException):
            return state.exception
        if isinstance(state, _FutureCancelled):
            raise CancelledError()
        raise InvalidStateError()

    def get_loop(self) -> "EventLoop":
        return self._loop

    def send(self: __Self, value: None) -> __Self:
        assert value is None, "Futures do not receive sent values."
        del value
        if self.done():
            raise StopIteration(self.result())
        return self

    def throw(
        self,
        typ: BaseException | type[BaseException] | None = None,
        val: object | None = None,
        tb: types.TracebackType | None = None,
    ) -> typing.Any:
        del tb
        assert typ is not None
        if isinstance(typ, GeneratorExit):
            return
        raise typ


class _TaskCancelling(_FutureWaiting):
    pass


class Task(Future[_T]):

    __Self = typing.TypeVar("__Self", bound="Task[_T]")

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
        if not isinstance(self._state, _TaskCancelling):
            # Change state first to avoid infinite recursion.
            self._state = _TaskCancelling()
            awaited_on = self._awaited_on
            if awaited_on is not None:
                awaited_on.cancel()
        return True

    def send(self: __Self, value: None) -> __Self:
        assert value is None, "Tasks do not receive sent values."
        if isinstance(self._state, _FutureCancelled):
            self.throw(CancelledError())
        else:
            self._step_coro(
                functools.partial(
                    self.get_coro().send,
                    value,
                )
            )
        return super().send(value)

    def _is_send_ready(self) -> bool:
        if self.done():
            return False
        if isinstance(self._state, _TaskCancelling):
            return True
        awaited_on = self._awaited_on
        if awaited_on is None:
            return True
        return awaited_on.done()

    def throw(
        self,
        typ: BaseException | type[BaseException] | None = None,
        val: object | None = None,
        tb: types.TracebackType | None = None,
    ) -> typing.Any:
        del tb
        assert typ is not None
        if isinstance(typ, type):
            assert val is not None
            assert isinstance(val, typ)
            typ = val
        self._step_coro(
            functools.partial(
                self.get_coro().throw,
                typ,
            )
        )

    def close(self) -> None:
        self.throw(CancelledError())

    def _step_coro(
        self,
        stepper: typing.Callable[[], Future[typing.Any]],
    ) -> None:
        if self.done():
            raise StopIteration()

        try:
            self._awaited_on = stepper()
        except StopIteration as error:
            self.set_result(error.value)
            raise
        except CancelledError as error:
            super().cancel()
            raise StopIteration() from error
        except BaseException as error:
            self.set_exception(error)
            raise StopIteration() from error

    def get_coro(self) -> Coroutine[_T]:
        return self._coro


class EventLoop:

    unpause_signal = signal.SIGINT if os.name != "nt" else signal.CTRL_C_EVENT
    _instance: "EventLoop" | None = None

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        assert self._instance is None
        EventLoop._instance = self
        self._getch_future: Future[int] | None = None
        self._is_stopping = False
        self._is_waiting = False
        self._pid = os.getpid()
        self._all_tasks: list[Task[typing.Any]] = []
        self._stdscr: curses.window | None = None

    def open(self) -> curses.window:
        stdscr = self._stdscr
        if stdscr is not None:
            return stdscr
        stdscr = self._stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(1)
        curses.start_color()
        # signal.signal(signal.SIGINT, signal.SIG_IGN)
        return stdscr

    def run_forever(self) -> None:
        self.run_until_complete(self.create_future())

    def run_until_complete(self, future: Future[_T]) -> _T | None:
        all_tasks = self._all_tasks
        try:
            # Always iterate once before exiting.
            while True:
                ready_tasks = (
                    task
                    for task in all_tasks
                    if task._is_send_ready()  # pylint: disable=protected-access
                )
                has_ready = False
                for task in ready_tasks:
                    has_ready = True
                    try:
                        task.send(None)
                    except StopIteration:
                        pass
                if has_ready:
                    all_tasks[:] = [task for task in all_tasks if not task.done()]
                else:
                    self._set_getch_result()
                if future.done():
                    return future.result()
                if self._is_stopping:
                    break
        finally:
            self._is_stopping = False
        return None

    def stop(self) -> None:
        self._is_stopping = True

    def is_closed(self) -> bool:
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

    def call_soon(
        self,
        callback: typing.Callable[..., typing.Any],
        *args: typing.Any,
    ) -> None:
        # TODO: Should return a Handle object.

        def wrapped_callback() -> Coroutine[None]:
            callback(*args)
            return
            # Force function into a zero-step generator.
            assert False, "Unreachable."  # pylint: disable=unreachable
            yield from self.create_future()

        self.create_task(wrapped_callback())

    def create_future(self) -> Future[_T]:
        return Future[_T](loop=self)

    def create_task(self, coro: Coroutine[_T]) -> Task[_T]:
        task = Task(coro, loop=self)
        self._all_tasks.append(task)
        return task

    def call_soon_threadsafe(
        self,
        callback: typing.Callable[..., typing.Any],
        *args: typing.Any,
    ) -> None:
        self.call_soon(callback, *args)
        if self._is_waiting:
            os.kill(self._pid, self.unpause_signal)

    def getch(self) -> Coroutine[int]:
        future = self._getch_future
        if future is None:
            future = self._getch_future = Future[int](loop=self)
        # return (yield from future)
        next_ch = yield from future
        return next_ch

    def _set_getch_result(self) -> None:
        stdscr = self._stdscr
        assert stdscr is not None
        next_ch = None
        try:
            self._is_waiting = True
            next_ch = stdscr.getch()
        except KeyboardInterrupt:
            return
        finally:
            self._is_waiting = False

        future = self._getch_future
        if future is not None:
            future.set_result(next_ch)
        self._getch_future = None


def get_running_loop() -> EventLoop:
    loop = EventLoop._instance  # pylint: disable=protected-access
    if loop is None:
        raise RuntimeError("No running loop.")
    return loop


def run(coro: Coroutine[_T]) -> _T | None:
    loop = EventLoop()
    try:
        loop.open()
        task = loop.create_task(coro)
        return loop.run_until_complete(task)
    finally:
        loop.close()
