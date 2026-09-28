import time
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
from database import db
from command import get_admin_list, wrap_bold

logger = logging.getLogger(__name__)

@Client.on_message(filters.command("broadcast") & filters.private & filters.incoming)
async def broadcast_command(client, message):
    if message.from_user.id not in get_admin_list():
        return

    if not message.reply_to_message:
        await message.reply_text(wrap_bold("❌ Error: Please use this command by replying to any message you want to broadcast."), parse_mode=enums.ParseMode.HTML)
        return

    status_msg = await message.reply_text(wrap_bold("⏳ Broadcast process initiated...\n\nFetching user collection from matrix pipeline nodes..."), parse_mode=enums.ParseMode.HTML)

    all_users = await db.get_all_users()
    total_users = len(all_users)
    success_count = 0
    failed_count = 0
    start_time = time.time()

    for user in all_users:
        user_id = user["_id"]
        try:
            await message.reply_to_message.copy(chat_id=user_id)
            success_count += 1
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            try:
                await message.reply_to_message.copy(chat_id=user_id)
                success_count += 1
            except Exception:
                failed_count += 1
        except (UserIsBlocked, InputUserDeactivated):
            failed_count += 1
        except Exception:
            failed_count += 1

    time_taken = round(time.time() - start_time, 2)

    final_report = (
        f"<b>📢 BROADCAST TASK COMPLETED Successfully ✅</b>\n\n"
        f"📊 <b>Total Targeted Profiles:</b> <code>{total_users}</code>\n"
        f"🟢 <b>Successful Delivery Count:</b> <code>{success_count}</code>\n"
        f"🔴 <b>Failed/Blocked Profile Logs:</b> <code>{failed_count}</code>\n"
        f"⏱️ <b>Total Elapsed Timeline:</b> <code>{time_taken} Seconds</code>"
    )

    await status_msg.edit_text(wrap_bold(final_report), parse_mode=enums.ParseMode.HTML)