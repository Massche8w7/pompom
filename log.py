from pyrogram import enums
from command import wrap_bold

try:
    from info import LOG_CHANNEL_ID
except ImportError:
    LOG_CHANNEL_ID = 0

_CLIENT = None


def set_client(client):
    global _CLIENT
    _CLIENT = client


async def forward_to_logs(from_chat_id, message_id, caption=None):
    if not LOG_CHANNEL_ID:
        return
    client = _CLIENT
    if client is None:
        return
    try:
        kwargs = dict(
            chat_id=LOG_CHANNEL_ID,
            from_chat_id=from_chat_id,
            message_id=message_id
        )
        if caption:
            kwargs["caption"] = wrap_bold(caption)
            kwargs["parse_mode"] = enums.ParseMode.HTML
        await client.copy_message(**kwargs)
    except Exception:
        pass
