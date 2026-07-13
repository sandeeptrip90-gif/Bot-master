#!/usr/bin/env python3
"""
Ultimate Enterprise Telegram Suite - DM Sender Engine (Database Integrated)
Filename: dmsender.py
"""

import os
import asyncio
import logging
import random
from datetime import datetime
from typing import Dict, Any

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeAudio, InputPeerUser
from telethon.errors import (
    PeerIdInvalidError, FloodWaitError, UserBannedInChannelError,
    UserDeactivatedError, AuthKeyUnregisteredError, SessionRevokedError,
    UserIsBlockedError, UserPrivacyRestrictedError
)
from pymongo import MongoClient

from config import CONFIG, DEVICE_PROFILES

logger = logging.getLogger("DMSenderEngine")

# Priority Override: ADMIN_ID mapped to environment variable as per system rules
ADMIN_ID = os.environ.get("ADMIN_ID")

class EnterpriseDMSender:
    def __init__(self, db):
        self.db = db
        self.is_running = False
        self.active_task = None
        self.wizard_state: Dict[int, Dict[str, Any]] = {}
        self.stats = {
            "total_sent": 0,
            "failed": 0,
            "accounts_used": 0,
            "accounts_down": 0,
            "total_targets": 0
        }
        
        # PATCH FIX: Remove hardcoded DB names and directly use initialized DB layer references
        try:
            # self.db.scraped_members pehle hi database.py me single DB 1 par mapped hai
            print("🎯 [DM Engine] Database Connected with Single-DB Unified Mapping.")
        except Exception as e:
            logger.error(f"❌ Fatal Mapping Fault in Database Router: {e}")

    def reset_stats(self):
        self.stats = {
            "total_sent": 0,
            "failed": 0,
            "accounts_used": 0,
            "accounts_down": 0,
            "total_targets": 0
        }

    def halt_campaign(self):
        """Stops the DM campaign immediately."""
        self.is_running = False
        if self.active_task:
            self.active_task.cancel()

    async def execute_dm_campaign(self, target_list: list, message_text: str, media_path: str, limit: int, ui_callback):
        """
        Robust background engine for distributed DM sending.
        Rotates accounts safely and handles blank string validation errors dynamically.
        """
        self.is_running = True
        self.reset_stats()
        
        # Clean safe text representation
        final_text = str(message_text).strip() if message_text else ""
        if final_text.lower() == "skip" or not final_text:
            final_text = None

        # Absolute fallback check: validation block to prevent empty payloads
        if not final_text and (not media_path or not os.path.exists(str(media_path))):
            await ui_callback("❌ **Campaign Aborted:** Both Text and Media payload cannot be empty. Setup aborted.")
            self.is_running = False
            return

        all_accounts = self.db.get_active_target_sessions()
        if not all_accounts:
            await ui_callback("❌ **Campaign Aborted:** Koi active verified session nahi mila.")
            self.is_running = False
            return

        account_pool = []
        for acc in all_accounts:
            phone = acc.get("phone")
            # 🔒 Lock this account globally so the auditor ignores it
            self.db.acquire_lock(phone)
            
            session_str = acc.get("session_string")
            api_id = int(acc.get("api_id", CONFIG["API_ID"]))
            api_hash = str(acc.get("api_hash", CONFIG["API_HASH"]))
            device = acc.get("device_metadata") or random.choice(DEVICE_PROFILES)
            
            client = TelegramClient(
                StringSession(session_str), api_id, api_hash,
                device_model=device.get("device_model", "PC 64bit"),
                system_version=device.get("system_version", "Windows 11"),
                app_version=device.get("app_version", "4.8.4")
            )
            account_pool.append({
                "client": client,
                "phone": phone,
                "consecutive_failures": 0,
                "is_connected": False
            })

        self.stats["accounts_used"] = len(account_pool)
        targets = target_list[:limit] if limit > 0 else target_list
        self.stats["total_targets"] = len(targets)
        
        await ui_callback(f"🚀 **DM Engine Started!**\nConnecting `{len(account_pool)}` distributed worker accounts...")

        pool_idx = 0
        target_idx = 0
        last_ui_update = datetime.now()

        while target_idx < len(targets) and self.is_running and len(account_pool) > 0:
            target_data = targets[target_idx]
            worker = account_pool[pool_idx]
            client = worker["client"]
            phone = worker["phone"]

            if not worker["is_connected"]:
                try:
                    await client.connect()
                    is_auth = await client.is_user_authorized()
                    if not is_auth:
                        raise AuthKeyUnregisteredError(request=None)
                    worker["is_connected"] = True
                except Exception as e:
                    error_str = str(e).lower()
                    if any(x in error_str for x in ["unregistered", "deactivated", "banned", "revoked"]):
                        self.db.remove_account_permanently(phone)
                        self.stats["accounts_down"] += 1
                    try: await client.disconnect() 
                    except: pass
                    account_pool.pop(pool_idx)
                    if pool_idx >= len(account_pool): pool_idx = 0
                    continue

            # Identity entity mapping matrix
            # RESOLUTION CORE: Resolving user entity via complete DB metrics & session caching
            entity = None
            try:
                if isinstance(target_data, dict):
                    user_id = target_data.get("user_id")
                    access_hash = target_data.get("access_hash")
                    username = target_data.get("username")
                    
                    # Method 1: Target via public username signature first (Fastest & Safest)
                    if username and str(username).strip() and str(username).lower() != "none":
                        u_str = str(username).strip()
                        entity = u_str if u_str.startswith("@") else f"@{u_str}"
                    
                    # Method 2: Access Hash & User ID Handshake resolution matrix
                    if not entity and user_id and access_hash:
                        try:
                            # Caches the entity into the client's session database map to clear peer token errors
                            input_peer = InputPeerUser(int(user_id), int(access_hash))
                            entity = await client.get_entity(input_peer)
                        except Exception as peer_err:
                            logger.debug(f"InputPeerUser explicit resolve skipped: {peer_err}")
                            entity = None
                            
                    # Method 3: Fallback straight to integer user id mapping
                    if not entity and user_id:
                        entity = int(user_id)
                else:
                    # Specific single handle target entry string fallback
                    target_str = str(target_data).strip()
                    if target_str.isdigit():
                        entity = int(target_str)
                    else:
                        entity = target_str if target_str.startswith("@") else f"@{target_str}"

                if not entity:
                    raise ValueError("Could not construct entity tokens.")

                # Core execution context block with absolute entity references
                if media_path and os.path.exists(str(media_path)):
                    is_voice = str(media_path).lower().endswith(('.ogg', '.mp3', '.m4a'))
                    attributes = [DocumentAttributeAudio(voice=True)] if is_voice else None
                    
                    # File transmission layer using the cached verified structural entity object
                    await client.send_file(
                        entity, 
                        str(media_path), 
                        caption=final_text,
                        voice_note=is_voice,
                        attributes=attributes
                    )
                else:
                    if not final_text:
                        raise ValueError("The message cannot be empty unless a file is provided")
                    await client.send_message(entity, final_text)

                self.stats["total_sent"] += 1
                worker["consecutive_failures"] = 0
                target_idx += 1
                
                # Human simulation timing window
                await asyncio.sleep(random.uniform(4.0, 7.0))

            except (PeerIdInvalidError, ValueError) as e:
                logger.error(f"Target Delivery Failure: {e}")
                self.stats["failed"] += 1
                target_idx += 1 # Safe skip without penalizing internal login pools

            except (UserIsBlockedError, UserPrivacyRestrictedError):
                self.stats["failed"] += 1
                target_idx += 1

            except FloodWaitError as e:
                worker["consecutive_failures"] += 1
                if e.seconds > 300 or worker["consecutive_failures"] >= 2:
                    await client.disconnect()
                    account_pool.pop(pool_idx)
                else:
                    await asyncio.sleep(e.seconds + 2)

            except Exception as e:
                error_str = str(e).lower()
                if any(x in error_str for x in ["banned", "deactivated", "unregistered", "revoked", "mute"]):
                    self.db.remove_account_permanently(phone)
                    self.stats["accounts_down"] += 1
                    account_pool.pop(pool_idx)
                else:
                    worker["consecutive_failures"] += 1
                    if worker["consecutive_failures"] >= 2:
                        try: await client.disconnect() 
                        except: pass
                        account_pool.pop(pool_idx)
                        
            if (datetime.now() - last_ui_update).seconds >= 10 or self.stats["total_sent"] % 5 == 0:
                await ui_callback(self._generate_live_status())
                last_ui_update = datetime.now()

            if len(account_pool) > 0:
                pool_idx = (pool_idx + 1) % len(account_pool)

        for worker in account_pool:
            if worker["is_connected"]:
                try: await worker["client"].disconnect()
                except: pass

        self.is_running = False
        final_msg = "✅ **DM CAMPAIGN COMPLETED** ✅\n" if target_idx >= len(targets) else "⚠️ **DM CAMPAIGN HALTED** ⚠️\n"
        await ui_callback(final_msg + self._generate_live_status())

        if media_path and os.path.exists(str(media_path)):
            try: os.remove(str(media_path))
            except: pass

    def _generate_live_status(self) -> str:
        return (
            "📊 **LIVE DM ENGINE TRACKER**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📨 Messages Delivered: `{self.stats['total_sent']} / {self.stats['total_targets']}`\n"
            f"🚫 Failed / Skipped Users: `{self.stats['failed']}`\n"
            f"⚡ Total Accounts Initialized: `{self.stats['accounts_used']}`\n"
            f"💀 Accounts Banned/Dropped: `{self.stats['accounts_down']}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: `{'🟢 RUNNING' if self.is_running else '🔴 STOPPED'}`"
        )


def setup_dmsender_handlers(bot: TelegramClient, db):
    sender_engine = EnterpriseDMSender(db)

    def is_admin(sender_id):
        if ADMIN_ID:
            return str(sender_id) == str(ADMIN_ID)
        return True

    @bot.on(events.NewMessage(pattern='/send_dmsender'))
    async def wizard_start(event):
        if not is_admin(event.sender_id): return
        
        if sender_engine.is_running:
            await event.reply("⚠️ **Engine Occupied:** Campaign background me active hai.")
            return

        try:
            pipeline = [{"$group": {"_id": "$source_group", "count": {"$sum": 1}}}]
            group_stats = sender_engine.db.get_group_stats()
            
            if not group_stats:
                msg = "📊 `scraped_data` collection is empty. \n\n👉 Direct single profile target karne ke liye `@username` type karein."
                group_list = []
            else:
                msg = "📊 **Scraped Database Summary:**\n━━━━━━━━━━━━━━━━━━━━━━\n"
                total_users = 0
                group_list = []
                for stat in group_stats:
                    grp = stat["_id"] if stat["_id"] else "Unknown Group"
                    cnt = stat["count"]
                    total_users += cnt
                    group_list.append(str(grp))
                    msg += f"🔹 `{grp}` : **{cnt} users**\n"
                
                msg += f"━━━━━━━━━━━━━━━━━━━━━━\n✨ **Total Available Users:** `{total_users}`\n\n"
                msg += "👉 **Type the EXACT Group Name** to fetch and send messages.\n"
                msg += "👉 **OR Type specific username/ID** to send an individual message."

            sender_engine.wizard_state[event.sender_id] = {
                "step": "AWAITING_TARGET_SELECTION",
                "available_groups": group_list,
                "targets": [],
                "text": "",
                "media": None,
                "limit": 0
            }
            await event.reply(msg)

        except Exception as e:
            await event.reply(f"❌ **Database Connection Error:** {e}")

    @bot.on(events.NewMessage)
    async def wizard_steps(event):
        if not is_admin(event.sender_id): return
        uid = event.sender_id
        if uid not in sender_engine.wizard_state:
            return
            
        if event.text and event.text.startswith('/'):
            return
            
        state = sender_engine.wizard_state[uid]
        step = state["step"]

        if step == "AWAITING_TARGET_SELECTION":
            inp = event.text.strip()
            extracted_targets = []

            if inp in state.get("available_groups", []):
                cursor = sender_engine.db.get_targets_by_group(inp)
                for doc in cursor:
                    extracted_targets.append({
                        "user_id": doc.get("user_id"),
                        "access_hash": doc.get("access_hash"),
                        "username": doc.get("username"),
                        "phone": doc.get("phone")
                    })
                
                if not extracted_targets:
                    await event.reply("❌ Is group me valid schema lines nahi mili. Phir se chunein.")
                    return
                
                state["targets"] = extracted_targets
                state["step"] = "AWAITING_LIMIT"
                await event.reply(
                    f"✅ **{len(state['targets'])} Users extracted mapping metadata structural array successfully!**\n\n"
                    f"Kitne logo ko message bhejna chahte hain? (Number daalein ya `all` likhein):"
                )
            else:
                state["targets"] = [inp]
                state["limit"] = 1
                state["step"] = "AWAITING_TEXT"
                await event.reply(
                    f"🎯 **Targeting individual:** {inp}\n\n"
                    "📝 Apna Promotional Message bhejein jo user ko DM karna hai.\n"
                    "*(Agar sirf media bhejna hai bina text ke, toh reply me `skip` type karein)*"
                )

        elif step == "AWAITING_LIMIT":
            inp = event.text.strip().lower()
            if inp == "all":
                limit = len(state['targets'])
            elif inp.isdigit():
                limit = int(inp)
                if limit <= 0:
                    await event.reply("❌ Valid number daaliye.")
                    return
            else:
                await event.reply("❌ Invalid format. Number me type karein ya 'all' likhein.")
                return

            state["limit"] = limit
            state["step"] = "AWAITING_TEXT"
            await event.reply(
                f"⚙️ **Target Limit Set to:** {limit}\n\n"
                "📝 Ab apna Promotional Message bhejein jo users ko DM karna hai.\n"
                "*(Agar sirf media bhejna hai bina text ke, toh reply me `skip` type karein)*"
            )

        elif step == "AWAITING_TEXT":
            msg_text = event.text.strip()
            state["text"] = msg_text
            
            state["step"] = "AWAITING_MEDIA"
            await event.reply(
                "🖼️ **Message Template Cached!**\n\n"
                "Ab media upload karein (Image/Video/Voice Note) ya aage badhne ke liye `skip` type karein:"
            )

        elif step == "AWAITING_MEDIA":
            if event.text and event.text.strip().lower() == "skip":
                state["media"] = None
            elif event.media:
                media_path = await bot.download_media(event.media)
                state["media"] = media_path
            else:
                await event.reply("❌ Media context not found. Re-send or type `skip`.")
                return

            ui_msg = await event.reply("⚡ Deploying DM Cluster Resources... Connecting to Accounts...")
            
            async def update_ui_status(text_payload):
                try: await ui_msg.edit(text_payload)
                except Exception: pass

            target_list = state["targets"]
            msg_txt = state["text"]
            media_pth = state["media"]
            limit_val = state["limit"]
            
            sender_engine.wizard_state.pop(uid)
            
            sender_engine.active_task = asyncio.create_task(
                sender_engine.execute_dm_campaign(target_list, msg_txt, media_pth, limit_val, update_ui_status)
            )

    @bot.on(events.NewMessage(pattern='/stop_dmsender'))
    async def wizard_stop(event):
        if not is_admin(event.sender_id): return
        if not sender_engine.is_running:
            await event.reply("ℹ️ Koi DM process running nahi hai.")
            return
        sender_engine.halt_campaign()
        await event.reply("🛑 **Emergency Brake Engaged!** Engine fully stopped.")

    return sender_engine