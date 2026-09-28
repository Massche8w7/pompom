import asyncio
import json
import os
import time

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db.json")

class Database:
    def __init__(self, db_file=DB_FILE):
        self.db_file = db_file
        self._lock = asyncio.Lock()
        self._data = {
            "users": {},
            "batches": {},
            "redeem": {},
            "file_cache": {},
            "user_links": []
        }
        self._load()

    def _load(self):
        try:
            with open(self.db_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for key in self._data:
                if key in loaded:
                    self._data[key] = loaded[key]
        except Exception:
            pass

    async def _save(self):
        tmp = self.db_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.db_file)

    def _default_user(self, user_id):
        return {
            "_id": int(user_id),
            "premium_expiry": 0,
            "last_trial_time": 0,
            "custom_channel": None,
            "custom_caption": None
        }

    async def add_user(self, user_id):
        async with self._lock:
            key = str(int(user_id))
            if key not in self._data["users"]:
                self._data["users"][key] = self._default_user(user_id)
                await self._save()

    async def get_user(self, user_id):
        async with self._lock:
            key = str(int(user_id))
            if key not in self._data["users"]:
                self._data["users"][key] = self._default_user(user_id)
                await self._save()
            return dict(self._data["users"][key])

    async def get_total_users(self):
        return len(self._data["users"])

    async def grant_premium(self, user_id, duration_seconds):
        async with self._lock:
            key = str(int(user_id))
            user = self._data["users"].get(key)
            if user is None:
                user = self._default_user(user_id)
                self._data["users"][key] = user
            current_time = int(time.time())
            existing = user.get("premium_expiry", 0)
            if existing > current_time:
                user["premium_expiry"] = existing + duration_seconds
            else:
                user["premium_expiry"] = current_time + duration_seconds
            await self._save()

    async def update_trial(self, user_id):
        async with self._lock:
            key = str(int(user_id))
            user = self._data["users"].get(key)
            if user is None:
                user = self._default_user(user_id)
                self._data["users"][key] = user
            current_time = int(time.time())
            user["last_trial_time"] = current_time
            user["premium_expiry"] = current_time + 21600
            await self._save()

    async def set_custom_channel(self, user_id, channel_id):
        async with self._lock:
            key = str(int(user_id))
            user = self._data["users"].get(key)
            if user is None:
                user = self._default_user(user_id)
                self._data["users"][key] = user
            user["custom_channel"] = channel_id
            await self._save()

    async def set_custom_caption(self, user_id, caption):
        async with self._lock:
            key = str(int(user_id))
            user = self._data["users"].get(key)
            if user is None:
                user = self._default_user(user_id)
                self._data["users"][key] = user
            user["custom_caption"] = caption
            await self._save()

    async def add_batch(self, user_id, chat_id, links):
        async with self._lock:
            batch_id = str(int(time.time())) + str(user_id)[-4:]
            self._data["batches"][batch_id] = {
                "_id": batch_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "links": links,
                "total": len(links),
                "current_index": 0,
                "success": 0,
                "fail": 0,
                "status": "pending"
            }
            await self._save()
            return batch_id

    async def get_batch(self, batch_id):
        batch = self._data["batches"].get(batch_id)
        return dict(batch) if batch else None

    async def update_batch_progress(self, batch_id, index, success, fail):
        async with self._lock:
            batch = self._data["batches"].get(batch_id)
            if batch:
                batch["current_index"] = index
                batch["success"] = success
                batch["fail"] = fail
                await self._save()

    async def update_batch_status(self, batch_id, status):
        async with self._lock:
            batch = self._data["batches"].get(batch_id)
            if batch:
                batch["status"] = status
                await self._save()

    async def get_running_batches(self):
        return [dict(b) for b in self._data["batches"].values() if b.get("status") == "running"]

    async def add_redeem_code(self, code, duration_str):
        async with self._lock:
            self._data["redeem"][code] = {"duration": duration_str}
            await self._save()

    async def get_redeem_code(self, code):
        doc = self._data["redeem"].get(code)
        if doc:
            return doc.get("duration")
        return None

    async def delete_redeem_code(self, code):
        async with self._lock:
            self._data["redeem"].pop(code, None)
            await self._save()

    async def get_all_users(self):
        return [dict(u) for u in self._data["users"].values()]

    async def save_file_cache(self, url, file_id, caption=None):
        async with self._lock:
            self._data["file_cache"][url] = {
                "file_id": file_id,
                "caption": caption,
                "timestamp": int(time.time())
            }
            await self._save()

    async def get_file_cache(self, url):
        doc = self._data["file_cache"].get(url)
        if doc:
            return doc.get("file_id"), doc.get("caption")
        return None, None

    async def save_user_link(self, user_id, link):
        async with self._lock:
            self._data["user_links"].append({
                "user_id": int(user_id),
                "link": link,
                "created_at": int(time.time())
            })
            await self._save()

    async def get_all_user_links(self):
        return list(self._data["user_links"])

db = Database()
