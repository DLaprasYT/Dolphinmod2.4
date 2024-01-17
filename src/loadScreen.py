"""Displays a loading menu while packages, palettes, etc are being loaded.

All the actual display is done in a subprocess, so we can allow interaction while
the main process is busy loading.

The id() of the main-process object is used to identify loadscreens.
"""
from __future__ import annotations


from typing import (
    AsyncGenerator, Collection, Generator, MutableSet, Set, Tuple, List, TypeVar,
    Any, Type,
)
from types import TracebackType
from typing_extensions import Literal
from weakref import WeakSet
import contextlib
import multiprocessing
import queue

import attrs
import srctools.logger
import trio

from config.gen_opts import GenOptions
from config import APP
from transtoken import TransToken
import utils


# Keep a reference to all loading screens, so we can close them globally.
_ALL_SCREENS: MutableSet[LoadScreen] = WeakSet()

# Queues we use to send data to the daemon and vice versa.
# DAEMON is used for messages from the daemon.
_QUEUE_SEND_LOAD = multiprocessing.Queue()  # loadscreen -> daemon
_QUEUE_REPLY_LOAD = multiprocessing.Queue()  # daemon -> loadscreen
_QUEUE_SEND_LOGGING: multiprocessing.Queue[
    Tuple[Literal['log'], str, str] |
    Tuple[Literal['visible'], bool, None] |
    Tuple[Literal['level'], str | int, None],
] = multiprocessing.Queue()  # logging -> daemon
_QUEUE_REPLY_LOGGING: multiprocessing.Queue[
    Tuple[Literal['level'], str] |
    Tuple[Literal['visible'], bool] |
    Tuple[Literal['quit'], None],
] = multiprocessing.Queue()  # daemon -> logging

T = TypeVar('T')


LOGGER = srctools.logger.get_logger(__name__)
# All tokens used by the subprocess. We translate here before passing it down.
TRANSLATIONS = {
    'skip': TransToken.ui('Skipped!'),
    'version': TransToken.ui('Version: {ver}').format(ver=utils.BEE_VERSION),
    'cancel': TransToken.ui('Cancel'),
    'clear': TransToken.ui('Clear'),
    'copy': TransToken.ui('Copy'),
    'log_show': TransToken.ui('Show:'),
    'log_title': TransToken.ui('Logs - {ver}').format(ver=utils.BEE_VERSION),
    'level_debug': TransToken.ui('Debug messages'),
    'level_info': TransToken.ui('Default'),
    'level_warn': TransToken.ui('Warnings Only'),
}


def show_main_loader(is_compact: bool) -> None:
    """Special function, which sets the splash screen compactness."""
    _QUEUE_SEND_LOAD.put(('set_is_compact', id(main_loader), (is_compact, )))
    main_loader._show()


def set_force_ontop(ontop: bool) -> None:
    """Set whether screens will be forced on top."""
    _QUEUE_SEND_LOAD.put(('set_force_ontop', None, ontop))


@contextlib.contextmanager
def suppress_screens() -> Generator[None, None, None]:
    """A context manager to suppress loadscreens while the body is active."""
    active = []
    for screen in _ALL_SCREENS:
        if not screen.active:
            continue
        screen.suppress()
        active.append(screen)
    try:
        yield None
    finally:
        for screen in active:
            screen.unsuppress()


class ScreenStage:
    """A single stage in a loading screen."""
    def __init__(self, title: TransToken) -> None:
        self.title = title
        self.id = hex(id(self))
        self._bound: Set[LoadScreen] = set()
        self._current = 0
        self._max = 0
        self._skipped = False

    async def set_length(self, num: int) -> None:
        """Change the current length of this stage."""
        self._max = num
        for screen in list(self._bound):
            screen._send_msg('set_length', self.id, num)
        await trio.sleep(0)

    async def step(self, info: object = None) -> None:
        """Increment one step."""
        self._current += 1
        self._skipped = False
        for screen in list(self._bound):
            screen._send_msg('step', self.id)
        await trio.sleep(0)

    async def skip(self) -> None:
        """Skip this stage."""
        self._current = 0
        self._skipped = True
        for screen in list(self._bound):
            screen._send_msg('skip_stage', self.id)
        await trio.sleep(0)

    async def iterate(self, seq: Collection[T]) -> AsyncGenerator[T, None]:
        """Tie the progress of a stage to a sequence of some kind."""
        await self.set_length(len(seq))
        for item in seq:
            yield item
            await self.step()


class LoadScreen:
    """LoadScreens show a loading screen for items.

    stages should be (id, title) pairs for each screen stage.
    Each stage can be stepped independently, referenced by the given ID.
    The title can be blank.
    """

    def __init__(
        self,
        *stages: ScreenStage,
        title_text: TransToken,
        is_splash: bool = False,
    ) -> None:
        # active determines whether the screen is on, and if False stops most
        # functions from doing anything
        self.active = False
        self.stages: List[ScreenStage] = list(stages)
        self.title = title_text
        self._scope: trio.CancelScope | None = None

        init: List[Tuple[str, str]] = [
            (stage.id, str(stage.title))
            for stage in stages
        ]

        # Order the daemon to make this screen. We pass translated text in for the splash screen.
        self._send_msg('init', is_splash, str(title_text), init)
        _ALL_SCREENS.add(self)

    def __enter__(self) -> LoadScreen:
        """LoadScreen can be used as a context manager.

        Inside the block, the screen will be visible. Cancelling will exit
        to the end of the with block.
        """
        if self._scope is not None:
            raise ValueError('Cannot re-enter loading screens!')
        self._scope = trio.CancelScope().__enter__()
        self._show()
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        """Hide the loading screen. If the Cancelled exception was raised, swallow that.
        """
        scope = self._scope
        if scope is None:
            raise ValueError('Already exited?')
        self._scope = None
        try:
            self.active = False
            self._send_msg('reset')
            for stage in self.stages:
                stage._bound.discard(self)
        finally:
            # Always call __exit__(), but only return if reset() didn't raise.
            res = scope.__exit__(exc_type, exc_val, exc_tb)
        return res

    def _send_msg(self, command: str, *args: Any) -> None:
        """Send a message to the daemon."""
        _QUEUE_SEND_LOAD.put((command, id(self), args))
        # Check the messages coming back as well.
        while True:
            arg: Any
            try:
                command, arg = _QUEUE_REPLY_LOAD.get_nowait()
            except queue.Empty:
                break
            if command == 'main_set_compact':
                # Save the compact state to the config.
                conf = APP.get_cur_conf(GenOptions)
                APP.store_conf(attrs.evolve(conf, compact_splash=arg))
            elif command == 'cancel':
                if self._scope is not None:
                    self._scope.cancel()
            else:
                raise ValueError('Bad command from daemon: ' + repr(command))

    def _show(self) -> None:
        """Display the loading screen."""
        self.active = True
        # Translate and send these across now.
        self._send_msg('show', str(self.title), [str(stage.title) for stage in self.stages])
        for stage in self.stages:
            stage._bound.add(self)

    def destroy(self) -> None:
        """Permanently destroy this screen and cleanup."""
        self.active = False
        self._send_msg('destroy')
        for stage in self.stages:
            stage._bound.discard(self)
        _ALL_SCREENS.remove(self)

    def suppress(self) -> None:
        """Temporarily hide the screen."""
        self.active = False
        self._send_msg('hide')

    def unsuppress(self) -> None:
        """Undo temporarily hiding the screen."""
        self.active = True
        self._send_msg('show', str(self.title), [str(stage.title) for stage in self.stages])


def shutdown() -> None:
    """Instruct the daemon process to shut down."""
    try:
        _QUEUE_SEND_LOAD.put(('quit_daemon', None, None))
        _QUEUE_SEND_LOAD.close()
        _QUEUE_REPLY_LOAD.close()
        _QUEUE_SEND_LOGGING.close()
        _QUEUE_REPLY_LOGGING.close()
    except (BrokenPipeError, ValueError):  # Already quit, don't care.
        pass


def update_translations() -> None:
    """Update the translations."""
    _QUEUE_SEND_LOAD.put((
        'update_translations', 0,
        {key: str(tok) for key, tok in TRANSLATIONS.items()},
    ))


_BG_PROC: multiprocessing.Process | None = None


def start_daemon() -> None:
    """Spawn the deamon process."""
    global _BG_PROC
    if _BG_PROC is not None:
        raise ValueError('Daemon already started!')

    # Initialise the daemon.
    _BG_PROC = multiprocessing.Process(
        target=utils.run_bg_daemon,
        args=(
            _QUEUE_SEND_LOAD, _QUEUE_REPLY_LOAD, _QUEUE_SEND_LOGGING, _QUEUE_REPLY_LOGGING,
            # Convert and pass translation strings.
            {key: str(tok) for key, tok in TRANSLATIONS.items()},
        ),
        name='bg_daemon',
        daemon=True,
    )
    _BG_PROC.start()


MAIN_PAK = ScreenStage(TransToken.ui('Packages'))
MAIN_OBJ = ScreenStage(TransToken.ui('Loading Objects'))
MAIN_UI = ScreenStage(TransToken.ui('Initialising UI'))

main_loader = LoadScreen(
    MAIN_PAK, MAIN_OBJ, MAIN_UI,
    title_text=TransToken.ui('Better Extended Editor for Portal 2'),
    is_splash=True,
)
