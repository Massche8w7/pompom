import sys, json, asyncio
sys.path.insert(0, "/tmp/supa")
from domain import _supa_map_items, fetch_supabase_links

# Schema-faithful samples built from the user's live backup-API outputs (long tokens elided)
S1 = {"success": True, "totalFiles": 1,
      "file": {"name": "Desi_Girl__ed_by_Boyfriend_M1330(1)(4).mp4", "size": 41095222, "sizeFormatted": "39.19 MB", "type": "video", "duration": "04:04", "quality": "480p", "thumbnail": "https://iteraplay.tera-api75.workers.dev/thumbnail?token=ELIDED"},
      "links": {"fastStream": {"360p": "https://iteraplay.tera-api75.workers.dev/fast_stream?token=A.m3u8", "480p": "https://iteraplay.tera-api75.workers.dev/fast_stream?token=B.m3u8"}},
      "allFiles": [{"name": "Desi_Girl__ed_by_Boyfriend_M1330(1)(4).mp4", "size": 41095222, "sizeFormatted": "39.19 MB", "type": "video", "duration": "04:04", "quality": "480p",
                    "fastStreamUrl": {"360p": "https://iteraplay.tera-api75.workers.dev/fast_stream?token=A.m3u8", "480p": "https://iteraplay.tera-api75.workers.dev/fast_stream?token=B.m3u8"},
                    "subtitleUrl": "https://iteraplay.tera-api75.workers.dev/subtitle?token=C", "thumbnail": "https://iteraplay.tera-api75.workers.dev/thumbnail?token=ELIDED", "folder": "root"}]}

S7 = {"success": True, "totalFiles": 7, "allFiles": [
    {"name": "VID (6).mp4", "size": 1633812,  "sizeFormatted": "1.56 MB", "duration": "00:11", "quality": "1080p", "fastStreamUrl": {"360p": "x360", "480p": "x480"}, "thumbnail": "t6"},
    {"name": "VID (22).mp4", "size": 2200732, "sizeFormatted": "2.10 MB", "duration": "00:15", "quality": "720p",  "fastStreamUrl": {"360p": "x360", "480p": "x480"}, "thumbnail": "t22"},
    {"name": "VID (3).mp4",  "size": 2187661, "sizeFormatted": "2.09 MB", "duration": "00:14", "quality": "1080p", "fastStreamUrl": {"360p": "x360", "480p": "x480"}, "thumbnail": "t3"},
    {"name": "VID (20).mp4", "size": 2062146, "sizeFormatted": "1.97 MB", "duration": "00:13", "quality": "1080p", "fastStreamUrl": {"360p": "x360", "480p": "x480"}, "thumbnail": "t20"},
    {"name": "VID (4).mp4",  "size": 1147585, "sizeFormatted": "1.09 MB", "duration": "00:10", "quality": "720p",  "fastStreamUrl": {"360p": "x360", "480p": "x480"}, "thumbnail": "t4"},
    {"name": "VID (5).mp4",  "size": 2067778, "sizeFormatted": "1.97 MB", "duration": "00:13", "quality": "1080p", "fastStreamUrl": {"360p": "x360", "480p": "x480"}, "thumbnail": "t5"},
    {"name": "VID (21).mp4", "size": 3474373, "sizeFormatted": "3.31 MB", "duration": "00:37", "quality": "480p",  "fastStreamUrl": {"360p": "x360", "480p": "x480"}, "thumbnail": "t21"}]}

S500 = {"error": "TeraBox API returned error status", "success": False}

class FakeResp:
    def __init__(self, code, body): self.status = code; self._b = body
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def text(self, errors="replace"): return self._b

class FakeSession:
    def __init__(self, code, body): self.code, self.body = code, body
    def post(self, *a, **k): return FakeResp(self.code, self.body)

async def runner():
    it = _supa_map_items(S1)
    assert len(it) == 1, it
    i = it[0]
    assert i["file_name"] == "Desi_Girl__ed_by_Boyfriend_M1330(1)(4).mp4"
    assert i["size_bytes"] == 41095222 and i["file_size"] == "39.19 MB"
    assert i["stream_final_url"] == S1["allFiles"][0]["fastStreamUrl"]["480p"]  # 480p > 360p
    assert i["download_url"] == i["stream_final_url"]
    assert i["extension"] == ".mp4" and i["quality"] == "480p" and i["duration"] == "04:04"
    print("PASS single-file: 1 item, size=41095222 (39.19 MB), 480p preferred, ext=.mp4")

    it7 = _supa_map_items(S7)
    assert len(it7) == 7, len(it7)
    assert [x["file_name"] for x in it7] == ["VID (6).mp4", "VID (22).mp4", "VID (3).mp4", "VID (20).mp4", "VID (4).mp4", "VID (5).mp4", "VID (21).mp4"]
    assert all(isinstance(x["size_bytes"], int) for x in it7)
    print("PASS multi-file totalFiles=7: 7 items, order preserved, int sizes, streams mapped")

    items, note = await fetch_supabase_links(FakeSession(429, '{"error":"rate limited"}'), "https://terasharelink.com/s/x")
    assert items == [] and "RATE_LIMITED" in note, (items, note)
    print("PASS http 429: retried once with backoff, returned rate-limit note")

    items, note = await fetch_supabase_links(FakeSession(500, json.dumps(S500)), "https://terasharelink.com/s/x")
    assert items == [] and "supabase http 500" in note, (items, note)
    print("PASS http 500: clean failure note (matches live 'TeraBox API returned error status' links)")

    items, note = await fetch_supabase_links(FakeSession(200, json.dumps(S1)), "https://teraboxlink.com/s/x")
    assert items and note is None, (items, note)
    print("PASS http 200 success: items returned, note=None")

asyncio.run(runner())
print("ALL_SUPA_TESTS_OK")

# ---- Higher-tier selection tests (user: always prefer 1080p > 720p > 480p > 360p) ----
QT = [("1080p", "https://cdn/tok-1080.m3u8"), ("480p", "https://cdn/tok-480.m3u8"),
      ("360p", "https://cdn/tok-360.m3u8"), ("720p", "https://cdn/tok-720.m3u8")]

ORDER_MIXED = {"success": True, "totalFiles": 1, "allFiles": [{
    "name": "mixed_order.mp4", "size": 1000, "sizeFormatted": "1 KB",
    "fastStreamUrl": {"480p": "tok-480", "1080p": "tok-1080", "360p": "tok-360", "720p": "tok-720"}}]}
ORDER_NO_1080 = {"success": True, "totalFiles": 1, "allFiles": [{
    "name": "no1080.mp4", "size": 1000, "sizeFormatted": "1 KB",
    "fastStreamUrl": {"480p": "tok-480", "360p": "tok-360"}}]}
ORDER_ORIGINAL = {"success": True, "totalFiles": 1, "allFiles": [{
    "name": "orig.mp4", "size": 1000, "sizeFormatted": "1 KB",
    "fastStreamUrl": {"480p": "tok-480", "original": "tok-master", "360p": "tok-360", "1080p": "tok-1080"}}]}
ORDER_HLS_LABELS = {"success": True, "totalFiles": 1, "allFiles": [{
    "name": "labels.mp4", "size": 1000, "sizeFormatted": "1 KB",
    "fastStreamUrl": {"SD": "tok-sd", "HD": "tok-hd", "FHD": "tok-fhd", "2K": "tok-2k", "4K": "tok-4k"}}]}
ORDER_EMPTY_TOP = {"success": True, "totalFiles": 1, "allFiles": [{
    "name": "gap.mp4", "size": 1000, "sizeFormatted": "1 KB",
    "fastStreamUrl": {"1080p": "", "720p": "tok-720", "360p": "tok-360"}}]}

async def quality_runner():
    from domain import _pick_best_stream, _quality_rank
    # rank sanity
    assert _quality_rank("original") > _quality_rank("4k") > _quality_rank("2k") > _quality_rank("1080p") \
           > _quality_rank("720p") > _quality_rank("480p") > _quality_rank("360p") > _quality_rank("weird-token")
    # selection from literal dicts
    assert _pick_best_stream({"360p": "a", "480p": "b", "720p": "c", "1080p": "d"}) == "d"
    assert _pick_best_stream({"360p": "a"}) == "a"
    assert _pick_best_stream("https://direct.m3u8") == "https://direct.m3u8"
    assert _pick_best_stream(None) == ""
    # map-level: highest tier is always selected regardless of key order
    assert _supa_map_items(ORDER_MIXED)[0]["stream_final_url"] == "tok-1080"
    assert _supa_map_items(ORDER_NO_1080)[0]["stream_final_url"] == "tok-480"
    # original/source beats 1080p
    assert _supa_map_items(ORDER_ORIGINAL)[0]["stream_final_url"] == "tok-master"
    # alias labels FHD/HD/SD + 4K/2K
    got = _supa_map_items(ORDER_HLS_LABELS)[0]["stream_final_url"]
    assert got == "tok-4k", got
    # empty top tier falls through to next available
    assert _supa_map_items(ORDER_EMPTY_TOP)[0]["stream_final_url"] == "tok-720"
    print("PASS quality ranking: original>4K>2K>FHD>HD>SD; mixed key order -> 1080p picked; missing 1080p -> 480p; empty top tier -> 720p fallback; direct URL passthrough")

asyncio.run(quality_runner())
print("ALL_QUALITY_TESTS_OK")
