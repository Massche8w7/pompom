import time
from database import db
from command import START_TIME, send_raw_telegram_message, edit_raw_telegram_message

def format_uptime(seconds):
    seconds = int(seconds)
    d, s = divmod(seconds, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{d}d {h}h {m}m {s}s"

async def stats_command(client, message):
    users = await db.get_total_users()
    uptime = time.time() - START_TIME

    text = (
        f"<b>📊 LIVE SYSTEM STATISTICS & STATUS 💎</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>TOTAL USERS:</b>\n"
        f"<code>{users}</code>\n\n"
        f"⏳ <b>SERVER UPTIME:</b>\n"
        f"<code>{format_uptime(uptime)}</code>\n\n"
        f"🛡 <b>SYSTEM STABILITY:</b>\n"
        f"<code>99.9% STABLE PERFORMANCE</code>\n\n"
        f"⚡ <b>SERVER STATUS:</b>\n"
        f"<code>ONLINE & PROCESSING</code>\n\n"
        f"🚀 <b>DOWNLOAD ENGINE:</b>\n"
        f"<code>ULTRA FAST MODE ENABLED</code>\n\n"
        f"🔥 <b>CURRENT PERFORMANCE:</b>\n"
        f"<code>OPTIMIZED & RUNNING SMOOTHLY</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"💎 THANK YOU FOR USING OUR SERVICES."
    )

    await send_raw_telegram_message(message.chat.id, text)

async def show_stats_callback(client, callback_query):
    users = await db.get_total_users()
    uptime = time.time() - START_TIME

    text = (
        f"<b>📊 LIVE SYSTEM STATISTICS & STATUS 💎</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>TOTAL USERS:</b>\n"
        f"<code>{users}</code>\n\n"
        f"⏳ <b>SERVER UPTIME:</b>\n"
        f"<code>{format_uptime(uptime)}</code>\n\n"
        f"🛡 <b>SYSTEM STABILITY:</b>\n"
        f"<code>99.9% STABLE PERFORMANCE</code>\n\n"
        f"⚡ <b>SERVER STATUS:</b>\n"
        f"<code>ONLINE & PROCESSING</code>\n\n"
        f"🚀 <b>DOWNLOAD ENGINE:</b>\n"
        f"<code>ULTRA FAST MODE ENABLED</code>\n\n"
        f"🔥 <b>CURRENT PERFORMANCE:</b>\n"
        f"<code>OPTIMIZED & RUNNING SMOOTHLY</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"💎 THANK YOU FOR USING OUR SERVICES."
    )

    await edit_raw_telegram_message(callback_query.message.chat.id, callback_query.message.id, text)
