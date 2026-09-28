from pyrogram.types import BotCommand

async def set_bot_commands(client):
    commands = [
        BotCommand("start", "🚀 START DOWNLOADER"),
        BotCommand("caption", "📝 SET YOUR CUSTOM CAPTION"),
        BotCommand("delcaption", "🗑️ REMOVE YOUR CUSTOM CAPTION"),
        BotCommand("set_ch", "🔗 CONNECT YOUR CHANNEL"),
        BotCommand("stats", "📊 LIVE SERVER & BOT STATUS"),
        BotCommand("broadcast", "📢 [ADMIN] BROADCAST MESSAGE"),
        BotCommand("restart", "🔄 [ADMIN] RESTART SYSTEM")
    ]

    try:
        await client.set_bot_commands(commands)
    except Exception:
        pass