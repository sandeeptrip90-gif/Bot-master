#!/usr/bin/env python3
"""
Ultimate Enterprise Telegram Suite - WebRTC VoiceChat & Cross-DB Session Migration Engine
Filename: videochat.py
"""

import os
import sys
import time
import asyncio
import random
import logging
from typing import List, Dict, Optional, Any, Tuple, Set, TYPE_CHECKING


from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, DeleteHistoryRequest
from telethon.errors import (
    FloodWaitError, PhoneNumberBannedError, UserAlreadyParticipantError,
)
try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream
except Exception:
    from pytgcalls.group_call_factory import GroupCallFactory

    class MediaStream:
        def __init__(self, media_path: str):
            self.media_path = media_path

    class PyTgCalls:
        def __init__(self, client):
            self.client = client
            self._factory = GroupCallFactory(
                client,
                mtproto_backend=GroupCallFactory.MTPROTO_CLIENT_TYPE.TELETHON,
            )
            self._group_call = None
            self._current_chat_id = None

        async def start(self):
            self._group_call = None
            return True

        async def play(self, chat_id, stream):
            media_path = getattr(stream, "media_path", None) or getattr(stream, "path", "")
            if not media_path:
                raise ValueError("No media path provided for PyTgCalls playback")
            self._group_call = self._factory.get_file_group_call(input_filename=media_path)
            self._current_chat_id = chat_id
            await self._group_call.start(chat_id)
            return True

        async def change_volume(self, chat_id, volume):
            if self._group_call is None:
                raise RuntimeError("PyTgCalls group call not started")
            await self._group_call.set_my_volume(volume)
            return True

        async def stop(self):
            if self._group_call is not None:
                try:
                    await self._group_call.stop()
                except Exception:
                    pass
                self._group_call = None


from config import CONFIG, DEVICE_PROFILES
from database import SuiteDatabase
from scraper import MemberScraper

logger = logging.getLogger("SuiteVoiceChat")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def get_channel_peer_id(entity) -> int:
    """Convert a channel/group entity to the peer id expected by PyTgCalls."""
    if hasattr(entity, "broadcast") and entity.broadcast:
        return int(f"-100{entity.id}")
    if hasattr(entity, "megagroup") and entity.megagroup:
        return int(f"-100{entity.id}")
    return int(entity.id)

class CloudVoiceChatEngine:
    """Manages secure WebRTC streaming loops, session cross-logins, and official service OTP wipes."""
    
    def __init__(self, db: SuiteDatabase):
        self.db = db
        self.scraper_helper = MemberScraper(db)
        self.is_running = False
        self._active_tasks: List[asyncio.Task] = []
        self._running_calls: List[PyTgCalls] = []
        self._running_clients: List[TelegramClient] = []
        self._last_status: Dict[str, str] = {}


    async def clean_banned_accounts_handler(self):
        """
        🎯 ON-DEMAND ACCURATE CLEANER:
        Yeh database ke har ek record (chahe incomplete ho ya corrupted) ko raw layer se 
        utha kar live test karega aur banned accounts ko completely wipe out kar dega.
        """
        import random
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from config import CONFIG, DEVICE_PROFILES

        print("📡 Starting Deep Raw Account Validity and Banned Session Cleanup...")
        
        # Directly fetch raw documents from the source_accounts collection only
        all_accounts = self.db.get_all_accounts_raw()
        if not all_accounts:
            return 0, 0, []

        success_count = 0
        banned_count = 0
        error_logs = []

        for acc in all_accounts:
            phone = acc.get("phone")
            session_str = acc.get("session_string")
            api_id = int(acc.get("api_id", CONFIG["API_ID"]))
            api_hash = str(acc.get("api_hash", CONFIG["API_HASH"]))
            
            if not phone or not session_str:
                if phone:
                    self.db.remove_account_permanently(phone)
                    banned_count += 1
                continue

            device = random.choice(DEVICE_PROFILES) if DEVICE_PROFILES else {}
            
            client = TelegramClient(
                StringSession(session_str), api_id, api_hash,
                device_model=device.get("device_model", "PC 64bit"),
                system_version=device.get("system_version", "Windows 11"),
                app_version=device.get("app_version", "4.8.4")
            )
            
            try:
                await client.connect()
                is_authorized = await client.is_user_authorized()
                
                if is_authorized:
                    success_count += 1
                else:
                    self.db.remove_account_permanently(phone)
                    banned_count += 1
                    error_logs.append({"phone": phone, "error": "Session Unauthorized/Expired"})
                    
            except Exception as e:
                error_str = str(e).lower()
                if "auth_key_unregistered" in error_str or "user_deactivated" in error_str or "banned" in error_str or "invalid" in error_str:
                    self.db.remove_account_permanently(phone)
                    banned_count += 1
                    error_logs.append({"phone": phone, "error": f"Banned/Deactivated: {str(e)[:40]}"})
                else:
                    error_logs.append({"phone": phone, "error": f"Network Error: {str(e)[:40]}"})
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
                await asyncio.sleep(0.4) 

        return success_count, banned_count, error_logs

    # =====================================================================
    # === 1. DUAL-DB CROSS REFRESH & OTP DELETION CORE ====================
    # =====================================================================
    async def process_cross_migration(self) -> Tuple[int, int, List[Dict]]:
        success_count = 0
        failed_count = 0
        error_logs: List[Dict] = []

        source_accounts = self.db.fetch_source_accounts()
        if not source_accounts:
            return 0, 0, [{"phone": "All", "error": "Source Database DB 1 is empty."}]

        for acc in source_accounts:
            phone = str(acc.get("phone", "")).strip()
            if not phone:
                failed_count += 1
                error_logs.append({"phone": "Unknown", "error": "DB1 doc missing phone"})
                continue

            api_id_raw = acc.get("api_id", CONFIG["API_ID"])
            api_hash_raw = acc.get("api_hash", CONFIG["API_HASH"])
            try:
                api_id = int(api_id_raw)
                api_hash = str(api_hash_raw)
            except Exception:
                failed_count += 1
                error_logs.append({"phone": phone, "error": "Invalid api_id/api_hash in DB1"})
                continue

            device_metadata = (
                acc.get("device_metadata")
                or acc.get("device_fingerprint")
                or acc.get("device_profile")
                or random.choice(list(DEVICE_PROFILES))
            )
            session_str = str(acc.get("session_string") or "").strip()

            if not session_str:
                failed_count += 1
                error_logs.append({"phone": phone, "error": "Manual OTP only: DB1 missing session_string. Use /login <phone>."})
                continue

            server_client = TelegramClient(
                StringSession(session_str),
                api_id,
                api_hash,
                device_model=device_metadata.get("device_model", device_metadata.get("device_model", "PC 64bit") if isinstance(device_metadata, dict) else "PC 64bit"),
                system_version=device_metadata.get("system_version", "Windows 11") if isinstance(device_metadata, dict) else "Windows 11",
                app_version=device_metadata.get("app_version", "4.8.4") if isinstance(device_metadata, dict) else "4.8.4",
            )

            try:
                await server_client.connect()

                if not await server_client.is_user_authorized():
                    raise Exception("Session is not authorized. Use /login <phone> for manual OTP.")

                try:
                    service_peer = await server_client.get_input_entity(777000)
                    await server_client(
                        DeleteHistoryRequest(
                            peer=service_peer,
                            max_id=0,
                            just_clear=False,
                            revoke=True,
                        )
                    )
                except Exception as clean_err:
                    logger.debug(f"Notification cleanup failed for {phone}: {clean_err}")

                new_session_str = server_client.session.save()
                self.db.save_migrated_session(
                    phone=phone,
                    api_id=api_id,
                    api_hash=api_hash,
                    session_str=new_session_str,
                    device=device_metadata if isinstance(device_metadata, dict) else random.choice(list(DEVICE_PROFILES)),
                )

                success_count += 1

            except Exception as crash:
                failed_count += 1
                error_logs.append({"phone": phone, "error": str(crash)[:80]})
            finally:
                try:
                    await server_client.disconnect()
                except Exception:
                    pass

        return success_count, failed_count, error_logs

    # =====================================================================
    # === 2. WEBRTC STREAMING HANDSHAKE MECHANISMS (UPGRADED) =============
    # =====================================================================
    def _voice_log(self, phone: str, message: str, level: str = "info"):
        line = f"[VOICECHAT] {phone}: {message}"
        self._last_status[phone] = message
        print(line, flush=True)
        getattr(logger, level, logger.info)(line)

    def _resolve_audio_path(self, audio_path: str) -> Optional[str]:
        if not audio_path:
            return None
        candidate = os.path.abspath(audio_path)
        if os.path.exists(candidate):
            return candidate
        if os.path.exists(os.path.join(os.getcwd(), audio_path)):
            return os.path.abspath(os.path.join(os.getcwd(), audio_path))
        return None

    async def _wait_for_voice_chat(self, client: TelegramClient, entity, phone: str, max_retries: int):
        """Probes the target group metadata grid to find active voice chat node channels."""
        for attempt in range(1, max_retries + 1):
            if not self.is_running:
                return None
            try:
                full_chat_info = await client(GetFullChannelRequest(channel=entity))
                if hasattr(full_chat_info, "full_chat") and getattr(full_chat_info.full_chat, "call", None):
                    return full_chat_info
                self._voice_log(phone, f"Voice chat not active yet (attempt {attempt}/{max_retries}). Waiting 10s...")
            except Exception as probe_err:
                self._voice_log(phone, f"Voice chat probe failed (attempt {attempt}/{max_retries}): {probe_err}", "warning")
            if attempt < max_retries:
                await asyncio.sleep(10)
        return None

    async def _execute_single_stream(
        self,
        acc_doc: Dict[str, Any],
        group_link: str,
        audio_path: str,
        replacement_queue: asyncio.Queue
    ):
        """Asynchronously spawns separate instances for independent audio delivery loops with Native PyTgCalls Takeover Guard."""
        phone = str(acc_doc.get("phone"))
        
        # 🔒 LOCK ACCOUNT: Auditor loop block bypass mapping activation
        self.db.acquire_lock(phone)
        
        resolved_audio_path = self._resolve_audio_path(audio_path)
        if not resolved_audio_path:
            msg = f"Audio file not found: {audio_path}"
            self._voice_log(phone, msg, "error")
            self.db.release_lock(phone)
            await self._trigger_replacement_spawn(replacement_queue, group_link, audio_path)
            return

        device = acc_doc.get("device_metadata") or acc_doc.get("device_fingerprint") or random.choice(DEVICE_PROFILES)
        
        client = TelegramClient(
            StringSession(acc_doc.get("session_string")), 
            int(acc_doc.get("api_id", CONFIG["API_ID"])), 
            str(acc_doc.get("api_hash", CONFIG["API_HASH"])),
            device_model=device.get("device_model", "PC 64bit"),
            system_version=device.get("system_version", "Windows 11"),
            app_version=device.get("app_version", "4.8.4")
        )
        
        self._running_clients.append(client)
        target_entity = None
        app = None
        
        try:
            self._voice_log(phone, "Connecting Telegram client interface session...")
            await client.connect()
            
            # Windows socket stabilization sleep
            await asyncio.sleep(1.0)
            
            if not await client.is_user_authorized():
                raise Exception("Unauthorized session token encountered inside target worker node pool.")

            me = await client.get_me()
            my_user_id = me.id
            self._voice_log(phone, f"Handshake logged-in identity verified: {getattr(me, 'first_name', None) or phone} (ID: {my_user_id})")

            # 🛠️ AUTO GROUP JOINING MATRIX PROTOCOL
            is_private, resolved_token = self.scraper_helper.resolve_group_link(group_link)
            try:
                if is_private:
                    self._voice_log(phone, f"Auto-joining private invite channel map: {resolved_token}")
                    updates = await client(ImportChatInviteRequest(hash=resolved_token))
                    if getattr(updates, "chats", None):
                        target_entity = updates.chats[0]
                else:
                    self._voice_log(phone, f"Auto-joining public destination target node: @{resolved_token}")
                    target_entity = await client.get_entity(resolved_token)
                    await client(JoinChannelRequest(target_entity))
            except UserAlreadyParticipantError:
                self._voice_log(phone, "Target channel validation: Account wrapper node already present.")
            except Exception as join_err:
                self._voice_log(phone, f"Membership extraction route warning: {join_err}", "warning")

            # 🔄 CRITICAL RE-RESOLUTION STEP: Pull completely fresh target tokens post joining
            await asyncio.sleep(2.5)
            target_entity = await client.get_entity(resolved_token if is_private else group_link)

            # 🎯 STRICT PEER ROUTING CONVERSION MAP FOR PYTGCALLS
            chat_id = get_channel_peer_id(target_entity)
            self._voice_log(phone, f"Target peer handshake matched structural chat_id: {chat_id}")

            # ⏳ OPTIMIZED HIGH-SPEED TIMEOUT MODULE: Set to 4 attempts (40 Seconds Max Wait)
            full_chat_info = await self._wait_for_voice_chat(client, target_entity, phone, max_retries=4)
            if not full_chat_info:
                self._voice_log(phone, "⚠️ Active voice chat window not found within 40 seconds. Auto-Releasing pool...", "error")
                self.terminate_voice_cluster()
                return

            # 🚀 Instantiate PyTgCalls
            await asyncio.sleep(1.0)
            app = PyTgCalls(client)

            # 🛡️ FIXED: NATIVE TELETHON EVENT BRIDGE (Compatible with all versions)
            @client.on(events.Raw)
            async def native_takeover_handler(update):
                # UpdateGroupCallParticipants telegram raw event ID check
                if type(update).__name__ == "UpdateGroupCallParticipants":
                    for participant in getattr(update, "participants", []):
                        if hasattr(participant, "peer") and hasattr(participant.peer, "user_id"):
                            if participant.peer.user_id == my_user_id:
                                # Agar status change 'left' nahi hai, matlab user join kar raha hai
                                if not getattr(participant, "left", False):
                                    print(f"🚨 [USER TAKEOVER GUARD] Manual app activity for +{phone} detected!", flush=True)
                                    
                                    async def force_exit_routine():
                                        try:
                                            if app in self._running_calls: self._running_calls.remove(app)
                                            await app.stop()
                                        except: pass
                                        try:
                                            if client in self._running_clients: self._running_clients.remove(client)
                                            await client.disconnect()
                                        except: pass
                                        self.db.release_lock(phone)
                                    
                                    asyncio.create_task(force_exit_routine())
                                    return

            await app.start()
            self._running_calls.append(app)

            # PyTgCalls structural transmission handshake play mapping
            await app.play(chat_id, MediaStream(media_path=resolved_audio_path))
            self._voice_log(phone, "🚀 WebRTC Audio matrix pipeline stream established inside group voice chat pane!")

            # WINDOWS/TELETHON STABILIZATION COOLDOWN: Safe idling space configuration
            await asyncio.sleep(5.0)

            # Keep Alive Tracking Engine Loop (Resilient Core Architecture)
            while self.is_running:
                if not client.is_connected():
                    break
                try:
                    await client.get_me()
                    await app.change_volume(chat_id, random.choice([90, 100]))
                    delay = random.randint(*CONFIG.get("LOOP_KEEP_ALIVE", (30, 45)))
                    self._voice_log(phone, f"Stream healthy and stabilized. Next audit tick in {delay}s.")
                    await asyncio.sleep(delay)
                except FloodWaitError as fw:
                    self._voice_log(phone, f"⚠️ Telegram infrastructure flood wait activated: {fw.seconds}s. Safe idling...", "warning")
                    await asyncio.sleep(fw.seconds + 5)
                except Exception as loop_internal_err:
                    err_txt = str(loop_internal_err).lower()
                    if "already ended" in err_txt or "not found" in err_txt:
                        self._voice_log(phone, "⚠️ Voice chat dropped by host admin. Closing pool to protect accounts...", "error")
                        self.terminate_voice_cluster()
                        break
                    else:
                        self._voice_log(phone, f"Minor baseline telemetry fluctuation caught: {loop_internal_err}", "warning")
                        await asyncio.sleep(10)

        except Exception as e:
            err_str = str(e).lower()
            self._voice_log(phone, f"💥 WebRTC Stream Dropout Exception caught: {e}", "error")
            if any(k in err_str for k in ["banned", "deactivated", "unregistered", "revoked", "disabled"]):
                self.db.mark_account_failed(phone, f"Banned or Dropped during WebRTC call session: {err_str[:60]}")
            try:
                if app and app in self._running_calls: self._running_calls.remove(app)
                if app: await app.stop()
            except: pass
            try:
                if client in self._running_clients: self._running_clients.remove(client)
                await client.disconnect()
            except: pass
            
            # Start a replacement ONLY if a stream drop exception occurs
            await self._trigger_replacement_spawn(replacement_queue, group_link, audio_path)

        finally:
            self.db.release_lock(phone)


    async def _trigger_replacement_spawn(self, replacement_queue: asyncio.Queue, group_link: str, audio_path: str):
        """Picks the next idle account from the queue index and mounts it into the live stream."""
        if not self.is_running:
            return
        try:
            next_backup_doc = replacement_queue.get_nowait()
            phone = next_backup_doc.get("phone")
            print(f"🔄 [REPLACEMENT ENGINE] Deploying backup session +{phone} into active voice cluster loop...", flush=True)
            task = asyncio.create_task(self._execute_single_stream(next_backup_doc, group_link, audio_path, replacement_queue))
            self._active_tasks.append(task)
        except asyncio.QueueEmpty:
            print("⚠️ [REPLACEMENT ENGINE] Failed to spawn replacement node: Backup account queue is empty!", flush=True)

    async def launch_voice_cluster(self, group_link: str, audio_file: str = "silent.mp3", desired_count: int = 50) -> str:
        """Triggers the complete concurrent deployment sequence matching the desired targeted count with auto-replacement queue."""
        if not os.path.exists(audio_file):
            return f"❌ **Operation Failed:** Audio file `{audio_file}` nahi mila."

        self.is_running = True
        self._last_status.clear()
        active_pool = self.db.get_active_target_sessions()

        if not active_pool:
            self.is_running = False
            return "❌ **Operation Failed:** Source DB me active session nahi mila."

        print(f"[VOICECHAT] Desired Target Stream Cap Set to: `{desired_count}` accounts.", flush=True)
        random.shuffle(active_pool) 
        initial_deploy_batch = active_pool[:desired_count]
        backup_accounts_pool = active_pool[desired_count:]

        replacement_queue = asyncio.Queue()
        for backup_doc in backup_accounts_pool:
            await replacement_queue.put(backup_doc)

        for acc in initial_deploy_batch:
            if not self.is_running:
                break
            task = asyncio.create_task(self._execute_single_stream(acc, group_link, audio_file, replacement_queue))
            self._active_tasks.append(task)
            await asyncio.sleep(random.randint(*CONFIG["ACCOUNT_LAUNCH_DELAY"]))

        return f"🚀 **Voice Chat Cluster Active Matrix Initiated:** Target set to `{desired_count}`. Active connections are streaming. Backups loaded in queue: `{replacement_queue.qsize()}` accounts."

    def terminate_voice_cluster(self):
        """
        🛑 BRUTE-FORCE EMERGENCY SHUTDOWN:
        Bypass mapping errors and violently force-clear all async loops.
        """
        if not self.is_running:
            return
            
        print("🛑 [VOICECHAT MASTER] Emergency Shutdown Core Triggered. Brute-forcing memory purge...", flush=True)
        self.is_running = False

        # 1. Force Cancel all active asyncio tasks immediately
        for task in self._active_tasks:
            try: task.cancel()
            except: pass
        self._active_tasks.clear()

        # 2. Brute-force stop PyTgCalls calls without checking internal attributes
        for app in list(self._running_calls):
            try:
                # Direct force stop to bypass the UpdateGroupCall 'chat_id' attribute error
                asyncio.create_task(app.stop())
            except: 
                pass
        self._running_calls.clear()

        # 3. Force Disconnect Telethon clients
        for client in list(self._running_clients):
            try:
                # Extract phone directly without checking session filename to avoid any object lookup errors
                asyncio.create_task(client.disconnect())
            except: 
                pass
        self._running_clients.clear()

        # 4. Mandatory database lock release
        self.db.release_all_locks() # Ensure your database.py has this method or use a loop
        
        self._last_status.clear()
        print("✅ [VOICECHAT MASTER] Memory purges complete. Cluster is now offline.", flush=True)
