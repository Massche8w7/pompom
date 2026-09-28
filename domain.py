import aiohttp
import asyncio
import os
import hashlib
import json
import logging
import random
import re
import time
import urllib.parse

logger = logging.getLogger("domain")
logger.setLevel(logging.INFO)

SUPPORTED_DOMAINS = [
    "terabox.com",
    "teraboxshare.com",
    "teraboxapp.com",
    "terabox.app",
    "mirrobox.com",
    "nephobox.com",
    "freeterabox.com",
    "1024tera.com",
    "4funbox.com",
    "1024terabox.com",
    "terasharelink.com",
    "teraboxurl.com",
    "terafileshare.com",
    "teraboxlink.com",
    "terasharefile.com",
    "teraboxurll.in"
]

def format_bytes(num):
    if num is None:
        return "Unknown"
    try:
        num = int(num)
    except Exception:
        return "Unknown"
    if num < 0:
        return "Unknown"
    if num < 1024:
        return f"{num} B"
    value = float(num)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        value /= 1024.0
        if value < 1024 or unit == "PB":
            if unit == "KB":
                return f"{value:.1f} {unit}"
            return f"{value:.2f} {unit}"
    return "Unknown"


async def fetch_head_size(session, url, timeout=20):
    try:
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as resp:
            if resp.status == 200:
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit():
                    return int(cl)
    except Exception:
        pass
    return None


def parse_size_to_bytes(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except Exception:
            return None
    s = str(value).strip().upper()
    if not s or s in ("UNKNOWN", "N/A", "-"):
        return None
    m = re.search(r"([\d.]+)\s*(B|KB|MB|GB|TB)", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}[unit]
    return int(num * mult)


def _extract_quality_numeric(quality_str):
    match = re.search(r'(\d+)', str(quality_str or ''))
    return int(match.group(1)) if match else 0


# Quality label -> tier for stream-selection. "original"/source master ranks above
# any transcode; unknown/unlabeled entries rank lowest but are kept as fallback.
_QUALITY_ALIASES = {
    "4k": 4096, "2160p": 2160, "qhd": 1440, "2k": 1440, "1440p": 1440,
    "fhd": 1080, "1080p": 1080, "fullhd": 1080, "hd": 720, "720p": 720,
    "sd": 480, "480p": 480, "360p": 360,
}
_QUALITY_SOURCE_RANK = 100000


def _quality_rank(label):
    """Return a comparable rank for a stream quality label (higher = better)."""
    s = str(label or "").strip().lower().replace(" ", "")
    if not s:
        return -1
    if s in ("original", "source", "master", "dlink"):
        return _QUALITY_SOURCE_RANK
    if s in _QUALITY_ALIASES:
        return _QUALITY_ALIASES[s]
    m = re.search(r"(\d{3,4})\s*p?", s)
    if m:
        return int(m.group(1))
    return 0


def _pick_best_stream(fs):
    """Pick the highest-tier stream URL from a {label: url} map.

    Tiers: original/source > 4K > 2K/1440p > 1080p/FHD > 720p/HD > 480p/SD > 360p
    > unknown. Empty values are skipped; missing top tiers fall through to the
    next available one; a plain string URL is returned as-is.
    """
    if not isinstance(fs, dict):
        return str(fs or "")
    best_url = ""
    best_rank = -1
    for label, url in fs.items():
        u = str(url or "")
        if not u:
            continue
        r = _quality_rank(label)
        if r > best_rank:
            best_rank = r
            best_url = u
    return best_url

async def check_url_working(session, url):
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with session.head(url, timeout=timeout, allow_redirects=True) as resp:
            if resp.status in [200, 206]:
                return True
    except Exception:
        pass
    try:
        headers = {"Range": "bytes=0-1"}
        async with session.get(url, headers=headers, timeout=timeout, allow_redirects=True) as resp:
            if resp.status in [200, 206]:
                return True
    except Exception:
        pass
    return False

async def parse_terabox_response(data, session=None):
    try:
        if not isinstance(data, dict) or not data.get("ok"):
            return []

        data_obj = data.get("data", {})
        title = data_obj.get("title", "Terabox_Video")
        thumbnail = data_obj.get("thumbnail", "")
        _raw_size_num = data_obj.get("size")
        if not isinstance(_raw_size_num, (int, float)):
            _raw_size_num = None
        file_size = str(data_obj.get("size_formatted") or "").strip()
        if not file_size or file_size.upper() in ("UNKNOWN", "N/A", "-"):
            file_size = format_bytes(_raw_size_num) if _raw_size_num is not None else "Unknown"
        duration_str = data_obj.get("duration_formatted") or ""

        media = data_obj.get("media", {})
        videos = media.get("videos", [])

        if not videos:
            return []


        def priority_score(video):
            q = str(video.get("quality", "")).lower()
            if "480" in q:
                return 100
            elif "720" in q:
                return 80
            elif "360" in q:
                return 60
            elif "1080" in q:
                return 40
            return 10

        sorted_videos = sorted(videos, key=priority_score, reverse=True)

        working_url = None
        selected_quality = "480p"

        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            for video in sorted_videos:
                url = video.get("url")
                if url and await check_url_working(session, url):
                    working_url = url
                    selected_quality = video.get("quality", "480p")
                    break
        finally:
            if close_session:
                await session.close()

        if not working_url:
            return []

        return [{
            "file_name": title,
            "size_bytes": (int(_raw_size_num) if _raw_size_num is not None else parse_size_to_bytes(file_size)),
            "stream_final_url": working_url,
            "download_url": working_url,
            "thumbnail": thumbnail,
            "extension": ".mp4",
            "duration": duration_str,
            "quality": selected_quality
        }]
    except Exception:
        return []


FETCH_FAIL_NOTES = {}

def _trunc(s, n=1500):
    if s is None:
        return "None"
    if len(s) <= n:
        return s
    return s[:n] + f"...<TRUNCATED {len(s) - n} chars>"


def _log_tier_empty(provider, url_text, response, body, sink):
    try:
        ct = response.headers.get("Content-Type", "?")
        final_url = str(response.url)
        status = response.status
        ln = len(body)
        if ln == 0:
            msg = (f"[{provider.upper()} EMPTY] url={url_text[:80]}.. final={final_url[:120]} "
                   f"status={status} content-type={ct} body_len=0 EMPTY_BODY")
        elif not body.strip():
            msg = (f"[{provider.upper()} EMPTY] url={url_text[:80]}.. final={final_url[:120]} "
                   f"status={status} content-type={ct} body_len={ln} BLANK_BODY")
        else:
            try:
                parsed = json.loads(body)
                resp_status = parsed.get("status") if isinstance(parsed, dict) else type(parsed).__name__
                msg = (f"[{provider.upper()} EMPTY] url={url_text[:80]}.. final={final_url[:120]} "
                       f"status={status} content-type={ct} body_len={ln} resp_status={resp_status} "
                       f"body={_trunc(body)!r}")
            except Exception:
                msg = (f"[{provider.upper()} EMPTY] url={url_text[:80]}.. final={final_url[:120]} "
                       f"status={status} content-type={ct} body_len={ln} NOT_JSON body={_trunc(body)!r}")
        logger.warning(msg)
        if sink is not None:
            sink.append(msg)
    except Exception:
        pass


def _log_resp_status(data, provider):
    try:
        if not isinstance(data, dict):
            logger.info(f"[{provider.upper()} STATUS] raw non-dict response type={type(data).__name__}")
            return
        status = data.get("status")
        code = data.get("code") or data.get("errno") or data.get("status_code") or data.get("error_code")
        total = data.get("total_files") or data.get("total_file")
        n = 0
        lst = data.get("list")
        if not isinstance(lst, list):
            lst = data.get("data", {})
            if isinstance(lst, dict):
                lst = lst.get("list", [])
        if isinstance(lst, list):
            n = len(lst)
        extra = f" | total_files={total} | parsed_items={n}" if (total is not None) else f" | parsed_items={n}"
        logger.info(f"[{provider.upper()} STATUS] status={status} code={code}{extra}")
    except Exception:
        pass


PLAY_BASE = "https://playterabox.com"
PLAY_DEFAULT_SECRET = "T9do@SM1?xGn5"
PLAY_DEFAULT_PATH = "/api/stream.php"
PLAY_FETCH_PATH = "/api/fetch-video"
PLAY_UAS = [
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; Mi 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
]

# --- Supabase backup resolver (backend of teraboxlinksconvertbot.lovable.app) ---
SUPA_API_URL = "https://uzhrlcttwpzihkqkxszl.supabase.co/functions/v1/terabox-api"
SUPA_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US",
    "Content-Type": "application/json",
    "Origin": "https://teraboxlinksconvertbot.lovable.app",
    "Referer": "https://teraboxlinksconvertbot.lovable.app/",
    "Priority": "u=1, i",
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36",
}


def _play_rand_ua():
    return random.choice(PLAY_UAS)


def _play_md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _play_headers():
    return {
        "Accept": "*/*",
        "Accept-Language": "en-US",
        "Content-Type": "application/json",
        "Origin": PLAY_BASE,
        "Referer": PLAY_BASE + "/",
        "User-Agent": _play_rand_ua(),
    }


async def _play_extract_creds(session, timeout=20):
    headers = {"User-Agent": _play_rand_ua(), "Referer": PLAY_BASE + "/", "Accept": "*/*"}
    async with session.get(PLAY_BASE + "/", headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        html = await r.text(errors="replace")
    scripts = re.findall(r'src="(/_next/static/chunks/[^"]+\.js)"', html)
    extra = []
    for s in scripts:
        if "webpack" not in s:
            continue
        try:
            async with session.get(PLAY_BASE + s, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as r2:
                txt = await r2.text(errors="replace")
            extra += ["/_next/static/chunks/" + m for m in re.findall(r'"static/chunks/([a-zA-Z0-9_\-]+\.js)"', txt)]
        except Exception:
            pass
    seen, urls = set(), []
    for rel in scripts + extra:
        if rel not in seen:
            seen.add(rel)
            urls.append(PLAY_BASE + rel)
    patterns = [
        re.compile(r'MD5\(\s*"([^"]+)"\s*\+\s*\w+\s*\+\s*"([^"]+)"\s*\)'),
        re.compile(r'MD5\("([^"]+)"\+[a-zA-Z]\+"([^"]+)"\)'),
        re.compile(r'MD5\("([^"]+)"\s*\+\s*[^+]+\s*\+\s*"([^"]+)"\)'),
        re.compile(r'md5\("([^"]+)"\s*\+\s*\w+\s*\+\s*"([^"]+)"\)', re.I),
        re.compile(r'"([^"]+)"\s*\+\s*\w+\s*\+\s*"([^"]+)"\s*\)\s*\.?\s*MD5', re.I),
    ]
    for url in urls:
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as r3:
                txt = await r3.text(errors="replace")
            if "fetch-video" not in txt and "stream.php" not in txt:
                continue
            for pat in patterns:
                mm = pat.search(txt)
                if mm:
                    return {"secret": mm.group(1), "path": mm.group(2), "source": url}
        except Exception:
            pass
    raise RuntimeError("playterabox creds extraction failed")


async def _play_fetch_video(session, link, secret, path, timeout=30):
    ts = int(time.time())
    token = _play_md5(secret + str(ts) + path)
    url = PLAY_BASE + PLAY_FETCH_PATH + "?token=" + urllib.parse.quote(token) + "&t=" + str(ts)
    async with session.post(url, json={"url": link}, headers=_play_headers(), timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        body = await resp.text(errors="replace")
        code = resp.status
    try:
        data = json.loads(body)
    except Exception:
        data = {}
    return code, data


def _play_map_items(data):
    result = []
    top = data.get("data")
    if not isinstance(top, list):
        top = data.get("list")
    if not isinstance(top, list):
        return result
    for it in top:
        name = str(it.get("name") or "Terabox_Video")
        size_fmt = str(it.get("file_size") or it.get("size_formatted") or "").strip()
        if not size_fmt or size_fmt.upper() in ("UNKNOWN", "N/A", "-"):
            size_fmt = "Unknown"
        dl = it.get("download_url") or it.get("download_link") or it.get("normal_dlink") or ""
        fs = it.get("stream_final_url")
        stream = _pick_best_stream(fs)
        st = it.get("stream_url") or ""
        stream = str(stream or st or "")
        if not dl and not stream:
            continue
        result.append({
            "file_name": name,
            "file_size": size_fmt,
            "size_bytes": parse_size_to_bytes(size_fmt),
            "stream_final_url": stream,
            "download_url": dl,
            "thumbnail": str(it.get("thumbnail") or ""),
            "extension": os.path.splitext(name)[1] or (".mp4" if stream else ".zip"),
            "duration": str(it.get("duration") or ""),
            "quality": str(it.get("quality") or "N/A"),
        })
    return result


def _supa_map_items(data):
    """Map supabase terabox-api response (allFiles[]) into standard item dicts."""
    result = []
    files = data.get("allFiles")
    if not isinstance(files, list):
        one = data.get("file")
        files = [one] if isinstance(one, dict) else []
    for it in files:
        name = str(it.get("name") or "Terabox_Video")
        size_raw = it.get("sizeBytes", it.get("size"))
        if isinstance(size_raw, str):
            size_bytes = parse_size_to_bytes(size_raw)
        else:
            try:
                size_bytes = int(size_raw)
            except (TypeError, ValueError):
                size_bytes = None
        size_fmt = str(it.get("sizeFormatted") or it.get("size") or "")
        if not size_fmt or size_fmt.upper() in ("UNKNOWN", "N/A", "-"):
            size_fmt = format_bytes(size_bytes) if size_bytes else "Unknown"
        fs = it.get("fastStreamUrl")
        stream = _pick_best_stream(fs)
        dl = str(it.get("downloadUrl") or it.get("download_url") or stream or "")
        if not dl and not stream:
            continue
        result.append({
            "file_name": name,
            "file_size": size_fmt,
            "size_bytes": size_bytes,
            "stream_final_url": stream,
            "download_url": dl,
            "thumbnail": str(it.get("thumbnail") or ""),
            "extension": os.path.splitext(name)[1] or (".mp4" if stream else ".zip"),
            "duration": str(it.get("duration") or ""),
            "quality": str(it.get("quality") or "N/A"),
        })
    return result


async def fetch_supabase_links(session, url_text, timeout=45):
    """Backup resolver: supabase edge function of teraboxlinksconvertbot.lovable.app.

    Returns (items, note). On HTTP 429 is retried once with a short backoff.
    """
    notes = []
    payload = {"url": url_text}
    for attempt in (1, 2):
        try:
            async with session.post(
                SUPA_API_URL, json=payload, headers=SUPA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                code = resp.status
                body = await resp.text(errors="replace")
        except Exception as e:
            notes.append(f"supabase request EXCEPTION: {e}")
            logger.warning(f"[SUPA REQ FAIL] url={url_text[:80]}.. err={e}")
            break
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        logger.info(f"[SUPA STATUS] url={url_text[:60]}.. code={code} body_len={len(body)}")
        if code == 429:
            note = f"supabase RATE_LIMITED attempt={attempt} body={body[:300]}"
            notes.append(note)
            logger.warning(f"[SUPA 429] url={url_text[:80]}.. body={body[:300]!r}")
            await asyncio.sleep(6)
            continue
        if code != 200:
            notes.append(f"supabase http {code} body={body[:300]}")
            logger.warning(f"[SUPA FAIL] url={url_text[:80]}.. code={code} body={body[:300]!r}")
            break
        if not isinstance(data, dict) or not data.get("success"):
            err = (data.get("error") if isinstance(data, dict) else "") or body[:300]
            notes.append(f"supabase api error: {err}")
            logger.warning(f"[SUPA API ERR] url={url_text[:80]}.. body={body[:400]!r}")
            break
        items = _supa_map_items(data)
        if items:
            logger.info(f"[SUPA OK] url={url_text[:60]}.. files={len(items)}")
            return items, None
        notes.append(f"supabase success but empty allFiles: {body[:400]}")
        logger.warning(f"[SUPA EMPTY] url={url_text[:80]}.. body={body[:400]!r}")
        break
    return [], " | ".join(notes)


async def fetch_playterabox_links(session, url_text, timeout=30):
    notes = []
    try:
        code, data = await _play_fetch_video(session, url_text, PLAY_DEFAULT_SECRET, PLAY_DEFAULT_PATH, timeout)
        _log_resp_status(data, "playterabox")
        if data.get("status") == "success":
            items = _play_map_items(data)
            if items:
                return items, None
            notes.append(f"playterabox success but empty list: {json.dumps(data)[:400]}")
            logger.warning(f"[PLAY EMPTY] url={url_text[:80]}.. body={json.dumps(data)[:400]!r}")
            return [], " | ".join(notes)
        bad = (code in (401, 403) or (data.get("status") in ("error", "failed")
               and re.search(r"token|auth|unauthorized|invalid|expired", json.dumps(data), re.I)))
        if bad:
            try:
                creds = await _play_extract_creds(session, timeout)
                code2, data = await _play_fetch_video(session, url_text, creds["secret"], creds["path"], timeout)
                _log_resp_status(data, "playterabox")
                if data.get("status") == "success":
                    items = _play_map_items(data)
                    if items:
                        return items, None
            except Exception as e:
                notes.append(f"playterabox creds re-extraction failed: {e}")
        notes.append(f"playterabox failed code={code} body={json.dumps(data)[:600]}")
        logger.warning(f"[PLAYERABOX FAIL] url={url_text[:80]}.. code={code} body={json.dumps(data)[:600]!r}")
    except Exception as e:
        notes.append(f"playterabox EXCEPTION: {e}")
        logger.warning(f"[FETCH] playterabox exception for {url_text[:60]}..: {e}")
    return [], " | ".join(notes)


async def fetch_terabox_links(session, url_text, timeout=30):
    tier_failures = []
    # Tier 1: playterabox.com
    try:
        items, note = await fetch_playterabox_links(session, url_text, timeout)
        if items:
            FETCH_FAIL_NOTES.pop(url_text, None)
            return items
        if note:
            tier_failures.append(note)
    except Exception as e:
        tier_failures.append(f"playterabox EXCEPTION: {e}")
        logger.warning(f"[FETCH] playterabox provider exception for {url_text[:60]}..: {e}")
    # Tier 2 (backup): supabase edge function - used for ANY primary failure (incl. rate limits)
    try:
        items, note = await fetch_supabase_links(session, url_text, timeout=45)
        if items:
            FETCH_FAIL_NOTES.pop(url_text, None)
            return items
        if note:
            tier_failures.append(note)
    except Exception as e:
        tier_failures.append(f"supabase EXCEPTION: {e}")
        logger.warning(f"[FETCH] supabase provider exception for {url_text[:60]}..: {e}")
    if url_text not in FETCH_FAIL_NOTES or len(FETCH_FAIL_NOTES) > 3000:
        FETCH_FAIL_NOTES.clear()
    FETCH_FAIL_NOTES[url_text] = " | ".join(tier_failures) if tier_failures else "all providers returned unfillable responses"
    return []
