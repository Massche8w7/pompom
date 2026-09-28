import json
import html
import asyncio
import aiohttp
import re
import os
import time
import urllib.parse
import zipfile
import shutil
from pyrogram import Client, filters, enums, ContinuePropagation
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageNotModified
from database import db
from command import wrap_bold, send_status_message, update_status_message, _STATUS_MSGS
from domain import SUPPORTED_DOMAINS, FETCH_FAIL_NOTES
import logging
from samraplugin import speed_engine, prog, log, autodell
from samraplugin.caption import WAITING_FOR_CAPTION

logger = logging.getLogger(__name__)
TASK_STORE = {}
STREAM_STORE = {}
PENDING_BULK = {}
DETECTED_DOMAIN = None

def get_automated_base_url():
    global DETECTED_DOMAIN
    if DETECTED_DOMAIN:
        return DETECTED_DOMAIN
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        return render_url.rstrip("/")
    heroku_name = os.environ.get("HEROKU_APP_NAME")
    if heroku_name:
        return f"https://{heroku_name}.herokuapp.com"
    railway_url = os.environ.get("RAILWAY_STATIC_URL")
    if railway_url:
        if railway_url.startswith("http"):
            return railway_url.rstrip("/")
        return f"https://{railway_url}"
    return "http://0.0.0.0:8080"

def get_domain_pattern():
    escaped_domains = [re.escape(d) for d in SUPPORTED_DOMAINS]
    return rf"((?:https?://)?\S*({'|'.join(escaped_domains)})\S*)"

def parse_api_duration(d_str):
    if not d_str or d_str in ["00:00", "00"]: return 0
    try:
        parts = str(d_str).split(':')
        if len(parts) == 3: return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2: return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 1: return int(parts[0])
    except Exception: pass
    return 0

def get_bulk_choice_keyboard(batch_id):
    buttons = [[InlineKeyboardButton("⚡ High-Speed Download", callback_data=f"startbulk_{batch_id}")]]
    return InlineKeyboardMarkup(buttons)

async def download_api_thumbnail(url, task_id):
    if not url: return None
    try:
        thumb_path = f"thumb_{task_id}.jpg"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    with open(thumb_path, 'wb') as f: f.write(await response.read())
                    return thumb_path
    except Exception: pass
    return None

async def check_interrupted_batches(client):
    running_batches = await db.get_running_batches()
    for batch in running_batches:
        await db.update_batch_status(batch["_id"], "paused")
        text = f"<b>⚠️ Restart Found</b>\n<b>Links:</b> <code>{batch['total']}</code> | <b>Done:</b> <code>{batch['current_index']}</code>\nClick below."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Resume Queue", callback_data=f"resumebatch_{batch['_id']}")]])
        try:
            await client.send_message(batch["chat_id"], wrap_bold(text), reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        except Exception:
            pass
        await asyncio.sleep(5)


ADMIN_BATCH_CANCEL = set()
MAX_SEND_SIZE_BYTES = 100 * 1024 * 1024
SUCCESS_FILE = os.path.join(os.getcwd(), "success.json")
_SUCCESS_LOCK = asyncio.Lock()


def _load_success_links():
    try:
        if os.path.exists(SUCCESS_FILE):
            with open(SUCCESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return set(str(x).strip() for x in data)
    except Exception:
        pass
    return set()


def _write_success_links(links):
    try:
        tmp = SUCCESS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(links), f, indent=2)
        os.replace(tmp, SUCCESS_FILE)
    except Exception as e:
        logger.warning(f"[SUCCESS.JSON] write failed: {e}")


async def is_link_done(link):
    async with _SUCCESS_LOCK:
        return str(link).strip() in _load_success_links()


async def mark_link_done(link):
    async with _SUCCESS_LOCK:
        links = _load_success_links()
        links.add(str(link).strip())
        _write_success_links(links)

_ADMIN_TXT_LOCK = asyncio.Lock()


def _admin_extract_links(content):
    links = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for u in re.findall(r"https?://\S+", line):
            u = u.rstrip(".,;)]}>\"").strip()
            if any(d in u for d in SUPPORTED_DOMAINS):
                links.append(u)
    return list(dict.fromkeys(links))


async def handle_txt_file(client, message):
    if not message.from_user or not getattr(message.document, "file_name", "") or not message.document.file_name.lower().endswith(".txt"):
        return
    from info import ADMIN_IDS, ADMIN_GROUP_ID
    admin_ids = ADMIN_IDS if isinstance(ADMIN_IDS, (list, tuple)) else [ADMIN_IDS]
    if message.from_user.id not in admin_ids:
        await client.send_message(message.chat.id, wrap_bold("<b>⛔ Admin Only</b>\n\nThis feature is restricted to bot admins."), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    if _ADMIN_TXT_LOCK.locked():
        await client.send_message(chat_id, wrap_bold("<b>⏳ Another batch is already running.</b>\n\nSend /stopbatch to cancel it."), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        return

    status_msg = await send_status_message(chat_id, "<b>⏳ Reading file...</b>")
    file_path = await message.download()
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        await update_status_message(chat_id, f"<b>❌ Failed to read file:</b> {html.escape(str(e))}")
        if file_path and os.path.exists(file_path):
            try: os.remove(file_path)
            except Exception: pass
        return
    if file_path and os.path.exists(file_path):
        try: os.remove(file_path)
        except Exception: pass

    links = _admin_extract_links(content)
    if not links:
        await update_status_message(chat_id, "<b>❌ No supported TeraBox links found.</b>")
        return

    async with _ADMIN_TXT_LOCK:
        ADMIN_BATCH_CANCEL.discard(user_id)
        target_chat = int(ADMIN_GROUP_ID)
        me = await client.get_me()
        bot_username = me.username if me and me.username else "TeraboxBot"
        user_obj = None
        try: user_obj = await client.get_users(user_id)
        except Exception: pass
        user_name = user_obj.first_name if user_obj else "Admin"

        total = len(links)
        success = fail = skipped = 0
        last_edit = 0.0

        stop_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Stop", callback_data=f"stopadmin_{user_id}")]])

        _DEFAULT_MARKUP = object()

        async def upd(text, reply_markup=_DEFAULT_MARKUP, force=False, **kwargs):
            nonlocal last_edit
            now = time.time()
            if force or now - last_edit >= 2.0:
                last_edit = now
                markup = stop_markup if reply_markup is _DEFAULT_MARKUP else reply_markup
                try:
                    await update_status_message(chat_id, text, reply_markup=markup)
                except Exception: pass

        async def banner():
            return f"<b>📄 Admin Batch</b>\n\n<b>Total Links:</b> <code>{total}</code>\n<b>Target Group:</b> <code>{target_chat}</code>\n<b>Mode:</b> Sequential (one by one)\n\n<b>✅ Sent:</b> <code>{success}</code>\n<b>❌ Failed:</b> <code>{fail}</code>\n<b>⏭️ Skipped:</b> <code>{skipped}</code>"

        await upd(await banner())

        for idx, link in enumerate(links, start=1):
            if user_id in ADMIN_BATCH_CANCEL:
                await upd(f"<b>🛑 Stopped by admin.</b>\n\n<b>✅ Sent:</b> <code>{success}</code>\n<b>❌ Failed:</b> <code>{fail}</code>\n<b>⏭️ Skipped:</b> <code>{skipped}</code>\n<b>Remaining:</b> <code>{total - idx + 1}</code>", reply_markup=None)
                return
            if await is_link_done(link):
                skipped += 1
                await upd(f"<b>⏭️ Already Sent (Index {idx}/{total})</b>\n\n<b>✅ Sent:</b> <code>{success}</code>\n<b>❌ Failed:</b> <code>{fail}</code>\n<b>⏭️ Skipped:</b> <code>{skipped}</code>\n\n<code>{link}</code>")
                logger.info(f"[ADMIN TXT BATCH] Link {idx}/{total} already in success.json | {link}")
                continue
            try:
                await upd(f"<b>📦 Processing {idx}/{total}</b>\n\n<b>✅ Sent:</b> <code>{success}</code>\n<b>❌ Failed:</b> <code>{fail}</code>\n<b>⏭️ Skipped:</b> <code>{skipped}</code>\n\n<code>{link}</code>")
                ok = await process_single_bulk_link(client, target_chat, None, link, idx, total, bot_username, speed_engine, user_id, user_name, upd, True)
                if ok is True: success += 1
                elif ok == "SKIPPED": skipped += 1
                else: fail += 1
                logger.info(f"[ADMIN TXT BATCH] Link {idx}/{total} result={ok} | {link}")
            except Exception as e:
                logger.error(f"[ADMIN TXT BATCH ERROR] Link {idx}/{total} | {link} | {e}")
                fail += 1
            if idx < total:
                await asyncio.sleep(3)

        await upd(f"<b>🎉 Batch Completed</b>\n\n<b>Total:</b> <code>{total}</code>\n<b>✅ Sent:</b> <code>{success}</code>\n<b>❌ Failed:</b> <code>{fail}</code>\n<b>⏭️ Skipped:</b> <code>{skipped}</code>", reply_markup=None)

async def handle_stop_admin_batch(client, callback_query):
    user_id = int(callback_query.data.split("_")[1])
    ADMIN_BATCH_CANCEL.add(user_id)
    try:
        await callback_query.answer("🛑 Stopping batch... It will stop after the current file finishes.", show_alert=True)
    except Exception:
        pass
    try:
        await callback_query.message.edit_text(wrap_bold("🛑 Stop requested\n\nCurrent file will finish, then the batch will stop."), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    except Exception:
        pass

async def handle_bulk_all(client, callback_query):
    user_id = callback_query.from_user.id
    if user_id not in PENDING_BULK:
        await callback_query.answer("❌ No pending bulk task found.", show_alert=True)
        return

    await callback_query.answer("Processing bulk list...")
    data = PENDING_BULK[user_id]
    valid_links = data["links"]
    msg_id = data["msg_id"]
    count = len(valid_links)
    del PENDING_BULK[user_id]

    chat_id = callback_query.message.chat.id
    batch_id = await db.add_batch(user_id, chat_id, valid_links)
    markup = get_bulk_choice_keyboard(batch_id)

    text = f"<b>✅ Queue Registered!</b>\n\n<b>Target Links:</b> <code>ALL ({count})</code>\n\n<b>Choose processing mode:</b>"
    await update_status_message(chat_id, text, reply_markup=markup)

async def handle_bulk_count(client, message):
    user_id = message.from_user.id

    if WAITING_FOR_CAPTION.get(user_id):
        raise ContinuePropagation

    if user_id not in PENDING_BULK:
        raise ContinuePropagation

    raw_text = message.text.strip()
    data = PENDING_BULK[user_id]
    valid_links = data["links"]
    msg_id = data["msg_id"]
    total_len = len(valid_links)

    start_idx = 0
    end_idx = total_len


    if data.get("step") == "start_entered":
        try:
            end_val = int(raw_text)
            start_val = data.get("start_val", 1)
            start_idx = max(0, start_val - 1)
            end_idx = min(total_len, end_val)
        except ValueError:
            await message.reply_text(wrap_bold("❌ Invalid ending range. Please send a valid number."), parse_mode=enums.ParseMode.HTML)
            return
    else:

        match_range = re.match(r"^(\d+)\s*(?:-|to|\s)\s*(\d+)$", raw_text, re.IGNORECASE)
        if match_range:
            start_val = int(match_range.group(1))
            end_val = int(match_range.group(2))
            start_idx = max(0, start_val - 1)
            end_idx = min(total_len, end_val)
        elif raw_text.isdigit():
            val = int(raw_text)

            if data.get("step") == "awaiting_range" and val < total_len and val <= 10:
                data["step"] = "start_entered"
                data["start_val"] = val
                await message.reply_text(wrap_bold(f"📌 Start Range Received: {val}\nNow send the End Range (e.g. <code>50</code> to process from {val} to 50):"), parse_mode=enums.ParseMode.HTML)
                return
            else:
                end_idx = min(total_len, val)
        else:
            raise ContinuePropagation

    if start_idx >= end_idx or start_idx >= total_len:
        await message.reply_text(wrap_bold("❌ Invalid range specified. Please try again."), parse_mode=enums.ParseMode.HTML)
        return

    selected_links = valid_links[start_idx:end_idx]
    del PENDING_BULK[user_id]

    chat_id = message.chat.id
    batch_id = await db.add_batch(user_id, chat_id, selected_links)

    try: await client.delete_messages(chat_id, message.id)
    except: pass

    markup = get_bulk_choice_keyboard(batch_id)

    text = f"<b>✅ Queue Registered!</b>\n\n<b>Target Links:</b> <code>{len(selected_links)}</code> (Range: {start_idx+1} to {end_idx})\n\n<b>Choose processing mode:</b>"
    await update_status_message(chat_id, text, reply_markup=markup)


async def handle_backbulk(client, callback_query):
    await callback_query.answer("Going back...")
    batch_id = callback_query.data.split("_")[1]
    batch = await db.get_batch(batch_id)
    if not batch: return

    markup = get_bulk_choice_keyboard(batch_id)
    count = batch["total"]

    text = f"<b>✅ Queue Registered!</b>\n\n<b>Target Links:</b> <code>{count}</code>\n\n<b>Choose processing mode:</b>"
    await update_status_message(callback_query.message.chat.id, text, reply_markup=markup)

async def run_batch_processor(client, batch_id, status_msg_id=None):
    batch = await db.get_batch(batch_id)
    if not batch: return
    chat_id, links, total, idx, success, fail = batch["chat_id"], batch["links"], batch["total"], batch["current_index"], batch["success"], batch["fail"]

    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Bulk", callback_data=f"cancelbulk_{batch_id}")]])

    if not status_msg_id:
        msg_resp = await send_status_message(chat_id, "<b>⏳ Firing engine...</b>", reply_markup=cancel_markup)
        status_msg_id = (msg_resp.get("result") or {}).get("message_id")
        try: await client.pin_chat_message(chat_id, status_msg_id)
        except Exception: pass

    me = await client.get_me()
    bot_username = me.username if me and me.username else "TeraboxBot"

    user_data = await db.get_user(batch["user_id"])
    current_time = int(time.time())
    custom_channel = user_data.get("custom_channel")
    engine_module = speed_engine

    user_obj = None
    try: user_obj = await client.get_users(batch["user_id"])
    except Exception: pass
    user_name = user_obj.first_name if user_obj else "User"

    last_edit = 0
    async def update_progress_msg(text, reply_markup=None):
        nonlocal last_edit
        now = time.time()
        if now - last_edit >= 2.0:
            try:
                await update_status_message(chat_id, text, reply_markup=reply_markup or cancel_markup)
                last_edit = now
            except Exception: pass

    try:
        while idx < total:
            current_status = (await db.get_batch(batch_id))["status"]
            if current_status == "cancelled":
                await update_status_message(chat_id, "<b>🛑 Bulk Queue Cancelled!</b>")
                try: await client.unpin_chat_message(chat_id, status_msg_id)
                except Exception: pass
                return
            elif current_status != "running":
                await update_status_message(chat_id, "<b>⏸ Paused.</b>")
                try: await client.unpin_chat_message(chat_id, status_msg_id)
                except Exception: pass
                return

            try: await update_status_message(chat_id, f"<b>📦 Queue ({idx+1}/{total})</b>\n\n<b>✅ Completed:</b> <code>{success}</code>\n<b>❌ Failed:</b> <code>{fail}</code>\n<b>Target:</b> <code>{links[idx]}</code>", reply_markup=cancel_markup)
            except Exception: pass

            if await process_single_bulk_link(client, chat_id, custom_channel, links[idx], idx+1, total, bot_username, engine_module, batch["user_id"], user_name, update_progress_msg):
                success += 1
            else:
                fail += 1

            idx += 1
            await db.update_batch_progress(batch_id, idx, success, fail)

            if idx < total:
                for c in range(4, 0, -2):
                    status_check = (await db.get_batch(batch_id))["status"]
                    if status_check == "cancelled":
                        await update_status_message(chat_id, "<b>🛑 Bulk Queue Cancelled!</b>")
                        try: await client.unpin_chat_message(chat_id, status_msg_id)
                        except Exception: pass
                        return

                    cd_text = f"<b>⏳ Cooldown Active</b>\n\nWaiting {c} seconds before processing next link...\n\n<b>Progress:</b> <code>{idx}/{total}</code> completed."
                    try: await update_status_message(chat_id, cd_text, reply_markup=cancel_markup)
                    except Exception: pass

                    await asyncio.sleep(2)

    finally:
        if (await db.get_batch(batch_id))["status"] != "cancelled":
            await db.update_batch_status(batch_id, "completed")
            final_text = f"<b>🎉 Your target has been completed!</b>\n\n<b>Total Links:</b> <code>{total}</code>\n<b>✅ Uploaded:</b> <code>{success}</code>\n<b>❌ Failed:</b> <code>{fail}</code>"
            try:
                await update_status_message(chat_id, final_text)
                await client.unpin_chat_message(chat_id, status_msg_id)
            except Exception: pass

async def handle_startbulk(client, callback_query):
    batch_id = callback_query.data.split("_")[1]
    chat_id = callback_query.message.chat.id
    msg_id = callback_query.message.id

    batch = await db.get_batch(batch_id)
    if not batch:
        await callback_query.answer("Task expired.", show_alert=True)
        return

    if batch.get("status") == "running":
        await callback_query.answer("Queue is already running!", show_alert=True)
        return

    await callback_query.answer("Initializing engine...")
    await db.update_batch_status(batch_id, "running")
    try: await client.pin_chat_message(chat_id, msg_id)
    except Exception: pass

    asyncio.create_task(run_batch_processor(client, batch_id, msg_id))

async def handle_cancel_bulk(client, callback_query):
    await callback_query.answer("Cancelling bulk queue... It will stop shortly.", show_alert=True)
    batch_id = callback_query.data.split("_")[1]
    await db.update_batch_status(batch_id, "cancelled")

async def handle_resume_callback(client, callback_query):
    await callback_query.answer("Resuming queue...")
    batch_id = callback_query.data.split("_")[1]
    batch = await db.get_batch(batch_id)
    if not batch or batch.get("status") in ["completed", "running", "cancelled"]:
        await callback_query.answer("Cannot resume this task.", show_alert=True)
        return

    await db.update_batch_status(batch_id, "running")
    chat_id = callback_query.message.chat.id
    msg_id = callback_query.message.id

    try: await client.pin_chat_message(chat_id, msg_id)
    except Exception: pass

    asyncio.create_task(run_batch_processor(client, batch_id, msg_id))

async def handle_terabox_link(client, message):

    url_match = re.search(get_domain_pattern(), message.text)
    if not url_match:
        logger.info("[SINGLE LINK] No supported Terabox link pattern found in text.")
        return

    url_text = url_match.group(1)
    chat_id = message.chat.id
    user_id = message.from_user.id
    logger.info(f"[SINGLE LINK INITIATED] User: {user_id} | Chat: {chat_id} | URL: {url_text}")


    await db.save_user_link(user_id, url_text)


    cached_file_id, cached_caption = await db.get_file_cache(url_text)
    if cached_file_id:
        user_data = await db.get_user(chat_id)
        custom_channel = user_data.get("custom_channel")
        target_chat = custom_channel if custom_channel else chat_id

        try:
            sent_msg = await client.send_cached_media(
                chat_id=target_chat,
                file_id=cached_file_id,
                caption=cached_caption or "<b>⚡ Instant File Delivery (Cached)</b>",
                parse_mode=enums.ParseMode.HTML
            )
            if target_chat == chat_id:
                warn_text = f"<b>⚠️ Auto-Delete Warning</b>\n\nYour cached file will be deleted in 30 minutes."
                warn_msg = await client.send_message(chat_id, wrap_bold(warn_text), parse_mode=enums.ParseMode.HTML)
                asyncio.create_task(autodell.auto_delete_task(client, chat_id, sent_msg.id, warn_msg.id, "Cached File", 1800))
            return
        except Exception as e:
            logger.warning(f"[CACHE DELIVERY FAILED] {str(e)}. Proceeding to fresh download.")

    status_msg = await update_status_message(chat_id, "<b>⏳ Fetching file metadata from server...</b>")
    status_id = status_msg.get("result", {}).get("message_id")


    terabox_data = []
    max_retries = 3

    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    logger.info(f"[API RETRY] Attempt {attempt}/{max_retries}")

                from domain import fetch_terabox_links
                terabox_data = await fetch_terabox_links(session, url_text, timeout=30)
                if terabox_data:
                    logger.info(f"[SINGLE LINK RESOLVED] Retrieved {len(terabox_data)} item(s)")
                    break

                if attempt < max_retries:
                    await asyncio.sleep(attempt)
                    continue
                await update_status_message(chat_id, "<b>⚠️ Download Server Temporarily Down</b>\n\nOur servers are currently undergoing maintenance or experiencing heavy load. Please try again in a few minutes.")
                logger.info("[SINGLE LINK] All fetch attempts failed. Aborting.")
                return

            except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
                logger.warning(f"[API RETRY LOG] Attempt {attempt}/{max_retries} failed with error: {str(e)}")
                if attempt < max_retries:
                    await asyncio.sleep(attempt)
                else:
                    logger.error(f"[SINGLE LINK FETCH ERROR] Exhausted {max_retries} retries: {str(e)}", exc_info=True)
                    await update_status_message(chat_id, "<b>⚠️ Download Server Temporarily Down</b>\n\nOur servers are currently undergoing maintenance or experiencing heavy load. Please try again in a few minutes.")
                    logger.info("[SINGLE LINK] Exception caught during API fetch retries. Aborting.")
                    return

    item = terabox_data[0]
    me = await client.get_me()
    bot_username = me.username if me and me.username else "TeraboxBot"

    raw_file_name = f"{re.sub(r'(_|-)?@[a-zA-Z0-9_]+', '', item.get('file_name', 'Terabox_File')).strip()} @{bot_username}"
    download_url = item.get("download_url", "")
    stream_url = item.get("stream_final_url", "")
    thumbnail = item.get("thumbnail", "")

    if not download_url and not stream_url:
        await update_status_message(chat_id, "<b>❌ Unable to retrieve valid download URL.</b>")
        logger.info("[SINGLE LINK] Both download_url and stream_url are empty. Aborting.")
        return

    user_data = await db.get_user(chat_id)
    current_time = int(time.time())

    stream_id = str(int(time.time())) + "_" + str(user_id)[-4:]
    if stream_url:
        STREAM_STORE[stream_id] = stream_url

    detected_domain = globals().get("DETECTED_DOMAIN", None)
    web_stream_link = None
    if detected_domain and isinstance(detected_domain, str) and detected_domain.lower() != "none":
        clean_domain = detected_domain.rstrip('/')
        if clean_domain.startswith("http://") or clean_domain.startswith("https://"):
            web_stream_link = f"{clean_domain}/stream/{stream_id}"
        else:
            web_stream_link = f"https://{clean_domain}/stream/{stream_id}"

    task_id = f"{chat_id}_{int(time.time())}"
    TASK_STORE[task_id] = {
        "user_id": user_id,
        "chat_id": chat_id,
        "items": terabox_data,
        "bot_username": bot_username,
        "stream_id": stream_id,
        "started": False,
        "cancelled": False,
        "msg_id": status_id
    }
    logger.info(f"[SINGLE LINK TASK STORED] Task ID: {task_id} | Stored keys: {list(TASK_STORE[task_id].keys())}")

    text = (
        f"<b>📂 File Found Successfully!</b>\n\n"
        f"<b>📄 Title:</b> <code>{html.escape(raw_file_name)}</code>\n"
        f"<b>💾 Size:</b> <code>{html.escape(str(item.get('file_size', 'Unknown')))}</code>\n\n"
        f"<i>Select your preferred download mode below:</i>"
    )

    buttons = []
    buttons.append([InlineKeyboardButton("⚡ Ultra Flash Download (16x Speed)", callback_data=f"vip_dl_{task_id}")])

    if stream_url and web_stream_link:
        buttons.append([InlineKeyboardButton("🌐 Direct Web Stream", url=web_stream_link)])

    markup = InlineKeyboardMarkup(buttons)
    logger.info(f"[KEYBOARD] {markup}")

    try:
        await update_status_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        logger.error(f"[SINGLE LINK EDIT ERROR] Status update failed: {str(e)}", exc_info=True)

    logger.info(f"[SINGLE LINK COMPLETED] Options presented to user {user_id} for Task ID: {task_id}")

async def handle_cancel_callback(client, callback_query):
    await callback_query.answer("Cancelling task...", show_alert=True)
    task_id = callback_query.data.split("_")[2]
    if task_id in TASK_STORE: TASK_STORE[task_id]["cancelled"] = True

async def handle_back_callback(client, callback_query):
    task_id = callback_query.data.split("_")[2]
    task_data = TASK_STORE.get(task_id)
    if not task_data:
        await callback_query.answer("Session Expired!", show_alert=True)
        return

    current_origin = get_automated_base_url()
    stream_url_rendered = f"{current_origin}/stream/{task_data['stream_id']}"
    await callback_query.answer("Launching web player routing pipeline...")

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Launch Web Player", url=stream_url_rendered)], [InlineKeyboardButton("⬅️ Back", callback_data=f"back_dl_{task_id}")]])
    try:
        await callback_query.message.edit_text(wrap_bold("<b>🎬 Your Streaming Node Wrapper Frame Link is Ready!</b>\n\nClick below to trigger video playback natively in your browser environment."), reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    except MessageNotModified:
        pass

async def handle_download_callback(client, callback_query):
    logger.info(f"[DOWNLOAD CALLBACK] {callback_query.data}")
    data = callback_query.data
    logger.info(f"[DOWNLOAD CALLBACK RECEIVED] Raw Callback Data: {data} | User: {callback_query.from_user.id}")

    if data.startswith("vip_dl_"):
        task_id = data[7:]
        mode = "vip"
    elif data.startswith("free_dl_"):
        task_id = data[8:]
        mode = "free"
    else:
        logger.info(f"[DOWNLOAD CALLBACK] Unknown callback data pattern: {data}. Aborting.")
        return

    task = TASK_STORE.get(task_id)
    if not task:
        logger.info(f"[DOWNLOAD CALLBACK] Task ID {task_id} not found in TASK_STORE. Alerting user.")
        await callback_query.answer("⚠️ Session expired or task not found. Please send the link again.", show_alert=True)
        return

    if task.get("started"):
        logger.info(f"[DOWNLOAD CALLBACK] Task ID {task_id} already started. Alerting user.")
        await callback_query.answer("⚡ Download is already in progress...", show_alert=True)
        return

    task["started"] = True
    await callback_query.answer("Initializing download pipeline...")
    logger.info(f"[DOWNLOAD CALLBACK] Task ID {task_id} marked as started. Mode: {mode}")

    chat_id = task.get("chat_id")
    user_id = task.get("user_id")
    items = task.get("items", [])
    bot_username = task.get("bot_username", "TeraboxBot")
    msg_id = task.get("msg_id") or callback_query.message.id

    if not items:
        logger.info(f"[DOWNLOAD CALLBACK] Task ID {task_id} contains no items. Aborting.")
        await update_status_message(chat_id, "<b>❌ No downloadable item found in session.</b>")
        return

    item = items[0]
    raw_file_name = f"{re.sub(r'(_|-)?@[a-zA-Z0-9_]+', '', item.get('file_name', 'Terabox_File')).strip()} @{bot_username}"
    download_url = item.get("download_url", "")
    stream_url = item.get("stream_final_url", "")

    user_data = await db.get_user(user_id)
    current_time = int(time.time())
    custom_channel = user_data.get("custom_channel")
    user_name = callback_query.from_user.first_name or "User"

    engine_module = speed_engine
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Download", callback_data=f"cancel_dl_{task_id}")]])

    safe_base = "".join(c for c in os.path.splitext(raw_file_name)[0] if c.isalnum() or c in " ._-@") or "single_file"
    current_ext = ".mp4" if stream_url else (os.path.splitext(raw_file_name)[1] or item.get("extension", "") or ".zip")
    api_duration_sec = parse_api_duration(item.get("duration", ""))

    local_file_path = os.path.join(os.getcwd(), f"file_{task_id}_{safe_base}{current_ext}")
    temp_dir = os.path.join(os.getcwd(), f"temp_{task_id}")
    video_duration = api_duration_sec

    last_edit = 0
    async def update_progress_msg(text, reply_markup=None):
        nonlocal last_edit
        now = time.time()
        if now - last_edit >= 2.0:
            try:
                await update_status_message(chat_id, text, reply_markup=reply_markup or cancel_markup)
                last_edit = now
            except Exception:
                pass

    start_time_total = time.time()
    logger.info(f"[DOWNLOAD STARTING] Task ID: {task_id} | Local Path: {local_file_path} | Temp Dir: {temp_dir}")

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(headers=headers) as session:
            downloaded_ok = False
            if download_url:
                logger.info("[DOWNLOAD EXECUTION] Running direct full-size download first.")
                try:
                    await engine_module.execute_standard_premium_download(
                        task_id, session, download_url, headers, local_file_path, temp_dir, TASK_STORE, update_progress_msg, cancel_markup, 1, 1
                    )
                    downloaded_ok = True
                except Exception as e:
                    logger.error(f"[DIRECT DOWNLOAD FAILED] {str(e)}. Falling back to stream.")
                    downloaded_ok = False
                    if os.path.exists(local_file_path):
                        os.remove(local_file_path)
                    shutil.rmtree(temp_dir, ignore_errors=True)

            if not downloaded_ok and stream_url:
                logger.info(f"[DOWNLOAD EXECUTION] Running stream download via {engine_module.__name__}")
                success, fetched_duration = await engine_module.execute_premium_download(
                    task_id, session, stream_url, local_file_path, temp_dir, TASK_STORE, update_progress_msg, cancel_markup, 1, 1
                )
                if success:
                    stream_url, video_duration = "SUCCESS", fetched_duration
                    logger.info(f"[DOWNLOAD EXECUTION] Stream download successful (fallback). Duration: {fetched_duration}s")
                else:
                    stream_url = ""
            if zipfile.is_zipfile(local_file_path) or local_file_path.lower().endswith('.zip'):
                logger.info("[DOWNLOAD ZIP EXTR] File is a zip archive. Extracting largest content...")
                try:
                    with zipfile.ZipFile(local_file_path, 'r') as z:
                        largest_file = max(z.infolist(), key=lambda x: x.file_size)
                        z.extract(largest_file, path=os.getcwd())
                        final_clean_name = f"{re.sub(r'(_|-)?@[a-zA-Z0-9_]+', '', os.path.splitext(largest_file.filename)[0]).strip()} @{bot_username}{os.path.splitext(largest_file.filename)[1]}"
                        final_path = os.path.join(os.getcwd(), final_clean_name)
                        if os.path.exists(final_path): os.remove(final_path)
                        os.rename(os.path.join(os.getcwd(), largest_file.filename), final_path)
                        os.remove(local_file_path)
                        local_file_path = final_path
                        logger.info(f"[DOWNLOAD ZIP EXTR SUCCESS] Extracted file path: {local_file_path}")
                except Exception as exc:
                    logger.error(f"[DOWNLOAD ZIP EXTR ERROR] {str(exc)}", exc_info=True)


                if zipfile.is_zipfile(local_file_path) or local_file_path.lower().endswith('.zip'):
                    logger.info(f"[DOWNLOAD ZIP EXTR] File is a zip archive. Extracting largest content...")
                    try:
                        with zipfile.ZipFile(local_file_path, 'r') as z:
                            largest_file = max(z.infolist(), key=lambda x: x.file_size)
                            z.extract(largest_file, path=os.getcwd())
                            final_clean_name = f"{re.sub(r'(_|-)?@[a-zA-Z0-9_]+', '', os.path.splitext(largest_file.filename)[0]).strip()} @{bot_username}{os.path.splitext(largest_file.filename)[1]}"
                            final_path = os.path.join(os.getcwd(), final_clean_name)
                            if os.path.exists(final_path): os.remove(final_path)
                            os.rename(os.path.join(os.getcwd(), largest_file.filename), final_path)
                            os.remove(local_file_path)
                            local_file_path = final_path
                            logger.info(f"[DOWNLOAD ZIP EXTR SUCCESS] Extracted file path: {local_file_path}")
                    except Exception as e:
                        logger.error(f"[DOWNLOAD ZIP EXTR ERROR] {str(e)}", exc_info=True)

        if TASK_STORE.get(task_id, {}).get("cancelled"):
            logger.info(f"[DOWNLOAD CANCELLED] Task ID {task_id} was cancelled during download.")
            await update_status_message(chat_id, "<b>🛑 Download Cancelled by User.</b>")
            if os.path.exists(local_file_path): os.remove(local_file_path)
            shutil.rmtree(temp_dir, ignore_errors=True)
            TASK_STORE.pop(task_id, None)
            return

        real_filename_to_show = os.path.basename(local_file_path)
        is_video = re.sub(r'\s+@[A-Za-z0-9_]+$', '', local_file_path.lower()).endswith(('.mp4', '.mkv', '.webm', '.avi'))

        downloaded_thumb_path = None
        if item.get("thumbnail", ""):
            try:
                downloaded_thumb_path = f"thumb_{task_id}.jpg"
                async with aiohttp.ClientSession() as thumb_session:
                    async with thumb_session.get(item.get("thumbnail", "")) as resp:
                        if resp.status == 200:
                            with open(downloaded_thumb_path, 'wb') as f: f.write(await resp.read())
                            logger.info(f"[THUMBNAIL DOWNLOAD SUCCESS] Thumbnail path: {downloaded_thumb_path}")
            except Exception as e:
                logger.error(f"[THUMBNAIL DOWNLOAD ERROR] {str(e)}")
                downloaded_thumb_path = None

        logger.info(f"[UPLOAD STARTING] Task ID: {task_id} | File: {real_filename_to_show} | Is Video: {is_video}")
        last_up_time = time.time()
        last_up_text = ""
        start_time_upload = time.time()

        async def upload_cb(current, total):
            nonlocal last_up_time, last_up_text
            now = time.time()
            if now - last_up_time >= 2.0 or current == total:
                text = prog.generate_premium_progress("🚀 Uploading to Telegram...", current, total, start_time_upload, 1, 1)
                if text != last_up_text:
                    await update_progress_msg(text)
                    last_up_text = text
                    last_up_time = now

        target_chat = custom_channel if custom_channel else chat_id
        caption_text = f"<b>{real_filename_to_show}</b>"

        if custom_channel:
            try:
                await client.get_chat(custom_channel)
            except Exception as e:
                logger.error(f"[UPLOAD ROUTING ERROR] Custom channel invalid: {str(e)}")
                await client.send_message(user_id, wrap_bold(f"<b>❌ Custom Channel Error</b>\n\nCould not access channel. Make sure I am Admin!\nFalling back to PM."), parse_mode=enums.ParseMode.HTML)
                target_chat = chat_id

        sent_msg = None
        from pyrogram.errors import FloodWait
        try:
            if is_video:
                logger.info(f"[SEND_VIDEO EXECUTING] Target Chat: {target_chat}")
                sent_msg = await client.send_video(
                    chat_id=target_chat, video=local_file_path, supports_streaming=True, caption=caption_text,
                    file_name=real_filename_to_show, duration=video_duration, thumb=downloaded_thumb_path,
                    parse_mode=enums.ParseMode.HTML, progress=upload_cb
                )
            else:
                logger.info(f"[SEND_DOCUMENT EXECUTING] Target Chat: {target_chat}")
                sent_msg = await client.send_document(
                    chat_id=target_chat, document=local_file_path, caption=caption_text,
                    file_name=real_filename_to_show, thumb=downloaded_thumb_path,
                    parse_mode=enums.ParseMode.HTML, progress=upload_cb
                )
        except FloodWait as fw:
            logger.info(f"[UPLOAD FLOODWAIT] Sleeping {fw.value + 5} seconds...")
            await asyncio.sleep(fw.value + 5)
            if is_video:
                sent_msg = await client.send_video(
                    chat_id=target_chat, video=local_file_path, supports_streaming=True, caption=caption_text,
                    file_name=real_filename_to_show, duration=video_duration, thumb=downloaded_thumb_path,
                    parse_mode=enums.ParseMode.HTML, progress=upload_cb
                )
            else:
                sent_msg = await client.send_document(
                    chat_id=target_chat, document=local_file_path, caption=caption_text,
                    file_name=real_filename_to_show, thumb=downloaded_thumb_path,
                    parse_mode=enums.ParseMode.HTML, progress=upload_cb
                )
        except Exception as upload_err:
            logger.error(f"[UPLOAD PRIMARY ERROR] {str(upload_err)}. Attempting PM fallback...")
            if target_chat != chat_id:
                target_chat = chat_id
                if is_video:
                    sent_msg = await client.send_video(
                        chat_id=target_chat, video=local_file_path, supports_streaming=True, caption=caption_text,
                        file_name=real_filename_to_show, duration=video_duration, thumb=downloaded_thumb_path,
                        parse_mode=enums.ParseMode.HTML, progress=upload_cb
                    )
                else:
                    sent_msg = await client.send_document(
                        chat_id=target_chat, document=local_file_path, caption=caption_text,
                        file_name=real_filename_to_show, thumb=downloaded_thumb_path,
                        parse_mode=enums.ParseMode.HTML, progress=upload_cb
                    )

        if sent_msg:
            logger.info(f"[TASK COMPLETED SUCCESS] Task ID: {task_id} | Sent Message ID: {sent_msg.id}")


            file_obj = sent_msg.video or sent_msg.document
            if file_obj and hasattr(file_obj, "file_id"):
                for it in items:
                    u_url = it.get("download_url", "")
                    if u_url:
                        await db.save_file_cache(u_url, file_obj.file_id, caption_text)
                _cached_u = locals().get("url_text")
                if _cached_u:
                    await db.save_file_cache(_cached_u, file_obj.file_id, caption_text)

            try:
                await client.delete_messages(chat_id, msg_id)
            except Exception:
                pass
            _STATUS_MSGS.pop(chat_id, None)

            if target_chat == chat_id:
                warn_text = f"<b>⚠️ Auto-Delete Warning</b>\n\nYour file <code>{real_filename_to_show}</code> will be deleted in 30 minutes.\nSave it or use <code>/set_ch -100xxxxxxxxxx</code>."
                warn_msg = await client.send_message(chat_id, wrap_bold(warn_text), parse_mode=enums.ParseMode.HTML)
                asyncio.create_task(autodell.auto_delete_task(client, chat_id, sent_msg.id, warn_msg.id, real_filename_to_show, 1800))

            total_elapsed = time.time() - start_time_total
            file_size = os.path.getsize(local_file_path) if os.path.exists(local_file_path) else 0
            avg_speed = file_size / total_elapsed if total_elapsed > 0 else 0
            plan_status = "⚡ Ultra Fast"

            log_caption = (
                f"<b>📦 File Processed Successfully!</b>\n\n"
                f"<b>👤 User:</b> <a href='tg://user?id={user_id}'>{user_name}</a>\n"
                f"<b>🆔 ID:</b> <code>{user_id}</code>\n"
                f"<b>💎 Plan:</b> {plan_status}\n\n"
                f"<b>📄 File:</b> <code>{real_filename_to_show}</code>\n"
                f"<b>💾 Size:</b> <code>{prog.format_b(file_size)}</code>\n"
                f"<b>⏱️ Time:</b> <code>{prog.format_time(total_elapsed)}</code>\n"
                f"<b>🚀 Avg Speed:</b> <code>{prog.format_b(avg_speed)}/s</code>"
            )
            await log.forward_to_logs(target_chat, sent_msg.id, log_caption)


            cooldown_seconds = 7
            await asyncio.sleep(cooldown_seconds)


    except Exception as general_err:
        logger.error(f"[DOWNLOAD/UPLOAD PIPELINE FATAL ERROR] Task ID: {task_id} | Error: {str(general_err)}", exc_info=True)
        try:
            await update_status_message(chat_id, f"<b>❌ An error occurred during download/upload processing:</b>\n<code>{html.escape(str(general_err))}</code>")
        except Exception:
            pass
    finally:
        if os.path.exists(local_file_path):
            os.remove(local_file_path)
        if downloaded_thumb_path and os.path.exists(downloaded_thumb_path):
            os.remove(downloaded_thumb_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        TASK_STORE.pop(task_id, None)
        logger.info(f"[TASK CLEANUP FINISHED] Task ID: {task_id} purged from TASK_STORE.")

async def process_single_bulk_link(client, chat_id, custom_channel, link, file_idx, total_files, bot_username, engine_module, user_id, user_name, update_progress_callback, no_caption=False):
    try:
        if await is_link_done(link):
            logger.info(f"[BULK SKIP-DONE] Link already in success.json | {link}")
            return "SKIPPED"
        from pyrogram.errors import FloodWait
        start_time_total = time.time()
        logger.info(f"[BULK SINGLE LINK PROCESSING] Index: {file_idx}/{total_files} | User: {user_id} | Link: {link}")


        await db.save_user_link(user_id, link)


        cached_file_id, cached_caption = await db.get_file_cache(link)
        target_chat = custom_channel if custom_channel else chat_id

        if cached_file_id and not no_caption:
            try:
                sent_msg = await client.send_cached_media(
                    chat_id=target_chat,
                    file_id=cached_file_id,
                    caption=("" if no_caption else (cached_caption or f"<b>⚡ Instant File Delivery (Cached Batch #{file_idx})</b>")),
                    parse_mode=enums.ParseMode.HTML
                )
                if target_chat == chat_id and not no_caption:
                    warn_text = f"<b>⚠️ Auto-Delete Warning</b>\n\nYour cached file will be deleted in 30 minutes."
                    warn_msg = await client.send_message(chat_id, wrap_bold(warn_text), parse_mode=enums.ParseMode.HTML)
                    asyncio.create_task(autodell.auto_delete_task(client, chat_id, sent_msg.id, warn_msg.id, "Cached File", 1800))
                await mark_link_done(link)
                return True
            except Exception as e:
                logger.warning(f"[BULK CACHE DELIVERY FAILED] Index {file_idx}: {str(e)}. Fallback to download.")

        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            from domain import fetch_terabox_links, parse_size_to_bytes, format_bytes, fetch_head_size
            terabox_data = await fetch_terabox_links(session, link, timeout=30)

        logger.info(f"[BULK PARSED SUCCESS TIER] Index: {file_idx} | Extracted Content: {str(terabox_data)}")

        if not terabox_data:
            _note = FETCH_FAIL_NOTES.get(link, "no notes")
            logger.warning(f"[BULK PARSE FAILED] Index: {file_idx} | Link: {link} | Empty list from API | reasons: {_note}")
            return False
        logger.info(f"[BULK PARSED OK] Index: {file_idx} | name={str(terabox_data[0].get('file_name', ''))[:60]} | size={terabox_data[0].get('file_size', '')}")
        item = terabox_data[0]
        size_bytes = None
        try:
            _md_name = str(item.get("file_name", "") or "Unknown")
            _md_size = str(item.get("file_size", "") or "").strip()
            if not _md_size or _md_size.upper() in ("UNKNOWN", "N/A", "-"):
                try:
                    if size_bytes is None and item.get("download_url"):
                        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as _hs:
                            size_bytes = await fetch_head_size(_hs, item.get("download_url"))
                except Exception:
                    size_bytes = None
                _md_size = format_bytes(size_bytes) if size_bytes is not None else "Unknown"
            _md_q = str(item.get("quality", "") or "N/A")
            _md_dur = str(item.get("duration", "") or "N/A")
            _md_text = (
                f"<b>📦 Processing {file_idx}/{total_files}</b>\n\n"
                f"<b>📄 Name:</b> <code>{html.escape(_md_name)}</code>\n"
                f"<b>💾 Size:</b> <code>{html.escape(_md_size)}</code>\n"
                f"<b>🎞 Quality:</b> <code>{html.escape(_md_q)}</code>\n"
                f"<b>⏱️ Duration:</b> <code>{html.escape(_md_dur)}</code>\n\n"
                f"<b>⬇️ Downloading...</b>"
            )
            try:
                await update_progress_callback(_md_text, force=True)
            except TypeError:
                await update_progress_callback(_md_text)
        except Exception:
            pass

        size_bytes = item.get("size_bytes")
        if size_bytes is None:
            size_bytes = parse_size_to_bytes(item.get("file_size", ""))
        if size_bytes is not None and size_bytes >= MAX_SEND_SIZE_BYTES:
            _skip_name = str(item.get("file_name", "") or "Unknown")
            _skip_size = str(item.get("file_size", "") or "Unknown")
            _skip_msg = (
                "<b>⏭️ Skipped (≥100 MB)</b>\n\n"
                f"<b>📄 Name:</b> <code>{html.escape(_skip_name)}</code>\n"
                f"<b>💾 Size:</b> <code>{html.escape(_skip_size)}</code>\n\n"
                "<b>👉 Moving to the next link...</b>"
            )
            try:
                await update_progress_callback(_skip_msg, force=True)
            except TypeError:
                await update_progress_callback(_skip_msg)
            return "SKIPPED"

        raw_file_name = f"{re.sub(r'(_|-)?@[a-zA-Z0-9_]+', '', item.get('file_name', f'Bulk_{file_idx}')).strip()} @{bot_username}"
        download_url, stream_url = item.get("download_url", ""), item.get("stream_final_url", "")
        if not download_url and not stream_url: return False

        safe_base = "".join(c for c in os.path.splitext(raw_file_name)[0] if c.isalnum() or c in " ._-@") or f"bulk_{file_idx}"
        current_ext = os.path.splitext(re.sub(r"\s+@[A-Za-z0-9_]+$", "", raw_file_name))[1] or item.get("extension", "") or (".mp4" if stream_url else ".zip")
        api_duration_sec = parse_api_duration(item.get("duration", ""))
        local_file_path, temp_dir, TASK_STORE, video_duration = os.path.join(os.getcwd(), f"bulk_{file_idx}_{safe_base}{current_ext}"), os.path.join(os.getcwd(), f"temp_bulk_{file_idx}"), {}, api_duration_sec

        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            downloaded_ok = False
            if download_url:
                try:
                    await engine_module.execute_standard_premium_download("dummy", session, download_url, {"User-Agent": "Mozilla/5.0"}, local_file_path, temp_dir, TASK_STORE, update_progress_callback, None, file_idx, total_files)
                    downloaded_ok = True
                except Exception as e:
                    logger.error(f"[BULK DIRECT DOWNLOAD FAILED] Index: {file_idx} | Error: {str(e)}")
                    downloaded_ok = False
            if not downloaded_ok and stream_url:
                success, fetched_duration = await engine_module.execute_premium_download("dummy", session, stream_url, local_file_path, temp_dir, TASK_STORE, update_progress_callback, None, file_idx, total_files)
                if success: stream_url, video_duration = "SUCCESS", fetched_duration
                else: stream_url = ""
            if zipfile.is_zipfile(local_file_path) or local_file_path.lower().endswith('.zip'):
                try:
                    with zipfile.ZipFile(local_file_path, 'r') as z:
                        largest_file = max(z.infolist(), key=lambda x: x.file_size)
                        z.extract(largest_file, path=os.getcwd())
                        final_clean_name = f"{re.sub(r'(_|-)?@[a-zA-Z0-9_]+', '', os.path.splitext(largest_file.filename)[0]).strip()} @{bot_username}{os.path.splitext(largest_file.filename)[1]}"
                        final_path = os.path.join(os.getcwd(), final_clean_name)
                        if os.path.exists(final_path): os.remove(final_path)
                        os.rename(os.path.join(os.getcwd(), largest_file.filename), final_path)
                        os.remove(local_file_path)
                        local_file_path = final_path
                except Exception as e:
                    logger.error(f"[BULK ZIP EXTRACTION EXCEPTION] Index: {file_idx} | Error: {str(e)}")
        real_filename_to_show = os.path.basename(local_file_path)
        _pfx = f"bulk_{file_idx}_"
        if real_filename_to_show.startswith(_pfx):
            real_filename_to_show = real_filename_to_show[len(_pfx):]
        is_video = re.sub(r'\s+@[A-Za-z0-9_]+$', '', local_file_path.lower()).endswith(('.mp4', '.mkv', '.webm', '.avi'))
        downloaded_thumb_path = None
        if item.get("thumbnail", ""):
            try:
                downloaded_thumb_path = f"bulk_thumb_{file_idx}.jpg"
                async with aiohttp.ClientSession() as thumb_session:
                    async with thumb_session.get(item.get("thumbnail", "")) as resp:
                        if resp.status == 200:
                            with open(downloaded_thumb_path, 'wb') as f: f.write(await resp.read())
            except Exception as e:
                logger.error(f"[BULK THUMBNAIL DOWNLOAD EXCEPTION] Index: {file_idx} | Error: {str(e)}")
                downloaded_thumb_path = None

        last_up_time = time.time()
        last_up_text = ""
        start_time_upload = time.time()

        async def upload_cb(current, total):
            nonlocal last_up_time, last_up_text
            now = time.time()
            if now - last_up_time >= 2.0 or current == total:
                text = prog.generate_premium_progress("🚀 Uploading...", current, total, start_time_upload, file_idx, total_files)
                if text != last_up_text:
                    await update_progress_callback(text)
                    last_up_text = text
                    last_up_time = now

        target_chat = custom_channel if custom_channel else chat_id
        caption_text = "" if no_caption else f"<b>{real_filename_to_show}</b>"

        if custom_channel:
            try:
                await client.get_chat(custom_channel)
            except Exception as e:
                await client.send_message(user_id, wrap_bold(f"<b>❌ Bulk Channel Connection Error</b>\n\nBot could not find your channel. Make sure I am an Admin!\nError: <code>{str(e)}</code>"), parse_mode=enums.ParseMode.HTML)
                target_chat = chat_id

        try:
            sent_msg = None
            try:
                if is_video: sent_msg = await client.send_video(chat_id=target_chat, video=local_file_path, supports_streaming=True, caption=caption_text, file_name=real_filename_to_show, duration=video_duration, thumb=downloaded_thumb_path, parse_mode=enums.ParseMode.HTML, progress=upload_cb)
                else: sent_msg = await client.send_document(chat_id=target_chat, document=local_file_path, caption=caption_text, file_name=real_filename_to_show, thumb=downloaded_thumb_path, parse_mode=enums.ParseMode.HTML, progress=upload_cb)
            except FloodWait as e:
                await asyncio.sleep(e.value + 5)
                if is_video: sent_msg = await client.send_video(chat_id=target_chat, video=local_file_path, supports_streaming=True, caption=caption_text, file_name=real_filename_to_show, duration=video_duration, thumb=downloaded_thumb_path, parse_mode=enums.ParseMode.HTML, progress=upload_cb)
                else: sent_msg = await client.send_document(chat_id=target_chat, document=local_file_path, caption=caption_text, file_name=real_filename_to_show, thumb=downloaded_thumb_path, parse_mode=enums.ParseMode.HTML, progress=upload_cb)
            except Exception as e:
                if target_chat != chat_id:
                    await client.send_message(user_id, wrap_bold(f"<b>⚠️ Bulk Routing Failed</b>\n\nCould not upload to channel. Falling back to PM.\nReason: <code>{str(e)}</code>"), parse_mode=enums.ParseMode.HTML)
                target_chat = chat_id
                try:
                    if is_video: sent_msg = await client.send_video(chat_id=target_chat, video=local_file_path, supports_streaming=True, caption=caption_text, file_name=real_filename_to_show, duration=video_duration, thumb=downloaded_thumb_path, parse_mode=enums.ParseMode.HTML, progress=upload_cb)
                    else: sent_msg = await client.send_document(chat_id=target_chat, document=local_file_path, caption=caption_text, file_name=real_filename_to_show, thumb=downloaded_thumb_path, parse_mode=enums.ParseMode.HTML, progress=upload_cb)
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 5)
                    if is_video: sent_msg = await client.send_video(chat_id=target_chat, video=local_file_path, supports_streaming=True, caption=caption_text, file_name=real_filename_to_show, duration=video_duration, thumb=downloaded_thumb_path, parse_mode=enums.ParseMode.HTML, progress=upload_cb)
                    else: sent_msg = await client.send_document(chat_id=target_chat, document=local_file_path, caption=caption_text, file_name=real_filename_to_show, thumb=downloaded_thumb_path, parse_mode=enums.ParseMode.HTML, progress=upload_cb)

            # Clean storage: remove thumbnail right after upload
            if downloaded_thumb_path and os.path.exists(downloaded_thumb_path):
                try: os.remove(downloaded_thumb_path)
                except Exception: pass

            if sent_msg:
                file_obj = sent_msg.video or sent_msg.document
                if file_obj and hasattr(file_obj, "file_id"):
                    await db.save_file_cache(link, file_obj.file_id, caption_text)
                    if download_url:
                        await db.save_file_cache(download_url, file_obj.file_id, caption_text)
                    await mark_link_done(link)

            if target_chat == chat_id and sent_msg and not no_caption:
                warn_text = f"<b>⚠️ Auto-Delete Warning</b>\n\nYour file <code>{real_filename_to_show}</code> will be deleted in 30 minutes.\nSave it or use <code>/set_ch -100xxxxxxxxxx</code>."
                warn_msg = await client.send_message(chat_id, wrap_bold(warn_text), parse_mode=enums.ParseMode.HTML)
                asyncio.create_task(autodell.auto_delete_task(client, chat_id, sent_msg.id, warn_msg.id, real_filename_to_show, 1800))

            if sent_msg:
                total_elapsed = time.time() - start_time_total
                file_size = os.path.getsize(local_file_path) if os.path.exists(local_file_path) else 0
                avg_speed = file_size / total_elapsed if total_elapsed > 0 else 0
                plan_status = "⚡ Ultra Fast"

                log_caption = (
                    f"<b>📦 Bulk File Processed!</b>\n\n"
                    f"<b>👤 User:</b> <a href='tg://user?id={user_id}'>{user_name}</a>\n"
                    f"<b>🆔 ID:</b> <code>{user_id}</code>\n"
                    f"<b>💎 Plan:</b> {plan_status}\n\n"
                    f"<b>📄 File:</b> <code>{real_filename_to_show}</code>\n"
                    f"<b>💾 Size:</b> <code>{prog.format_b(file_size)}</code>\n"
                    f"<b>⏱️ Time:</b> <code>{prog.format_time(total_elapsed)}</code>\n"
                    f"<b>🚀 Avg Speed:</b> <code>{prog.format_b(avg_speed)}/s</code>"
                )
                await log.forward_to_logs(target_chat, sent_msg.id, log_caption)

        except Exception as upload_err:
            logger.error(f"[BULK UPLOAD FATAL ERROR] Index: {file_idx} | Exception: {str(upload_err)}")

        if os.path.exists(local_file_path): os.remove(local_file_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return True
    except Exception as general_err:
        logger.error(f"[BULK PIPELINE TRACE ERROR] Index: {file_idx} | Error: {str(general_err)}", exc_info=True)
        if 'temp_dir' in locals(): shutil.rmtree(temp_dir, ignore_errors=True)
        if 'local_file_path' in locals() and os.path.exists(local_file_path): os.remove(local_file_path)
        return False
