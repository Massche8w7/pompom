import sys
import os
import asyncio
import logging
import pyrogram
import pyrogram.utils
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from aiohttp import web
from info import API_ID, API_HASH, BOT_TOKEN
from database import db
import command
import pmfilter
from samraplugin import stats, menucmd, caption, log

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s")
logger = logging.getLogger(__name__)

pyrogram.utils.MIN_CHAT_ID = -999999999999
pyrogram.utils.MIN_CHANNEL_ID = -1009999999999

app = Client(
    "terabox_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=dict(root="samraplugin")
)
command.set_client(app)
log.set_client(app)

AUTH_BLOCK_TEXT = "User Unauthorized 🥀"

async def global_auth_gate(client, message):
    """Only ADMIN_IDS may use the bot; everyone else gets blocked."""
    try:
        from info import ADMIN_IDS
    except Exception:
        ADMIN_IDS = []
    u = message.from_user
    if u and u.id in ADMIN_IDS:
        return
    try:
        await message.reply(command.wrap_bold(AUTH_BLOCK_TEXT), parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass
    raise StopPropagation

async def global_auth_gate_callback(client, callback_query):
    try:
        from info import ADMIN_IDS
    except Exception:
        ADMIN_IDS = []
    u = callback_query.from_user
    if u and u.id in ADMIN_IDS:
        return
    try:
        await callback_query.answer(AUTH_BLOCK_TEXT, show_alert=True)
    except Exception:
        pass
    raise StopPropagation

# Global gate (group -1 runs before every other handler)
app.add_handler(MessageHandler(global_auth_gate, filters.private & filters.incoming), group=-1)
app.add_handler(CallbackQueryHandler(global_auth_gate_callback, filters.all), group=-1)

async def cleanup_orphan_files():
    import os
    import shutil
    for item in os.listdir(os.getcwd()):
        if item.startswith("temp_") and os.path.isdir(item):
            shutil.rmtree(item, ignore_errors=True)
        elif (item.startswith("bulk_") or item.startswith("thumb_") or item.startswith("file_")) and os.path.isfile(item):
            try:
                os.remove(item)
            except Exception:
                pass

async def periodic_storage_cleaner():
    """Clean storage: periodically remove leftover thumb/temp/bulk files older than 30 minutes."""
    import time as _t
    import shutil as _sh
    while True:
        try:
            now = _t.time()
            for item in os.listdir(os.getcwd()):
                fp = os.path.join(os.getcwd(), item)
                try:
                    if os.path.isfile(fp) and (item.startswith("bulk_thumb_") or item.startswith("thumb_") or item.startswith("bulk_") or item.startswith("file_")):
                        if now - os.path.getmtime(fp) > 1800:
                            os.remove(fp)
                    elif item.startswith("temp_") and os.path.isdir(fp):
                        if now - os.path.getmtime(fp) > 1800:
                            _sh.rmtree(fp, ignore_errors=True)
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(600)

app.add_handler(MessageHandler(command.start_command, filters.command("start") & filters.private & filters.incoming))
app.add_handler(MessageHandler(command.txt_command, filters.command("txt") & filters.private & filters.incoming))
app.add_handler(MessageHandler(command.set_ch_command, filters.command("set_ch") & filters.private & filters.incoming))
app.add_handler(MessageHandler(command.restart_command, filters.command("restart")))
app.add_handler(MessageHandler(stats.stats_command, filters.command("stats") & filters.private & filters.incoming))
app.add_handler(MessageHandler(command.stopbatch_command, filters.command("stopbatch") & filters.private & filters.incoming))
app.add_handler(CallbackQueryHandler(pmfilter.handle_download_callback, filters.regex(r"^(vip_dl_|free_dl_)")))

app.add_handler(MessageHandler(caption.set_caption_init, filters.command("caption") & filters.private & filters.incoming))
app.add_handler(MessageHandler(caption.delete_caption_command, filters.command("delcaption") & filters.private & filters.incoming))
app.add_handler(MessageHandler(caption.capture_caption_input, filters.private & filters.incoming), group=-1)


app.add_handler(MessageHandler(pmfilter.handle_bulk_count, filters.text & filters.private & filters.incoming))
app.add_handler(MessageHandler(pmfilter.handle_terabox_link, filters.text & filters.private & filters.incoming))

app.add_handler(CallbackQueryHandler(pmfilter.handle_startbulk, filters.regex(r"^startbulk_")))
app.add_handler(CallbackQueryHandler(pmfilter.handle_backbulk, filters.regex(r"^backbulk_")))

app.add_handler(CallbackQueryHandler(pmfilter.handle_cancel_bulk, filters.regex(r"^cancelbulk_")))
app.add_handler(CallbackQueryHandler(pmfilter.handle_stop_admin_batch, filters.regex(r"^stopadmin_")))
app.add_handler(CallbackQueryHandler(pmfilter.handle_bulk_all, filters.regex(r"^bulk_all$")))
app.add_handler(CallbackQueryHandler(pmfilter.handle_download_callback, filters.regex(r"^(vip_dl_|free_dl_)")))
app.add_handler(CallbackQueryHandler(pmfilter.handle_back_callback, filters.regex(r"^back_dl_")))
app.add_handler(CallbackQueryHandler(pmfilter.handle_cancel_callback, filters.regex(r"^cancel_dl_")))
app.add_handler(CallbackQueryHandler(stats.show_stats_callback, filters.regex(r"^about_stats_btn")))

app.add_handler(MessageHandler(pmfilter.handle_txt_file, filters.document & filters.private & filters.incoming))
app.add_handler(CallbackQueryHandler(pmfilter.handle_resume_callback, filters.regex(r"^resumebatch_")))

async def handle_stream_page(request):
    logger.info("Stream extraction gateway path hit")
    try:
        current_origin = f"{request.scheme}://{request.host}"
        pmfilter.DETECTED_DOMAIN = current_origin

        stream_id = request.match_info['stream_id']
        from pmfilter import STREAM_STORE
        real_link = STREAM_STORE.get(stream_id)
        if not real_link:
            return web.Response(text="<html><body style='background:#000000;color:#fff;text-align:center;padding:50px;'><h2>STREAM SESSION EXPIRED PLEASE CONTINUE WITH NEW SESSION.</h2></body></html>", content_type='text/html', status=404)
        me = await app.get_me()
        bot_name = me.first_name if me and me.first_name else "TERABOX BOT"
        from playtemp import get_player_html
        html_content = get_player_html(real_link, bot_name)
        return web.Response(text=html_content, content_type='text/html')
    except Exception as e:
        logger.error(f"Forensic stream rendering crash: {str(e)}", exc_info=True)
        return web.Response(text="<html><body><h2>INTERNAL STREAM PIPELINE ERROR.</h2></body></html>", content_type='text/html', status=500)

async def start_web_server():
    server = web.Application()
    server.router.add_get('/stream/{stream_id}', handle_stream_page)
    runner = web.AppRunner(server)
    await runner.setup()
    base_port = int(os.environ.get("PORT", 8080))
    for port in range(base_port, base_port + 20):
        try:
            site = web.TCPSite(runner, '0.0.0.0', port)
            await site.start()
            pmfilter.DETECTED_DOMAIN = f"http://127.0.0.1:{port}"
            logger.info(f"Internal backend application engine mounted on port {port}.")
            return
        except OSError:
            logger.warning(f"Port {port} already in use, trying next port...")
    logger.error("No free port found for web server.")

async def startup_notification(client):
    try:
        from info import SUPRT_MSG_REST_ID
        if SUPRT_MSG_REST_ID:
            text = command.wrap_bold("✅ Bot Started!")
            await client.send_message(SUPRT_MSG_REST_ID, command.wrap_bold(text), parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass

if __name__ == "__main__":
    app.start()
    app.loop.create_task(cleanup_orphan_files())
    app.loop.create_task(startup_notification(app))
    app.loop.create_task(pmfilter.check_interrupted_batches(app))
    app.loop.create_task(periodic_storage_cleaner())
    app.loop.create_task(menucmd.set_bot_commands(app))
    app.loop.create_task(start_web_server())
    pyrogram.idle()
    app.stop()
