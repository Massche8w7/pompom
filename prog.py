import time

def format_b(size_in_bytes):
    size = float(size_in_bytes)
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}

    while size >= power and n < 4:
        size /= power
        n += 1

    return f"{size:.2f} {power_labels[n]}"

def format_time(seconds):
    if seconds < 0:
        return "Calculating..."

    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)

    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    else:
        return f"{s}s"

def generate_premium_progress(
    action_title,
    current_bytes,
    total_bytes,
    start_time,
    file_idx=1,
    total_files=1,
    is_m3u8=False,
    total_segments=0,
    downloaded_segments=0
):
    now = time.time()
    elapsed = now - start_time

    speed = current_bytes / elapsed if elapsed > 0 else 0

    if is_m3u8 and total_segments > 0 and downloaded_segments > 0:
        estimated_total = (current_bytes / downloaded_segments) * total_segments
        total_to_show = estimated_total
        percentage = (downloaded_segments / total_segments) * 100
    else:
        total_to_show = total_bytes
        percentage = (current_bytes / total_bytes) * 100 if total_bytes > 0 else 0

    percentage = min(100.0, max(0.0, percentage))

    filled_length = int(percentage / 10)

    progress_chars = ["▰", "▱"]
    filled = progress_chars[0] * filled_length
    empty = progress_chars[1] * (10 - filled_length)

    bar = filled + empty

    eta = (
        (total_to_show - current_bytes) / speed
        if speed > 0 and total_to_show > current_bytes
        else 0
    )

    file_counter = (
        f"📂 <b>FILE:</b> <code>{file_idx}/{total_files}</code>\n"
        if total_files > 1 else ""
    )

    text = (
        f"<b>🚀 {action_title.upper()} 💎</b>\n\n"

        f"{file_counter}"

        f"━━━━━━━━━━━━━━━━━━\n\n"

        f"📊 <b>PROGRESS STATUS</b>\n"
        f"<code>{bar} {percentage:.1f}%</code>\n\n"

        f"📦 <b>DOWNLOADED SIZE</b>\n"
        f"<code>{format_b(current_bytes)}</code>\n\n"

        f"🗂 <b>TOTAL FILE SIZE</b>\n"
        f"<code>{format_b(total_to_show) if total_to_show > 0 else 'Unknown Size'}</code>\n\n"

        f"⚡ <b>LIVE SPEED</b>\n"
        f"<code>{format_b(speed)}/s</code>\n\n"

        f"⏳ <b>ESTIMATED TIME LEFT</b>\n"
        f"<code>{format_time(eta)}</code>\n\n"

        f"🕒 <b>ELAPSED TIME</b>\n"
        f"<code>{format_time(elapsed)}</code>\n\n"

        f"━━━━━━━━━━━━━━━━━━\n\n"

        f"🔥 <b>ULTRA HIGH-SPEED ENGINE ACTIVE</b>\n"
        f"🛡 Stable • Secure • Ultra Fast"
    )

    return text
