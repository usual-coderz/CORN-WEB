import asyncio
import threading
from datetime import datetime, timedelta
from pyrogram import Client
from .config import API_ID, API_HASH, _global_stats
from .database import temp_sessions_col

class SessionThread:
    def __init__(self, session_id, phone):
        self.session_id = session_id
        self.phone = phone
        self.client = None
        self.loop = None
        self.thread = None
        self.connected = False
        self.connection_error = None
        self._connected_event = threading.Event()
        self._start_thread()

    def _start_thread(self):
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    async def _init_and_connect(self):
        self.client = Client(
            name=f"session_{self.session_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
            no_updates=True
        )
        await self.client.connect()

    def _run_loop(self):
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._init_and_connect())
            self.connected = True
            self._connected_event.set()
            self.loop.run_forever()
        except Exception as e:
            self.connection_error = str(e)
            self.connected = False
            self._connected_event.set()
        finally:
            try:
                if self.loop and self.loop.is_running():
                    self.loop.stop()
                if self.loop and not self.loop.is_closed():
                    self.loop.close()
            except:
                pass

    def wait_for_connection(self, timeout=10):
        return self._connected_event.wait(timeout=timeout)

    def execute(self, coro_func, *args, timeout=60):
        if not self.loop or not self.connected:
            raise RuntimeError("Session not connected")
        async def wrapper():
            coro = coro_func(*args)
            return await coro
        future = asyncio.run_coroutine_threadsafe(wrapper(), self.loop)
        return future.result(timeout=timeout)

    def stop(self):
        try:
            if self.client and self.connected:
                asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop).result(timeout=3)
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=3)
        except:
            pass

class SessionManager:
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()
        self._active_count = 0

    def create_session(self, session_id, phone):
        self.remove_session(session_id)
        session = SessionThread(session_id, phone)
        if not session.wait_for_connection(timeout=10):
            session.stop()
            raise RuntimeError("Session connection timeout")
        if not session.connected:
            error_msg = session.connection_error or "Unknown error"
            session.stop()
            raise RuntimeError(f"Failed to connect: {error_msg}")
        with self._lock:
            self._sessions[session_id] = session
            self._active_count = len([s for s in self._sessions.values() if s.connected])
        if temp_sessions_col is not None:
            temp_sessions_col.update_one(
                {'_id': session_id},
                {'$set': {'phone': phone, 'created_at': datetime.now(), 'status': 'connected'}},
                upsert=True
            )
        return session

    def get_session(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.connected and session.thread.is_alive():
                return session
            elif session:
                self._sessions.pop(session_id, None)
        return None

    def remove_session(self, session_id):
        with self._lock:
            session = self._sessions.pop(session_id, None)
            self._active_count = len([s for s in self._sessions.values() if s.connected])
        if session:
            session.stop()
        if temp_sessions_col is not None:
            temp_sessions_col.delete_one({'_id': session_id})

    def cleanup_old(self, max_age_minutes=10):
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        if temp_sessions_col is not None:
            old_docs = temp_sessions_col.find({'created_at': {'$lt': cutoff}})
            for doc in old_docs:
                self.remove_session(doc['_id'])
            temp_sessions_col.delete_many({'created_at': {'$lt': cutoff}})

    def cleanup_dead_sessions(self):
        with self._lock:
            dead_sessions = []
            for session_id, session in list(self._sessions.items()):
                if not session.connected or not session.thread.is_alive():
                    dead_sessions.append(session_id)

            for session_id in dead_sessions:
                self._sessions.pop(session_id, None)
                if temp_sessions_col is not None:
                    temp_sessions_col.delete_one({'_id': session_id})

            self._active_count = len([s for s in self._sessions.values() if s.connected])
            return len(dead_sessions)

    def get_active_count(self):
        with self._lock:
            return self._active_count

class SessionCleaner:
    def __init__(self, session_manager):
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self.session_manager = session_manager

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("🧹 Session Cleaner started (30s interval)")

    def stop(self):
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("🧹 Session Cleaner stopped")

    def _run(self):
        from .database import ads_config_col, broadcast_msgs_col
        while not self._stop_event.is_set():
            try:
                count = self.session_manager.cleanup_dead_sessions()
                if count > 0:
                    print(f"🧹 Cleaned up {count} dead sessions")

                _global_stats['active_sessions'] = self.session_manager.get_active_count()
                if broadcast_msgs_col is not None:
                    _global_stats['pending_msgs'] = broadcast_msgs_col.count_documents({})

                if ads_config_col is not None:
                    ads_config_col.update_one(
                        {"_id": "stats"},
                        {"$set": {
                            "active_sessions": _global_stats['active_sessions'],
                            "pending_msgs": _global_stats['pending_msgs'],
                            "last_cleanup": datetime.now()
                        }},
                        upsert=True
                    )
            except Exception as e:
                print(f"Session cleaner error: {e}")

            self._stop_event.wait(30)

session_manager = SessionManager()
session_cleaner = SessionCleaner(session_manager)