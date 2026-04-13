import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from discord_bot.bot import compute_share_quantity
from config import Config


def test_exact_division_gives_whole_shares():
    assert compute_share_quantity(price=100.0, max_position_usd=500.0) == 5


def test_floors_to_whole_shares():
    # 500 / 333 = 1.5 → floor to 1
    assert compute_share_quantity(price=333.0, max_position_usd=500.0) == 1


def test_returns_zero_when_price_exceeds_max():
    assert compute_share_quantity(price=600.0, max_position_usd=500.0) == 0


def test_returns_zero_for_zero_price():
    # Guard against division by zero — treat as unaffordable
    assert compute_share_quantity(price=0.0, max_position_usd=500.0) == 0


def test_large_position_many_shares():
    assert compute_share_quantity(price=10.0, max_position_usd=1000.0) == 100


def test_fractional_share_scenario():
    # 500 / 199.99 = 2.5 → floor to 2
    assert compute_share_quantity(price=199.99, max_position_usd=500.0) == 2


# --- G-1: _scan_etf_command ---

def _make_bot_instance():
    """Construct a TradingBot without a real Discord connection by mocking discord.Client.__init__."""
    from discord_bot.bot import TradingBot
    config = Config()
    config.discord_channel_id = 12345
    with patch("discord.Client.__init__", return_value=None), \
         patch("discord.app_commands.CommandTree", return_value=MagicMock()):
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot._scan_callback = None
        bot._scan_etf_callback = None
        bot.tree = MagicMock()
    return bot


@pytest.mark.asyncio
async def test_scan_etf_command_creates_task_when_callback_set():
    """_scan_etf_command calls asyncio.create_task with the callback when _scan_etf_callback is set."""
    from discord_bot.bot import TradingBot

    bot = _make_bot_instance()

    callback_called = {"called": False}

    async def fake_scan_etf():
        callback_called["called"] = True

    bot._scan_etf_callback = fake_scan_etf

    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    created_tasks = []
    original_create_task = asyncio.create_task

    def capture_create_task(coro, **kwargs):
        task = original_create_task(coro, **kwargs)
        created_tasks.append(task)
        return task

    with patch("discord_bot.bot.asyncio.create_task", side_effect=capture_create_task):
        await TradingBot._scan_etf_command(bot, interaction)

    # Wait for any pending tasks to complete
    if created_tasks:
        await asyncio.gather(*created_tasks)

    assert len(created_tasks) == 1, "Expected one task to be created"
    assert callback_called["called"], "Callback should have been invoked via the task"


@pytest.mark.asyncio
async def test_scan_etf_command_does_nothing_when_no_callback():
    """_scan_etf_command does not create a task when _scan_etf_callback is None."""
    from discord_bot.bot import TradingBot

    bot = _make_bot_instance()
    bot._scan_etf_callback = None

    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    with patch("discord_bot.bot.asyncio.create_task") as mock_create_task:
        await TradingBot._scan_etf_command(bot, interaction)

    mock_create_task.assert_not_called()


# --- G-2: send_etf_recommendation ---

@pytest.mark.asyncio
async def test_send_etf_recommendation_posts_embed_and_returns_message_id():
    """send_etf_recommendation fetches channel, builds ETF embed, attaches ApproveRejectView,
    posts via _send_message, and returns str(msg.id)."""
    from discord_bot.bot import TradingBot

    bot = _make_bot_instance()

    fake_msg = MagicMock()
    fake_msg.id = 99001

    fake_channel = MagicMock()
    bot.fetch_channel = AsyncMock(return_value=fake_channel)

    fake_embed = MagicMock()

    with patch("discord_bot.bot.build_etf_recommendation_embed", return_value=fake_embed) as mock_build_embed, \
         patch("discord_bot.bot._send_message", new_callable=AsyncMock, return_value=fake_msg) as mock_send:

        result = await TradingBot.send_etf_recommendation(
            bot,
            rec_id=42,
            ticker="SPY",
            signal="BUY",
            reasoning="Strong momentum.",
            price=450.0,
            rsi=65.0,
            ma50=440.0,
            expense_ratio=0.0009,
        )

    # Channel fetched using the configured channel id
    bot.fetch_channel.assert_awaited_once_with(bot.config.discord_channel_id)

    # Embed built with correct arguments (etf_max_expense_ratio=None, confidence=None when not provided)
    mock_build_embed.assert_called_once_with(
        "SPY", "BUY", "Strong momentum.", 450.0, 65.0, 440.0, 0.0009,
        etf_max_expense_ratio=None, confidence=None
    )

    # _send_message called with the channel and embed
    mock_send.assert_awaited_once()
    call_args = mock_send.call_args[0]
    assert call_args[0] is fake_channel
    assert call_args[1] is fake_embed

    # Returns str(msg.id)
    assert result == "99001"
