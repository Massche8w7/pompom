import logging
import os
import re
import time
import asyncio
import aiohttp
import urllib.parse
import zipfile
import shutil
from pyrogram import Client, filters, enums, ContinuePropagation
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import db
from domain import SUPPORTED_DOMAINS
from domain import fetch_terabox_links
from command import send_status_message, update_status_message, wrap_bold
from samraplugin import speed_engine, prog, log, autodell
logger = logging.getLogger(__name__)
PENDING_BULK = {}

def get_domain_pattern():
    escaped_domains = [re.escape(d) for d in SUPPORTED_DOMAINS]
    return rf"(https?://\S*({'|'.join(escaped_domains)})\S*)"

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

async def check_interrupted_batches(client):
    running_batches = await db.get_running_batches()
    for batch in running_batches:
        await db.update_batch_status(batch["_id"], "paused")
        text = f"<b>⚠️ Restart Found</b>\n<b>Links:</b> <code>{batch['total']}</code> | <b>Done:</b> <code>{batch['current_index']}</code>\nClick below."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Resume Queue", callback_data=f"resumebatch_{batch['_id']}")]])
        await client.send_message(batch["chat_id"], wrap_bold(text), reply_markup=markup, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)

async def handle_txt_file(client, message):
    if not message.document.file_name.lower().endswith('.txt'): return

    chat_id = message.chat.id
    user_data = await db.get_user(chat_id)
    current_time = int(time.time())

    processing_msg = await send_status_message(chat_id, "<b>⏳ Scanning and validating links...</b>")
    file_path = await message.download()

    try:
        with open(file_path, 'r', encoding='utf-8') as f: content = f.read()
    except Exception:
        await update_status_message(chat_id, "<b>❌ Failed to read text file.</b>")
        if os.path.exists(file_path): os.remove(file_path)
        return

    if os.path.exists(file_path): os.remove(file_path)

    all_links = list(dict.fromkeys(re.findall(r"(https?://[^\s]+)", content)))
    valid_links = []
    invalid_links = []

    for link in all_links:
        if any(domain in link for domain in SUPPORTED_DOMAINS):
            valid_links.append(link)
        else:
            invalid_links.append(link)

    if invalid_links:
        inv_text = "<b>⚠️ Unsupported Domains Found:</b>\n\n"
        for inv in invalid_links[:20]:
            inv_text += f"➔ <code>{inv}</code>\n"
        if len(invalid_links) > 20:
            inv_text += f"...and {len(invalid_links) - 20} more.\n"
        inv_text += "\n<i>📌 Upar di gayi unsupported link list aap admin ko send krdo, wo in domain ko bot me add kr denge.</i>"
        await client.send_message(chat_id, wrap_bold(inv_text), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)

    if not valid_links:
        await update_status_message(chat_id, "<b>❌ No supported links found in the file.</b>")
        return

    PENDING_BULK[message.from_user.id] = {
        "links": valid_links,
        "msg_id": processing_msg.id
    }

    text = f"<b>📄 File Scan Completed!</b>\n\n<b>✅ Supported Links:</b> <code>{len(valid_links)}</code>\n<b>❌ Unsupported Links:</b> <code>{len(invalid_links)}</code>\n\n"
    text += "<b>🔢 How many links do you want to upload?</b>\n<i>Reply with a number (e.g. 20) or click ALL.</i>"

    markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"🚀 Process ALL ({len(valid_links)})", callback_data="bulk_all")]])
    await update_status_message(chat_id, text, reply_markup=markup)

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

    if user_id not in PENDING_BULK:
        raise ContinuePropagation
    if not message.text.isdigit():
        raise ContinuePropagation

    count = int(message.text.strip())
    data = PENDING_BULK[user_id]
    valid_links = data["links"]
    msg_id = data["msg_id"]

    if count <= 0:
        await message.reply_text(wrap_bold("<b>❌ Please enter a valid number greater than 0.</b>"), parse_mode=enums.ParseMode.HTML)
        return

    if count > len(valid_links):
        count = len(valid_links)

    selected_links = valid_links[:count]
    del PENDING_BULK[user_id]

    chat_id = message.chat.id
    batch_id = await db.add_batch(user_id, chat_id, selected_links)

    try: await client.delete_messages(chat_id, message.id)
    except: pass

    markup = get_bulk_choice_keyboard(batch_id)

    text = f"<b>✅ Queue Registered!</b>\n\n<b>Target Links:</b> <code>{count}</code>\n\n<b>Choose processing mode:</b>"
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

async def process_single_bulk_link(client, chat_id, custom_channel, link, file_idx, total_files, bot_username, engine_module, user_id, user_name, update_progress_callback):
    try:
        from pyrogram.errors import FloodWait
        start_time_total = time.time()
        logger.info(f"[BULK SINGLE LINK PROCESSING] Index: {file_idx}/{total_files} | User: {user_id} | Link: {link}")

        headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        payload = {"url": link}

        async with aiohttp.ClientSession(headers=headers) as session:
            terabox_data = await fetch_terabox_links(session, link, timeout=30)

            logger.info(f"[BULK API RAW DATA] Index: {file_idx} | Items: {len(terabox_data)}")


        logger.info(f"[BULK PARSED SUCCESS TIER] Index: {file_idx} | Extracted Content: {str(terabox_data)}")

        if not terabox_data: return False
        item = terabox_data[0]

        raw_file_name = f"{re.sub(r'(_|-)?@[a-zA-Z0-9_]+', '', item.get('file_name', f'Bulk_{file_idx}')).strip()} @{bot_username}"
        download_url, stream_url = item.get("download_url", ""), item.get("stream_final_url", "")
        if not download_url and not stream_url: return False

        safe_base = "".join(c for c in os.path.splitext(raw_file_name)[0] if c.isalnum() or c in " ._-@") or f"bulk_{file_idx}"
        current_ext = os.path.splitext(re.sub(r"\s+@[A-Za-z0-9_]+$", "", raw_file_name))[1] or item.get("extension", "") or (".mp4" if stream_url else ".zip")
        api_duration_sec = parse_api_duration(item.get("duration", ""))
        local_file_path, temp_dir, TASK_STORE, video_duration = os.path.join(os.getcwd(), f"bulk_{file_idx}_{safe_base}{current_ext}"), os.path.join(os.getcwd(), f"temp_bulk_{file_idx}"), {}, api_duration_sec

        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            if stream_url:
                success, fetched_duration = await engine_module.execute_premium_download("dummy", session, stream_url, local_file_path, temp_dir, TASK_STORE, update_progress_callback, None, file_idx, total_files)
                if success: stream_url, video_duration = "SUCCESS", fetched_duration
                else: stream_url = ""
            if not stream_url:
                await engine_module.execute_standard_premium_download("dummy", session, download_url, {"User-Agent": "Mozilla/5.0"}, local_file_path, temp_dir, TASK_STORE, update_progress_callback, None, file_idx, total_files)
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
        caption_text = f"<b>{real_filename_to_show}</b>"

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

            if target_chat == chat_id and sent_msg:
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
