import asyncio
import logging
import os
import shutil
import time
import urllib.parse
import aiohttp
from samraplugin import prog

logger = logging.getLogger(__name__)

try:
    from info import PREMIUM_THREADS
except ImportError:
    PREMIUM_THREADS = 16


class M3U8DownloadError(Exception):

    pass


async def fetch_m3u8_data(session, playlist_url):











    try:
        parsed_original_url = urllib.parse.urlparse(playlist_url)
        original_query = parsed_original_url.query

        logger.info(f"[M3U8 FETCHING MANIFEST] Requesting playlist URL: {playlist_url}")
        async with session.get(playlist_url) as response:
            if response.status != 200:
                logger.error(f"[M3U8 MANIFEST FETCH FAILED] Status: {response.status} | URL: {playlist_url}")
                return [], 0
            content = await response.text()
            base_url = str(response.url)

        logger.info(f"[M3U8 FULL MANIFEST CONTENT START] URL: {base_url}\n{content}\n[M3U8 FULL MANIFEST CONTENT END]")

        lines = content.strip().split('\n')
        segments = []
        total_duration = 0.0
        next_line_is_playlist = False

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith('#EXT-X-STREAM-INF:'):
                next_line_is_playlist = True
                continue

            if next_line_is_playlist and not line.startswith('#'):
                master_url = urllib.parse.urljoin(base_url, line)
                if original_query and original_query not in master_url:
                    separator = '&' if '?' in master_url else '?'
                    master_url = f"{master_url}{separator}{original_query}"
                logger.info(f"[M3U8 MASTER PLAYLIST REDIRECT] Following variant playlist to: {master_url}")
                return await fetch_m3u8_data(session, master_url)

            if line.startswith('#EXTINF:'):
                try:
                    duration_str = line.replace('#EXTINF:', '').split(',')[0]
                    total_duration += float(duration_str)
                except ValueError:
                    pass
            elif not line.startswith('#'):

                resolved_segment_url = urllib.parse.urljoin(base_url, line)


                if original_query:
                    parsed_seg = urllib.parse.urlparse(resolved_segment_url)
                    if not parsed_seg.query:
                        separator = '&' if '?' in resolved_segment_url else '?'
                        resolved_segment_url = f"{resolved_segment_url}{separator}{original_query}"

                segments.append(resolved_segment_url)

        logger.info(f"[M3U8 RESOLVED SEGMENTS COUNT] Total: {len(segments)} | Estimated Duration: {int(total_duration)}s")
        for idx, seg_u in enumerate(segments):
            logger.info(f"[RESOLVED SEGMENT URL #{idx+1}] {seg_u}")

        return segments, int(total_duration)
    except Exception as e:
        logger.error(f"[M3U8 MANIFEST EXCEPTION] Error: {str(e)}", exc_info=True)
        return [], 0


async def _merge_ts_with_ffmpeg(concat_file_path, local_file_path):




    ffmpeg_cmd = shutil.which("ffmpeg")
    if not ffmpeg_cmd:
        logger.warning("[FFMPEG NOT FOUND] ffmpeg binary is not installed in the system PATH.")
        return False

    cmd = [
        ffmpeg_cmd,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file_path,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        local_file_path
    ]

    logger.info(f"[FFMPEG MERGE STARTED] Command: {' '.join(cmd)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()

        if proc.returncode == 0 and os.path.exists(local_file_path) and os.path.getsize(local_file_path) > 0:
            logger.info(f"[FFMPEG MERGE COMPLETED] Output: {local_file_path} ({os.path.getsize(local_file_path)} bytes)")
            return True
        else:
            logger.error(f"[FFMPEG MERGE FAILED] Exit code: {proc.returncode} | Stderr: {stderr.decode(errors='ignore')}")
            return False
    except Exception as e:
        logger.error(f"[FFMPEG EXCEPTION] {str(e)}", exc_info=True)
        return False


async def execute_premium_download(
    task_id, session, stream_url, local_file_path, temp_dir, TASK_STORE,
    update_progress_callback, cancel_markup, file_idx, total_files
):
    logger.info(f"[SPEED ENGINE STARTED] Task: {task_id} | Defaulting to 480p Stream: {stream_url}")

    segments, duration = await fetch_m3u8_data(session, stream_url)
    if not segments:
        raise M3U8DownloadError(f"Failed to extract M3U8 segments from playlist: {stream_url}")

    total_segments = len(segments)
    os.makedirs(temp_dir, exist_ok=True)

    downloaded_segments = 0
    downloaded_bytes = 0
    start_time = time.time()
    last_update = time.time()
    last_text = ""

    sem = asyncio.Semaphore(PREMIUM_THREADS)

    async def download_segment(index, seg_url):
        nonlocal downloaded_segments, downloaded_bytes, last_update, last_text
        if TASK_STORE.get(task_id, {}).get("cancelled"):
            return False

        seg_path = os.path.join(temp_dir, f"seg_{index:05d}.ts")

        for attempt in range(1, 4):
            if TASK_STORE.get(task_id, {}).get("cancelled"):
                return False

            try:
                async with sem:
                    timeout = aiohttp.ClientTimeout(total=30)
                    async with session.get(seg_url, timeout=timeout) as resp:
                        if resp.status in [200, 206]:
                            chunk_data = await resp.read()
                            if not chunk_data:
                                logger.error(f"[ZERO BYTE SEGMENT] Segment #{index+1} returned 0 bytes")
                                raise M3U8DownloadError(f"Segment #{index+1} returned zero bytes.")

                            with open(seg_path, 'wb') as f:
                                f.write(chunk_data)

                            downloaded_bytes += len(chunk_data)
                            downloaded_segments += 1

                            now = time.time()
                            if now - last_update >= 3.0:
                                text = prog.generate_premium_progress(
                                    "⚡ Downloading 480p Ultra Flash Stream",
                                    downloaded_bytes, 0, start_time, file_idx, total_files,
                                    True, total_segments, downloaded_segments
                                )
                                if text != last_text:
                                    if update_progress_callback:
                                        await update_progress_callback(text, reply_markup=cancel_markup)
                                    last_text = text
                                    last_update = now
                            return True
            except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as err:
                logger.warning(f"[CHUNK RETRY] Segment #{index+1} attempt {attempt}/3 error: {str(err)}")

            if attempt < 3:
                await asyncio.sleep(attempt)

        logger.error(f"[CHUNK FATAL FAILURE] Segment #{index+1} failed all attempts.")
        return False

    tasks = [download_segment(i, url) for i, url in enumerate(segments)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    if TASK_STORE.get(task_id, {}).get("cancelled"):
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(local_file_path):
            os.remove(local_file_path)
        raise M3U8DownloadError("Download task was explicitly cancelled by the user.")

    failed_indices = [i for i, res in enumerate(results) if res is not True]
    if failed_indices:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(local_file_path):
            os.remove(local_file_path)
        raise M3U8DownloadError(f"Failed to download {len(failed_indices)} out of {total_segments} TS chunks.")

    logger.info(f"[CHUNKS COMPLETE] All {total_segments} segments downloaded successfully. Size: {downloaded_bytes} bytes.")
    if update_progress_callback:
        await update_progress_callback("<b>⏳ Merging stream segments into MP4...</b>", reply_markup=cancel_markup)

    concat_file_path = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_file_path, "w", encoding="utf-8") as f:
        for i in range(total_segments):
            f.write(f"file 'seg_{i:05d}.ts'\n")

    merged_via_ffmpeg = await _merge_ts_with_ffmpeg(concat_file_path, local_file_path)

    if not merged_via_ffmpeg:
        logger.info("[FALLBACK DIRECT CONCAT] Concatenating TS files directly.")
        with open(local_file_path, 'wb') as outfile:
            for i in range(total_segments):
                seg_path = os.path.join(temp_dir, f"seg_{i:05d}.ts")
                if os.path.exists(seg_path):
                    with open(seg_path, 'rb') as infile:
                        outfile.write(infile.read())

    if not os.path.exists(local_file_path) or os.path.getsize(local_file_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(local_file_path):
            os.remove(local_file_path)
        raise M3U8DownloadError("Failed to assemble video file.")

    logger.info(f"[DOWNLOAD PIPELINE SUCCESS] Output File: {local_file_path} | Size: {os.path.getsize(local_file_path)} bytes")
    shutil.rmtree(temp_dir, ignore_errors=True)
    return True, duration


async def execute_standard_premium_download(
    task_id, session, download_url, headers, local_file_path, temp_dir, TASK_STORE,
    update_progress_callback, cancel_markup, file_idx, total_files
):



    logger.info(f"[STANDARD DOWNLOAD STARTED] Task: {task_id} | URL: {download_url}")
    total_size = 0
    try:
        async with session.head(download_url, headers=headers, allow_redirects=True) as resp:
            total_size = int(resp.headers.get('Content-Length', 0))
    except Exception as e:
        logger.warning(f"[HEAD REQUEST FAILED] Could not determine Content-Length: {str(e)}")

    if total_size < 1048576 or total_size == 0:
        logger.info("[SINGLE THREAD FALLBACK] Target size too small or unknown. Downloading in single thread.")
        async with session.get(download_url, headers=headers) as resp:
            if resp.status not in [200, 206]:
                raise M3U8DownloadError(f"Direct download returned HTTP status {resp.status}")

            downloaded = 0
            start_time = time.time()
            last_update = time.time()
            last_text = ""

            with open(local_file_path, 'wb') as f:
                async for chunk in resp.content.iter_chunked(4194304):
                    if TASK_STORE.get(task_id, {}).get("cancelled"):
                        if os.path.exists(local_file_path):
                            os.remove(local_file_path)
                        raise M3U8DownloadError("Task was cancelled by user.")
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_update >= 3.0:
                        text = prog.generate_premium_progress("⬇️ Downloading file", downloaded, total_size, start_time, file_idx, total_files)
                        if text != last_text:
                            if update_progress_callback:
                                await update_progress_callback(text, reply_markup=cancel_markup)
                            last_text = text
                            last_update = now
        return True

    os.makedirs(temp_dir, exist_ok=True)
    chunk_size = total_size // PREMIUM_THREADS
    ranges = []
    for i in range(PREMIUM_THREADS):
        start = i * chunk_size
        end = start + chunk_size - 1 if i < PREMIUM_THREADS - 1 else total_size - 1
        ranges.append((i, start, end))

    downloaded_bytes = 0
    start_time = time.time()
    last_update = time.time()
    last_text = ""
    sem = asyncio.Semaphore(PREMIUM_THREADS)

    async def download_part(index, start, end):
        nonlocal downloaded_bytes, last_update, last_text
        part_path = os.path.join(temp_dir, f"part_{index:03d}.dat")
        part_headers = {**headers, "Range": f"bytes={start}-{end}"}

        for attempt in range(1, 4):
            if TASK_STORE.get(task_id, {}).get("cancelled"):
                return False
            try:
                async with sem:
                    timeout = aiohttp.ClientTimeout(total=60)
                    async with session.get(download_url, headers=part_headers, timeout=timeout) as resp:
                        if resp.status in [200, 206]:
                            with open(part_path, 'wb') as f:
                                async for chunk in resp.content.iter_chunked(4194304):
                                    if TASK_STORE.get(task_id, {}).get("cancelled"):
                                        return False
                                    f.write(chunk)
                                    downloaded_bytes += len(chunk)
                                    now = time.time()
                                    if now - last_update >= 3.0:
                                        text = prog.generate_premium_progress("⬇️ Downloading file", downloaded_bytes, total_size, start_time, file_idx, total_files)
                                        if text != last_text:
                                            if update_progress_callback:
                                                await update_progress_callback(text, reply_markup=cancel_markup)
                                            last_text = text
                                            last_update = now
                            return True
            except Exception as e:
                logger.warning(f"[PART RETRY] Part {index} attempt {attempt}/3 error: {str(e)}")

            if attempt < 3:
                await asyncio.sleep(attempt)
        return False

    tasks = [download_part(i, s, e) for i, s, e in ranges]
    results = await asyncio.gather(*tasks)

    if TASK_STORE.get(task_id, {}).get("cancelled"):
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(local_file_path):
            os.remove(local_file_path)
        raise M3U8DownloadError("Download task was cancelled by user.")

    if not all(results):
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(local_file_path):
            os.remove(local_file_path)
        raise M3U8DownloadError("Failed to download standard file chunks after maximum retry attempts.")

    if update_progress_callback:
        await update_progress_callback("<b>⏳ Merging parts into final file...</b>", reply_markup=cancel_markup)

    with open(local_file_path, 'wb') as outfile:
        for i in range(PREMIUM_THREADS):
            part_path = os.path.join(temp_dir, f"part_{i:03d}.dat")
            if os.path.exists(part_path):
                with open(part_path, 'rb') as infile:
                    outfile.write(infile.read())

    shutil.rmtree(temp_dir, ignore_errors=True)
    return True