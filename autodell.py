import asyncio
from pyrogram import enums
from command import wrap_bold

async def auto_delete_task(
    client,
    chat_id,
    media_msg_id,
    warn_msg_id,
    filename,
    delay=1800
):
    await asyncio.sleep(delay)

    try:
        await client.delete_messages(
            chat_id,
            [media_msg_id, warn_msg_id]
        )

        alert_text = (
            f"<b>🗑️ AUTO DELETE COMPLETED ⚠️</b>\n\n"

            f"━━━━━━━━━━━━━━━━━━\n\n"

            f"📂 <b>FILE REMOVED:</b>\n"
            f"<code>{filename}</code>\n\n"

            f"⏳ <b>DELETE TIMER:</b>\n"
            f"<code>{int(delay / 60)} MINUTES COMPLETED</code>\n\n"

            f"━━━━━━━━━━━━━━━━━━\n\n"

            f"🚀 Your downloaded media has been automatically removed from the server for security and storage optimization.\n\n"

            f"⚡ Generate the download link again anytime using the bot.\n\n"

            f"🛡 SECURE AUTO CLEANUP ENABLED"
        )

        await client.send_message(
            chat_id,
            wrap_bold(alert_text),
            parse_mode=enums.ParseMode.HTML
        )

    except Exception:
        pass
