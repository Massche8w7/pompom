from pyrogram import Client, filters, enums
from database import db
from command import wrap_bold

WAITING_FOR_CAPTION = {}

@Client.on_message(filters.command("caption") & filters.private & filters.incoming)
async def set_caption_init(client, message):
    user_id = message.from_user.id
    WAITING_FOR_CAPTION[user_id] = True

    guideline_text = (
        "Send your new caption.\n\n"
        "Examples:\n"
        "Movie Name (2026)\n"
        "or\n"
        "Movie Name\n"
        "1080p WEB-DL\n"
        "or\n"
        "Join @MyChannel\n"
        "or\n"
        "🎬 Movie Name\n"
        "📁 1080p HDRip\n"
        "❤️ Enjoy!\n\n"
        "Now send your caption."
    )
    await message.reply_text(wrap_bold(guideline_text), parse_mode=enums.ParseMode.HTML)

@Client.on_message(filters.command("delcaption") & filters.private & filters.incoming)
async def delete_caption_command(client, message):
    user_id = message.from_user.id
    await db.set_custom_caption(user_id, None)
    if user_id in WAITING_FOR_CAPTION:
        del WAITING_FOR_CAPTION[user_id]
    await message.reply_text(wrap_bold("🗑️ Custom Caption Reset Successfully!\n\nYour profile has restored default framework configurations."), parse_mode=enums.ParseMode.HTML)

@Client.on_message(filters.private & filters.incoming, group=-1)
async def capture_caption_input(client, message):
    user_id = message.from_user.id
    if not WAITING_FOR_CAPTION.get(user_id):
        return

    if message.text and message.text.startswith('/'):
        return

    del WAITING_FOR_CAPTION[user_id]

    if message.text:
        saved_value = message.text
    elif message.caption:
        saved_value = message.caption
    else:
        await message.reply_text(wrap_bold("❌ Invalid structure node payload!\n\nPlease supply a textual formatting message configuration string framework."), parse_mode=enums.ParseMode.HTML)
        return

    await db.set_custom_caption(user_id, saved_value)
    await message.reply_text(wrap_bold("✅ Custom Caption Configured Successfully!\n\nAll subsequent upload processing segments will dynamically append this matrix structure node context metadata wrapper natively."), parse_mode=enums.ParseMode.HTML)