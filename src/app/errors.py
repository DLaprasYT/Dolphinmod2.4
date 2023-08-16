"""Handles displaying errors to the user that occur during operations."""
from __future__ import annotations

import types
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Awaitable, Callable, ClassVar, Generator, Protocol, final, overload
from exceptiongroup import ExceptionGroup, BaseExceptionGroup

import attrs
import trio
import srctools.logger

from transtoken import TransToken


LOGGER = srctools.logger.get_logger(__name__)
DEFAULT_TITLE = TransToken.ui("BEEmod Error")
DEFAULT_DESC = TransToken.ui_plural(
    "An error occurred while performing this task:",
    "Multiple errors occurred while performing this task:",
)


@final
@attrs.define(init=False)
class AppError(Exception):
    """An error that occurs when using the app, that should be displayed to the user."""
    message: TransToken

    def __init__(self, message: TransToken) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"AppError: {self.message}"


class Handler(Protocol):
    """The signature of handler functions."""
    def __call__(self, title: TransToken, desc: TransToken, errors: list[AppError]) -> Awaitable[object]:
        ...

@final
class ErrorUI:
    """A context manager which handles processing the errors."""
    title: TransToken
    desc: TransToken
    _errors: list[AppError]

    _handler: ClassVar[Handler | None] = None

    @classmethod
    @contextmanager
    def install_handler(
        cls, handler: Handler,
    ) -> Generator[None, None, None]:
        """Install the handler for displaying errors."""
        if cls._handler is not None:
            raise ValueError("Handler already installed!")
        try:
            cls._handler = handler
            yield
        finally:
            cls._handler = None

    def __init__(
        self,
        title: TransToken = DEFAULT_TITLE,
        desc: TransToken = DEFAULT_DESC,
    ) -> None:
        """Create a UI handler. install_handler() must already be running."""
        if self._handler is None:
            LOGGER.warning("ErrorUI initialised with no handler running!")
        self.title = title
        self.desc = desc
        self._errors = []

    def __repr__(self) -> str:
        return f"<ErrorUI, title={self.title}, {len(self._errors)} errors>"

    @property
    def failed(self) -> bool:
        """Check if the operation has failed."""
        return bool(self._errors)

    def add(self, error: AppError | ExceptionGroup | BaseExceptionGroup) -> None:
        """Log an error having occurred, while still running code.

        If an exception group is passed, this will extract the AppErrors, reraising others.
        """
        if isinstance(error, AppError):
            self._errors.append(error)
        else:
            matching, rest = error.split(AppError)
            if matching is not None:
                self._errors.extend(matching.exceptions)
            if rest is not None:
                raise rest

    async def __aenter__(self) -> ErrorUI:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> bool:
        if isinstance(exc_val, AppError):
            self._errors.append(exc_val)
        elif isinstance(exc_val, BaseExceptionGroup):
            matching, rest = exc_val.split(AppError)
            if rest is not None:
                # Another actual exception occurred. Let the exception group
                # propagate unchanged.
                return False
            if matching is not None:
                self._errors.extend(matching.exceptions)
        elif exc_val is not None:
            # Caught something else, don't suppress.
            if self._errors:
                # Combine both in an exception group.
                raise BaseExceptionGroup(
                    "ErrorUI block raised",
                    [exc_val, *self._errors],
                )

            # Just some other exception, leave it unaltered.
            return False

        if self._errors:
            desc = self.desc.format(n=len(self._errors))
            # We had an error.
            if self._handler is None:
                LOGGER.error(
                    "ErrorUI block failed, but no handler installed!\ntitle={}\ndesc={}\n{}",
                    self.title,
                    desc,
                    "\n".join(map(str, self._errors)),
                )
            else:
                # Do NOT pass self!
                await ErrorUI._handler(self.title, desc, self._errors)
        return True
