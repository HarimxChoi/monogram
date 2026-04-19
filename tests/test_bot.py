"""C4 bot tests — module-level imports and handler signatures."""
from __future__ import annotations


def test_bot_module_imports():
    from monogram.bot import bot, dp, run_bot, send_reply

    assert callable(send_reply)
    assert callable(run_bot)


def test_dispatcher_has_handlers():
    from monogram.bot import dp

    # aiogram 3.x: dp.message has registered handlers
    assert len(dp.message.handlers) >= 3, (
        f"expected at least 3 message handlers (start, status, any), "
        f"got {len(dp.message.handlers)}"
    )
