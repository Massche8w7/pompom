import time
import os
import sys
import re
import asyncio
from pyrogram import enums
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import db

try:
    from info import ADMIN_IDS
except ImportError:
    try:
        from info import ADMINS as ADMIN_IDS
    except ImportError:
        ADMIN_IDS = [123456789]


async def stopbatch_command(client, message):
    from info import ADMIN_IDS
    admin_ids = ADMIN_IDS if isinstance(ADMIN_IDS, (list, tuple)) else [ADMIN_IDS]
    if not message.from_user or message.from_user.id not in admin_ids:
        return
    from pmfilter import ADMIN_BATCH_CANCEL
    ADMIN_BATCH_CANCEL.add(message.from_user.id)
    await send_raw_telegram_message(message.chat.id, "<b>🛑 Stop request sent.</b>\n\nBatch will stop after the current file finishes.")

def get_admin_list():
    if isinstance(ADMIN_IDS, int): return [ADMIN_IDS]
    elif isinstance(ADMIN_IDS, list): return ADMIN_IDS
    return []

START_TIME = time.time()

_CLIENT = None


def set_client(client):
    global _CLIENT
    _CLIENT = client


def _get_client():
    if _CLIENT is None:
        raise RuntimeError("Telegram client not registered. Call command.set_client(app) on startup.")
    return _CLIENT


def _normalize_keyboard(markup):
    if markup is None:
        return None
    if isinstance(markup, InlineKeyboardMarkup):
        return markup
    rows = markup.get("inline_keyboard", []) if isinstance(markup, dict) else []
    new_rows = []
    for row in rows:
        buttons = []
        for btn in row:
            if isinstance(btn, dict):
                text = btn.get("text", "")
                callback_data = btn.get("callback_data")
                url = btn.get("url")
            else:
                text = getattr(btn, "text", "")
                callback_data = getattr(btn, "callback_data", None)
                url = getattr(btn, "url", None)
            if callback_data is not None:
                buttons.append(InlineKeyboardButton(text, callback_data=callback_data))
            elif url is not None:
                buttons.append(InlineKeyboardButton(text, url=url))
        if buttons:
            new_rows.append(buttons)
    if not new_rows:
        return None
    return InlineKeyboardMarkup(new_rows)


_STATUS_MSGS = {}  # chat_id -> message_id of the chat's last tracked status message


def wrap_bold(text):
    """Ensure the whole message renders bold under HTML parse_mode.

    Inner <b> tags are stripped and a single outer <b> pair is applied, so
    the entire message renders bold with no nested bold tags.
    """
    if text is None:
        return None
    s = str(text)
    if not s.strip():
        return s
    inner = re.sub(r"</?b>", "", s)
    return f"<b>{inner}</b>"


async def send_raw_telegram_message(chat_id, text, reply_markup=None, reply_to_message_id=None):
    client = _get_client()
    kwargs = dict(
        chat_id=chat_id,
        text=wrap_bold(text),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True
    )
    keyboard = _normalize_keyboard(reply_markup)
    if keyboard is not None:
        kwargs["reply_markup"] = keyboard
    if reply_to_message_id is not None:
        kwargs["reply_to_message_id"] = reply_to_message_id
    sent = await client.send_message(**kwargs)
    return {"result": {"message_id": sent.id}}


async def edit_raw_telegram_message(chat_id, message_id, text, reply_markup=None):
    client = _get_client()
    try:
        kwargs = dict(
            chat_id=chat_id,
            message_id=message_id,
            text=wrap_bold(text),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True
        )
        keyboard = _normalize_keyboard(reply_markup)
        if keyboard is not None:
            kwargs["reply_markup"] = keyboard
        await client.edit_message_text(**kwargs)
    except MessageNotModified:
        # Content identical to the previous edit — nothing to update.
        return


async def send_status_message(chat_id, text, reply_markup=None, reply_to_message_id=None):
    """Send a message and remember it as the chat's current status message."""
    res = await send_raw_telegram_message(chat_id, text, reply_markup=reply_markup, reply_to_message_id=reply_to_message_id)
    mid = (res.get("result") or {}).get("message_id")
    if mid is not None:
        _STATUS_MSGS[chat_id] = mid
    return res


async def update_status_message(chat_id, text, reply_markup=None, reply_to_message_id=None):
    """Update the chat's status message IN PLACE (editMessageText) instead of
    spamming new messages. Falls back to sending a fresh message when there is
    nothing to edit or the edit fails, and re-tracks the new id, so repeated
    updates always reuse one message per chat.
    """
    mid = _STATUS_MSGS.get(chat_id)
    if mid is not None:
        try:
            await edit_raw_telegram_message(chat_id, mid, text, reply_markup=reply_markup)
            return {"result": {"message_id": mid}}
        except MessageNotModified:
            return {"result": {"message_id": mid}}
        except Exception:
            _STATUS_MSGS.pop(chat_id, None)
    return await send_status_message(chat_id, text, reply_markup=reply_markup, reply_to_message_id=reply_to_message_id)


async def start_command(client, message):
    await db.add_user(message.chat.id)
    first_name = message.from_user.first_name if message.from_user else "User"

    emoji_msg = await client.send_message(message.chat.id, "🙋")

    text = (
        f"<b>🌟 Hey {first_name}! Welcome to the Ultimate Terabox Downloader 🚀</b>\n\n"
        f"I am your advanced, high-speed Terabox processing engine. Send me any <b>Terabox link</b>, and I will extract and download it at lightning speed! ⚡\n\n"
        f"<b>🔥 My Capabilities:</b>\n"
        f"🔸 <b>Extreme Speeds:</b> Up to 16x Threads parallel downloading.\n"
        f"🔸 <b>Bulk Processing:</b> Send a <code>.txt</code> file to download hundreds of links automatically.\n"
        f"🔸 <b>Direct Streaming:</b> Watch videos instantly on the web without downloading.\n\n"
        f"<i>Send me any Terabox link right now to experience the magic! 👇</i>"
    )

    markup = {
        "inline_keyboard": [
            [{"text": "ℹ️ About & Stats", "callback_data": "about_stats_btn", "style": "success"}]
        ]
    }

    await send_raw_telegram_message(message.chat.id, text, reply_markup=markup)

    try:
        await client.delete_messages(message.chat.id, emoji_msg.id)
    except Exception:
        pass

async def set_ch_command(client, message):
    parts = message.text.split()
    if len(parts) != 2:
        await send_raw_telegram_message(message.chat.id, "<b>♻ Usage:</b>\n\n<code>/set_ch -100xxxxxxxxxx</code>\n<code>/set_ch remove</code> (To unlink channel)\n\nUse this to receive files directly into your channel. Make sure I am an admin!")
        return

    channel_id = parts[1].lower()

    if channel_id in ["0", "remove", "off", "none"]:
        await db.set_custom_channel(message.chat.id, None)
        await send_raw_telegram_message(message.chat.id, "<b>✅ Channel Unlinked Successfully!</b>\n\nAll your future downloads will now be sent directly to your PM.")
        return

    if not channel_id.startswith("-100"):
        await send_raw_telegram_message(message.chat.id, "<b>❌ Invalid Channel ID!</b>\nIt must start with <code>-100</code>")
        return

    try:
        channel_id_int = int(channel_id)
        await db.set_custom_channel(message.chat.id, channel_id_int)
        await send_raw_telegram_message(message.chat.id, f"<b>✅ Channel Connected Successfully!</b>\n\nAll your future downloads will be sent directly to <code>{channel_id}</code>.")
    except ValueError:
        await send_raw_telegram_message(message.chat.id, "<b>❌ Invalid Channel ID format!</b>")

async def restart_command(client, message):
    if message.from_user.id not in get_admin_list(): return

    await send_raw_telegram_message(message.chat.id, "<b>🔄 System Reboot Initiated...</b>\n\nForcing server native restart.")
    os._exit(1)

async def txt_command(client, message):
    user_id = message.chat.id
    links_docs = await db.get_all_user_links()

    if not links_docs:
        await client.send_message(user_id, wrap_bold("<b>❌ No links recorded in the database yet.</b>"), parse_mode=enums.ParseMode.HTML)
        return

    txt_filename = f"user_links_{user_id}_{int(time.time())}.txt"
    try:
        with open(txt_filename, "w", encoding="utf-8") as f:
            for doc in links_docs:
                f.write(f"{doc.get('link')}\n")

        caption_text = wrap_bold(f"📄 Exported Links Report\n\nTotal Links: <code>{len(links_docs)}</code>")
        await client.send_document(user_id, document=txt_filename, caption=caption_text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await client.send_message(user_id, wrap_bold(f"<b>❌ Failed to generate TXT file:</b> <code>{str(e)}</code>"), parse_mode=enums.ParseMode.HTML)
    finally:
        if os.path.exists(txt_filename):
            try:
                os.remove(txt_filename)
            except Exception:
                pass

