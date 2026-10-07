import asyncio
import threading
import time
import os
import uuid
from datetime import datetime
from pyrogram import Client
from pyrogram.errors import (
    FloodWait, BadRequest, UserDeactivated, AuthKeyUnregistered,
    PeerIdInvalid, ChannelInvalid
)
from .config import API_ID, API_HASH, _global_stats
from .database import users_col, ads_config_col, broadcast_msgs_col

class AdsBroadcaster:
    def __init__(self, session_manager):
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._round_count = 0
        self._current_broadcast_id = None
        self.session_manager = session_manager

    def start(self):
        if self._running:
            return False
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("📢 Ads Broadcaster started")
        return True

    def stop(self):
        if not self._running:
            return False
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("📢 Ads Broadcaster stopped")
        return True

    def _get_interval(self):
        if ads_config_col is not None:
            config = ads_config_col.find_one({"_id": "main_config"})
            if config:
                return config.get("interval", 600)
        return 600

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._run_round()
            except Exception as e:
                print(f"Broadcast round error: {e}")

            interval = self._get_interval()
            minutes = interval // 60
            print(f"⏳ Round complete. Waiting {minutes} minutes for next round...")
            self._stop_event.wait(interval)

    def _verify_user_session(self, user):
        user_id = user.get("user_id")
        phone = user.get("phone", "Unknown")
        session_string = user.get("session_string")
        
        if not session_string:
            return False
            
        try:
            async def check():
                client = Client(
                    name=f"check_{user_id}_{uuid.uuid4().hex[:6]}",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=session_string,
                    in_memory=True,
                    no_updates=True
                )
                try:
                    await client.connect()
                    me = await client.get_me()
                    return me is not None
                except (AuthKeyUnregistered, UserDeactivated):
                    return False
                finally:
                    try:
                        await client.disconnect()
                    except:
                        pass
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(check())
            try:
                loop.close()
            except:
                pass
            return result
        except Exception as e:
            print(f"⚠️ Session check failed for {phone}: {e}")
            return False

    def _delete_previous_messages(self):
        if broadcast_msgs_col is None:
            return

        try:
            old_msgs = list(broadcast_msgs_col.find())
            if not old_msgs:
                print("📝 No old messages to delete")
                return

            print(f"🗑️ Deleting {len(old_msgs)} old broadcast messages...")
            deleted_count = 0
            
            for msg_data in old_msgs:
                try:
                    session_string = msg_data.get("session_string")
                    chat_id = msg_data.get("chat_id")
                    message_id = msg_data.get("message_id")

                    if not all([session_string, chat_id, message_id]):
                        continue

                    async def delete_msg():
                        client = Client(
                            name=f"del_{uuid.uuid4().hex[:8]}",
                            api_id=API_ID,
                            api_hash=API_HASH,
                            session_string=session_string,
                            in_memory=True,
                            no_updates=True
                        )
                        try:
                            await client.connect()
                            await client.delete_messages(chat_id, message_id)
                            return True
                        except Exception:
                            return False
                        finally:
                            try:
                                await client.disconnect()
                            except:
                                pass

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    success = loop.run_until_complete(delete_msg())
                    try:
                        loop.close()
                    except:
                        pass

                    if success:
                        deleted_count += 1
                        broadcast_msgs_col.delete_one({"_id": msg_data["_id"]})

                except Exception as e:
                    print(f"⚠️ Failed to delete message: {e}")

            print(f"✅ Deleted {deleted_count} old messages")

        except Exception as e:
            print(f"Error deleting old messages: {e}")

    def _run_round(self):
        self._round_count += 1
        broadcast_id = f"round_{self._round_count}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._current_broadcast_id = broadcast_id

        print(f"\n{'='*60}")
        print(f"🚀 STARTING BROADCAST ROUND #{self._round_count}")
        print(f"🆔 Broadcast ID: {broadcast_id}")
        print(f"{'='*60}\n")

        self._delete_previous_messages()

        if users_col is None or ads_config_col is None:
            print("❌ DB not connected")
            return

        config = ads_config_col.find_one({"_id": "main_config"})
        if not config or not config.get("ads_enabled", False):
            print("❌ Ads disabled")
            return

        caption = config.get("caption", "")
        photo_path = config.get("photo_path")

        raw_users = list(users_col.find({"ads_enabled": True}))
        
        users_to_send = []
        for user in raw_users:
            if self._verify_user_session(user):
                users_to_send.append(user)
            else:
                users_col.update_one(
                    {"_id": user["_id"]}, 
                    {"$set": {"ads_enabled": False, "status": "expired"}}
                )
                print(f"❌ Session expired for {user.get('phone', 'Unknown')}, disabled")

        if not users_to_send:
            print("📭 No valid users to broadcast")
            return

        print(f"📨 Broadcasting to {len(users_to_send)} valid users...")

        for idx, user in enumerate(users_to_send):
            if self._stop_event.is_set():
                print("⏹️ Broadcast stopped")
                break

            threading.Thread(
                target=self._send_to_user,
                args=(user, caption, photo_path, broadcast_id),
                daemon=True
            ).start()

            if (idx + 1) % 5 == 0:
                time.sleep(2)

    def _send_to_user(self, user, caption, photo_path, broadcast_id):
        user_id = user.get("user_id")
        phone = user.get("phone", "Unknown")
        session_string = user.get("session_string")

        if not session_string:
            print(f"⚠️ No session string for {phone}")
            return

        sent_messages = []

        try:
            async def send_ad():
                client = Client(
                    name=f"temp_{user_id}_{uuid.uuid4().hex[:8]}",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=session_string,
                    in_memory=True,
                    no_updates=True
                )

                try:
                    await client.connect()
                    
                    try:
                        me = await client.get_me()
                        if not me:
                            raise AuthKeyUnregistered("Invalid session")
                    except Exception:
                        raise AuthKeyUnregistered("Session verification failed")

                    try:
                        if photo_path and os.path.exists(photo_path):
                            msg = await client.send_photo("me", photo=photo_path, caption=caption)
                            sent_messages.append({
                                "chat_id": "me",
                                "message_id": msg.id,
                                "session_string": session_string
                            })
                        else:
                            msg = await client.send_message("me", caption)
                            sent_messages.append({
                                "chat_id": "me",
                                "message_id": msg.id,
                                "session_string": session_string
                            })
                        print(f"✅ DM sent to {phone}")
                    except Exception as e:
                        print(f"❌ DM failed for {phone}: {e}")

                    try:
                        group_count = 0
                        
                        async for dialog in client.get_dialogs():
                            if self._stop_event.is_set():
                                break

                            if dialog is None or dialog.chat is None:
                                continue

                            chat_type = getattr(dialog.chat, 'type', None)

                            if chat_type in ["group", "supergroup"]:
                                chat_id = dialog.chat.id
                                
                                try:
                                    await client.get_chat(chat_id)
                                    
                                    if photo_path and os.path.exists(photo_path):
                                        msg = await client.send_photo(chat_id, photo=photo_path, caption=caption)
                                    else:
                                        msg = await client.send_message(chat_id, caption)

                                    sent_messages.append({
                                        "chat_id": chat_id,
                                        "message_id": msg.id,
                                        "session_string": session_string
                                    })
                                    group_count += 1
                                    await asyncio.sleep(3)

                                except PeerIdInvalid:
                                    print(f"⚠️ Skipping invalid peer {chat_id} for {phone}")
                                    continue
                                except ChannelInvalid:
                                    print(f"⚠️ Skipping invalid channel {chat_id} for {phone}")
                                    continue
                                except FloodWait as fw:
                                    print(f"⏳ FloodWait in group for {phone}: {fw.value}s")
                                    await asyncio.sleep(min(fw.value, 30))
                                except BadRequest as e:
                                    print(f"⚠️ BadRequest in group for {phone}: {e}")
                                    continue
                                except Exception as e:
                                    print(f"⚠️ Group send failed for {phone} in {chat_id}: {e}")
                                    continue

                        print(f"✅ Sent to {group_count} groups for {phone}")

                    except Exception as e:
                        print(f"❌ Groups failed for {phone}: {e}")

                    if users_col is not None and sent_messages:
                        users_col.update_one(
                            {"_id": user["_id"]},
                            {
                                "$set": {
                                    "last_ad_time": datetime.now(),
                                    "last_broadcast_id": broadcast_id
                                }, 
                                "$inc": {"total_ads_sent": len(sent_messages)}
                            }
                        )

                except UserDeactivated:
                    raise
                except AuthKeyUnregistered:
                    raise
                except Exception as e:
                    print(f"❌ Error for {phone}: {e}")
                    raise
                finally:
                    try:
                        await client.disconnect()
                    except:
                        pass

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(send_ad())
            try:
                loop.close()
            except:
                pass

            if broadcast_msgs_col is not None and sent_messages:
                for msg_data in sent_messages:
                    broadcast_msgs_col.insert_one({
                        "user_id": user_id,
                        "phone": phone,
                        "broadcast_id": broadcast_id,
                        "chat_id": str(msg_data["chat_id"]),
                        "message_id": msg_data["message_id"],
                        "session_string": session_string,
                        "sent_at": datetime.now()
                    })

        except UserDeactivated:
            if users_col is not None:
                users_col.update_one(
                    {"_id": user["_id"]}, 
                    {"$set": {"ads_enabled": False, "status": "deactivated"}}
                )
            print(f"❌ User {phone} deactivated")
        except AuthKeyUnregistered:
            if users_col is not None:
                users_col.update_one(
                    {"_id": user["_id"]}, 
                    {"$set": {"ads_enabled": False, "status": "expired"}}
                )
            print(f"❌ Session expired for {phone}")
        except FloodWait as e:
            print(f"⏳ Flood wait for {phone}: {e.value}s")
        except PeerIdInvalid as e:
            print(f"❌ Peer ID invalid for {phone}: {e}")
        except Exception as e:
            print(f"❌ Failed to send to {phone}: {e}")

ads_broadcaster = None

def init_broadcaster(session_manager):
    global ads_broadcaster
    ads_broadcaster = AdsBroadcaster(session_manager)
    return ads_broadcaster