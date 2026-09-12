"""main.py owns console logging; discord.py must not add a second handler.

`main()` installs a RotatingFileHandler and a StreamHandler on the ROOT logger.
`discord.Client.run()` then defaults to `log_handler=MISSING`, which calls
`discord.utils.setup_logging(root=root_logger)` with `root_logger=False` -- so it
attaches its own StreamHandler to the `discord` logger and, crucially, never sets
`propagate=False`. Every `discord.*` record was therefore emitted twice on the
console: once by discord.py's handler, once by ours after propagation.

Only the console doubled. The file handler is on root and saw each record once,
which is why `logs/algo_trade.log` looked correct while the terminal did not.
"""
import ast
import logging
import pathlib

import discord
from discord.utils import setup_logging

MAIN_PY = pathlib.Path(__file__).resolve().parent.parent / "main.py"


def _bot_run_call() -> ast.Call:
    """The `bot.run(...)` call node in main.py, or fail saying it vanished.

    Anchored by AST rather than a string match: a reformat, a line break or an
    added kwarg must not silently turn this test into one that asserts nothing.
    """
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "bot"
    ]
    assert len(calls) == 1, f"expected exactly one bot.run(...) in main.py, found {len(calls)}"
    return calls[0]


def test_main_tells_discord_py_not_to_install_its_own_log_handler():
    """`bot.run` must pass log_handler=None.

    That kwarg is the only thing suppressing the duplicate console handler --
    discord.py skips setup_logging entirely when it is None. Dropping it brings
    the double-printing straight back, silently, since nothing else observes it.
    """
    call = _bot_run_call()
    kwargs = {kw.arg: kw.value for kw in call.keywords}

    assert "log_handler" in kwargs, (
        "bot.run() no longer passes log_handler=None, so discord.py will install "
        "a StreamHandler on the 'discord' logger and every discord record will be "
        "printed twice on the console."
    )
    assert isinstance(kwargs["log_handler"], ast.Constant) and kwargs["log_handler"].value is None, (
        "log_handler must be exactly None; any handler object re-enables the "
        "duplicate emission this kwarg exists to prevent."
    )


def test_discord_py_still_duplicates_records_without_the_kwarg():
    """Pin the DEPENDENCY contract that makes the kwarg necessary.

    This one characterises discord.py, not our code, so it passes today by
    construction. It earns its place by failing on an upgrade that changes the
    behaviour -- if discord.py ever stops adding the handler or starts setting
    propagate=False, the workaround above is obsolete and this says so.
    """
    discord_logger = logging.getLogger(discord.__name__)
    had = list(discord_logger.handlers)
    try:
        discord_logger.handlers.clear()
        setup_logging(handler=logging.NullHandler(), root=False)

        assert len(discord_logger.handlers) == 1, (
            "discord.utils.setup_logging(root=False) no longer adds a handler to "
            "the 'discord' logger; the log_handler=None workaround may be obsolete."
        )
        assert discord_logger.propagate is True, (
            "discord.py now suppresses propagation, so records would no longer "
            "reach main.py's root handler twice; re-evaluate the workaround."
        )
    finally:
        discord_logger.handlers[:] = had


# ─── Which handlers root carries ─────────────────────────────────────────────


class _FakeStream:
    """A stream whose isatty() answer is fixed, or explodes."""

    def __init__(self, tty, raises=False):
        self._tty = tty
        self._raises = raises

    def isatty(self):
        if self._raises:
            raise ValueError("stream is closed")
        return self._tty


def _kinds(handlers):
    """Handler classes, most-derived name first, for readable assertions."""
    return sorted(type(h).__name__ for h in handlers)


def _close(handlers):
    for h in handlers:
        h.close()


def test_a_terminal_gets_both_the_file_and_the_console_handler(tmp_path):
    """Interactive runs are unchanged: you still watch the bot in the terminal."""
    import main

    handlers = main.build_log_handlers(tmp_path, stream=_FakeStream(tty=True))
    try:
        assert _kinds(handlers) == ["RotatingFileHandler", "StreamHandler"]
    finally:
        _close(handlers)


def test_a_redirected_stream_gets_only_the_file_handler(tmp_path):
    """The Task Scheduler deployment redirects stderr to logs/stdout.log.

    A StreamHandler there writes a second, unrotated copy of every record the
    RotatingFileHandler already has, and keeps a console alive as a hazard --
    Windows QuickEdit lets a stray selection suspend writes, block the logging
    lock and wedge the scheduler. Drop the handler; the file log loses nothing.
    """
    import main

    handlers = main.build_log_handlers(tmp_path, stream=_FakeStream(tty=False))
    try:
        assert _kinds(handlers) == ["RotatingFileHandler"]
    finally:
        _close(handlers)


def test_an_absent_stream_gets_only_the_file_handler(tmp_path):
    """pythonw.exe leaves sys.stderr as None; constructing a StreamHandler on it
    would raise inside logging on the first record, taking the whole log with it."""
    import main

    handlers = main.build_log_handlers(tmp_path, stream=None)
    try:
        assert _kinds(handlers) == ["RotatingFileHandler"]
    finally:
        _close(handlers)


def test_a_stream_whose_isatty_raises_gets_only_the_file_handler(tmp_path):
    """Fails closed. A closed or detached stream must not abort startup, and the
    file log is the one that has to survive."""
    import main

    handlers = main.build_log_handlers(tmp_path, stream=_FakeStream(tty=False, raises=True))
    try:
        assert _kinds(handlers) == ["RotatingFileHandler"]
    finally:
        _close(handlers)


def test_the_file_handler_rotates_and_lands_in_the_given_directory(tmp_path):
    """Rotation is what bounds the log; pin it so a refactor cannot drop it."""
    import main

    handlers = main.build_log_handlers(tmp_path, stream=None)
    try:
        fh = handlers[0]
        assert fh.baseFilename == str(tmp_path / "algo_trade.log")
        assert fh.maxBytes == 5 * 1024 * 1024
        assert fh.backupCount == 3
    finally:
        _close(handlers)


def test_every_handler_shares_one_format(tmp_path):
    """Two handlers formatting differently is how the duplicate console lines
    were legible as two different loggers in the first place."""
    import main

    handlers = main.build_log_handlers(tmp_path, stream=_FakeStream(tty=True))
    try:
        formats = {h.formatter._fmt for h in handlers}
        assert len(formats) == 1, formats
    finally:
        _close(handlers)
