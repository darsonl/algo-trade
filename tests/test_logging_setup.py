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

