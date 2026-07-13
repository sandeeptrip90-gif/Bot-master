#!/usr/bin/env python3
"""
Ultimate Enterprise Telegram Suite - Dual-Database & State Tracking Layer
Filename: database.py
"""

import time
import re
import pickle
import random
import pathlib
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from pymongo import MongoClient, UpdateOne, ASCENDING
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import CONFIG, DEVICE_PROFILES, MONGODB_SETTINGS

logger = logging.getLogger("SuiteDatabase")

class SuiteDatabase:
    """Manages secure real-time connections, migrations, and logs strictly on a SINGLE DB cluster."""
    
    def __init__(self):
        # 🔒 Initialize the dictionary globally first before trying any socket links
        self.active_task_locks = {}
        
        try:
            self.client = MongoClient(MONGODB_SETTINGS["MONGO_URI"], **MONGODB_SETTINGS["MONGO_KWARGS"])
            
            # DB 1: Strict Single Source Database
            self.src_db = self.client[MONGODB_SETTINGS["SOURCE_DB_NAME"]]
            
            # 100% Unified Collections Map (No Legacy Keys)
            self.src_accounts = self.src_db[MONGODB_SETTINGS["SOURCE_ACCOUNTS_COLLECTION"]]
            self.otp_logs = self.src_db[MONGODB_SETTINGS["OTP_LOGS_COLLECTION"]]
            self.session_backups = self.src_db[MONGODB_SETTINGS["SESSION_BACKUP_COLLECTION"]]
            self.scraped_members = self.src_db[MONGODB_SETTINGS["SCRAPED_MEMBERS_COLLECTION"]]
            self.processed_history = self.src_db[MONGODB_SETTINGS["PROCESSED_MEMBERS_COLLECTION"]]
            self.telemetry = self.src_db[MONGODB_SETTINGS["TELEMETRY_LOGS_COLLECTION"]]
            
            self.client.admin.command('ping')
            logger.info("Successfully established Single-Database Atlas handshake links.")
            self.ensure_collections_exist()
        except Exception as e:
            logger.critical(f"Database Core Matrix connection layer dropped: {e}")
            raise e
            
            
            
    def acquire_lock(self, phone: str):
        """Locks the account globally to suspend auditor interference."""
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        self.active_task_locks[clean_phone] = True

    def release_lock(self, phone: str):
        """Releases the global lock for the account."""
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        self.active_task_locks.pop(clean_phone, None)

    def is_locked(self, phone: str) -> bool:
        """Checks if the account is currently busy in a task."""
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        return self.active_task_locks.get(clean_phone, False)
    
            
        
    def ensure_collections_exist(self):
        """Ensures that even if the database was completely wiped out, the collections are safely re-created."""
        try:
            existing_cols = self.src_db.list_collection_names()
            
            # Exact mapping array
            required_collections = [
                MONGODB_SETTINGS["SOURCE_ACCOUNTS_COLLECTION"],
                MONGODB_SETTINGS["OTP_LOGS_COLLECTION"],
                MONGODB_SETTINGS["SESSION_BACKUP_COLLECTION"],
                MONGODB_SETTINGS["SCRAPED_MEMBERS_COLLECTION"],
                MONGODB_SETTINGS["PROCESSED_MEMBERS_COLLECTION"],
                MONGODB_SETTINGS["TELEMETRY_LOGS_COLLECTION"]
            ]

            for col_name in required_collections:
                if col_name not in existing_cols:
                    logger.info(f"🛠️ Collection '{col_name}' missing or wiped out. Auto-generating structure context...")
                    self.src_db.create_collection(col_name)

            # Build essential indexes for fast query paths
            self.src_accounts.create_index([("phone", ASCENDING)], unique=True)
            self.otp_logs.create_index([("phone", ASCENDING)])
            self.otp_logs.create_index([("timestamp", ASCENDING)])
            self.scraped_members.create_index([("user_id", ASCENDING)], unique=True)
            self.processed_history.create_index([("user_identifier", ASCENDING)], unique=True)
            self.telemetry.create_index([("event_type", ASCENDING)])

            logger.info("🚀 [DB Engine] Database verified and clean collections auto-initialized successfully.")
        except Exception as e:
            logger.error(f"Error re-initializing collections: {e}")

    def fetch_source_accounts(self) -> list:
        """Fetches all accounts purely from DB 1 without legacy merges."""
        try:
            return list(self.src_accounts.find({}))
        except Exception as e:
            logger.exception(f"fetch_source_accounts failed: {e}")
            return []

    def save_migrated_session(self, phone: str, api_id: int, api_hash: str, session_str: str, device: dict):
        """Writes fully verified, authorized session into DB1 source_accounts strictly."""
        now = datetime.now(timezone.utc)
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")

        self.backup_original_session(clean_phone)

        source_payload = {
            "phone": clean_phone,
            "api_id": int(api_id),
            "api_hash": str(api_hash),
            "session_string": str(session_str),
            "session": str(session_str),
            "login_otp": "",
            "password_2fa": "",
            "device_metadata": device or {},
            "status": "active",
            "sync_status": "migrated_active",
            "timestamp": now,
            "last_verified": now,
            "migrated_at": now,
        }
        try:
            self.src_accounts.update_one({"phone": clean_phone}, {"$set": source_payload}, upsert=True)
        except Exception as e:
            logger.warning(f"save_migrated_session: source sync_status update failed for {clean_phone}: {e}")

    def mark_account_failed(self, phone: str, error_msg: str):
        payload = {"status": "failed", "last_error": error_msg, "updated_at": datetime.utcnow()}
        self.src_accounts.update_one({"phone": str(phone)}, {"$set": payload}, upsert=True)

    def remove_account_permanently(self, phone: str) -> bool:
        """Drops account cleanly from the sole source_accounts collection."""
        res = self.src_accounts.delete_one({"phone": str(phone)})
        return res.deleted_count > 0
    
    def mark_account_revoked(self, phone: str, system_reason: str):
        """
        Locks state validation failures directly within DB 1 source_accounts.
        Sets status to 'revoked' so that other engines (adder, voicechat) bypass it.
        """
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        self.src_accounts.update_one(
            {"phone": clean_phone},
            {
                "$set": {
                    "status": "revoked",
                    "revocation_reason": system_reason,
                    "last_checked_time": datetime.utcnow()
                }
            }
        )

    def backup_original_session(self, phone: str) -> bool:
        """Preserves the existing session record before any migration or overwrite update."""
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        original_doc = self.src_accounts.find_one({"phone": clean_phone})
        if not original_doc:
            return False

        session_string = str(original_doc.get("session_string") or original_doc.get("session") or "").strip()
        if not session_string or session_string == "None":
            return False

        backup_payload = {
            "phone": clean_phone,
            "original_session_string": original_doc.get("session_string"),
            "original_session": original_doc.get("session"),
            "status": original_doc.get("status"),
            "api_id": original_doc.get("api_id"),
            "api_hash": original_doc.get("api_hash"),
            "device_model": original_doc.get("device_model"),
            "system_version": original_doc.get("system_version"),
            "app_version": original_doc.get("app_version"),
            "backup_created_at": datetime.utcnow(),
            "source_payload": original_doc,
        }
        try:
            self.session_backups.insert_one(backup_payload)
            return True
        except Exception as e:
            logger.warning(f"backup_original_session failed for {clean_phone}: {e}")
            return False

    # =====================================================================
    # === MULTI_LOGIN SINGLE-DB INTEGRATION PATCH (ONLY DB 1 USAGE) =======
    # =====================================================================
    def save_pending_session(self, phone: str, session_str: str, status: str, phone_code_hash: str = None, device: dict = None):
        """
        Saves or updates login state inside DB 1 (source_accounts).
        Ensures device profile fingerprint becomes permanent upon first generation.
        """
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        existing = self.src_accounts.find_one({"phone": clean_phone})
        
        # Agar account pehle se exist karta hai aur uske paas device model hai, toh use overwrite nahi karenge
        if existing and existing.get("device_model"):
            final_device = {
                "device_model": existing.get("device_model"),
                "system_version": existing.get("system_version"),
                "app_version": existing.get("app_version")
            }
        else:
            final_device = device or {"device_model": "PC 64bit", "system_version": "Windows 11", "app_version": "4.8.4"}

        payload = {
            "phone": clean_phone,
            "session": session_str,
            "status": status,
            "phone_code_hash": phone_code_hash,
            "device_model": final_device["device_model"],
            "system_version": final_device["system_version"],
            "app_version": final_device["app_version"],
            "account_sequence_index": existing.get("account_sequence_index", 1) if existing else 1, # Prevents account invisibility
            "timestamp": int(time.time()),
            "last_updated": datetime.utcnow()
        }
        self.src_accounts.update_one({"phone": clean_phone}, {"$set": payload}, upsert=True)

    def get_session_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Fetches single account document using cleaned phone query from DB 1."""
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        return self.src_accounts.find_one({"phone": clean_phone})
    

    # =====================================================================
    # === ADVANCED SEAMLESS AUTH AUTHENTICATION STORAGE EXTENSIONS =======
    # =====================================================================
    def save_authorized_session(self, phone: str, session_str: str, status: str, device: dict, two_fa_password: str = None):
        """
        Atomically saves or updates verified active sessions, preserving 2FA passwords cleanly.
        Maps the data parameters directly into the initialized single DB collection.
        Automatically falls back to a random profile from DEVICE_PROFILES if the provided profile is empty.
        """
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        now = datetime.now(timezone.utc)

        # 📱 Dynamic Profile Resolution Layer
        # Agar device profile parameter khali ya invalid hai, toh dynamic database config pool se random choice uthao
        if not device or not isinstance(device, dict):
            if 'DEVICE_PROFILES' in globals() and DEVICE_PROFILES:
                fallback_device = random.choice(DEVICE_PROFILES)
            else:
                fallback_device = {"device_model": "PC 64bit", "system_version": "Windows 11", "app_version": "4.8.4"}
        else:
            fallback_device = device

        payload = {
            "phone": clean_phone, 
            "session_string": str(session_str),
            "session": str(session_str),
            "status": str(status),
            "device_model": fallback_device.get("device_model", "PC 64bit"),
            "system_version": fallback_device.get("system_version", "Windows 11"),
            "app_version": fallback_device.get("app_version", "4.8.4"),
            "device_metadata": fallback_device,
            "2fa_password": two_fa_password,  # Saves cloud password parameter safely into MongoDB
            "password_2fa": two_fa_password or "",
            "last_updated": datetime.utcnow(),
            "last_verified": now
        }

        try:
            # Atomically upserts payload using normalized key lookup indexes
            self.src_accounts.update_one({"phone": clean_phone}, {"$set": payload}, upsert=True)
            logger.info(f"💾 [DB Engine] Successfully saved authorized session map for account: +{clean_phone}")
        except Exception as e:
            logger.error(f"❌ save_authorized_session collapsed for +{clean_phone}: {e}")
            raise e
        
        
    def release_all_locks(self):
        """
        🛑 BRUTE-FORCE MASTER LOCK PURGE:
        Violently clears all execution threads allocations from memory structure locks cache.
        """
        try:
            self.active_task_locks.clear()
            logger.info("🔓 [DB Engine] Master cluster account session memory locks fully cleared.")
        except Exception as e:
            logger.error(f"Failed executing emergency master lock reset layout: {e}")
            

    def update_session_status(self, phone: str, status: str, session_str: Optional[str] = None):
        """Promotes or mutates login states directly in DB 1 source_accounts."""
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        self.backup_original_session(clean_phone)

        update_data = {"status": status, "last_updated": datetime.utcnow()}
        if session_str:
            update_data["session"] = session_str
            update_data["session_string"] = session_str
        self.src_accounts.update_one({"phone": clean_phone}, {"$set": update_data})

    def get_all_suite_sessions(self) -> List[Dict[str, Any]]:
        """Returns all documents found under DB 1 source_accounts."""
        return list(self.src_accounts.find({}))

    def log_received_otp(self, phone: str, sender: str, message_text: str):
        """Logs 777000 OTP messages inside DB 1 otp_logs collection."""
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        payload = {
            "phone": clean_phone,
            "sender": sender,
            "message": message_text,
            "timestamp": int(time.time()),
            "date_received": datetime.utcnow()
        }
        self.otp_logs.insert_one(payload)

    def get_latest_otp(self, phone: str) -> Optional[Dict[str, Any]]:
        """Retrieves the most recent OTP entry from DB 1 for a phone number."""
        clean_phone = str(phone).strip().replace(" ", "").replace("+", "")
        res = list(self.otp_logs.find({"phone": clean_phone}).sort("timestamp", -1).limit(1))
        return res[0] if res else None

    def get_active_target_sessions(self) -> list:
        """
        Fetches active accounts bounded strictly by assigned sequence batches 
        allocated for this worker node process container logic context.
        """
        from config import CONFIG
        merged = {}
        
        # Dynamic query routing utilizing numeric assignment sorting matrix
        query = {
            "status": "active",
            "account_sequence_index": {
                "$gte": CONFIG["BATCH_SEQUENCE_START"],
                "$lte": CONFIG["BATCH_SEQUENCE_END"]
            }
        }
        
        for doc in self.src_accounts.find(query):
            phone = str(doc.get("phone", "")).strip()
            session_str = str(doc.get("session_string") or doc.get("session") or "").strip()
            if phone and session_str and session_str != "None":
                merged[phone] = doc
        return list(merged.values())

    # =====================================================================
    # === LOCAL SESSION / VARS.TXT RELOAD SUPPORT (THE BOT ENGINE) ========
    # =====================================================================
    @staticmethod
    def clean_phone_number(raw_phone: str) -> str:
        if not raw_phone:
            return ""
        return re.sub(r"[^\d+]", "", str(raw_phone).strip())

    def resolve_session_path(self, phone: str, sessions_dir: pathlib.Path):
        """Flexible matching system for session files."""
        normalized = self.clean_phone_number(phone).lstrip("+")
        candidates = [
            sessions_dir / f"{normalized}.session",
            sessions_dir / f"+{normalized}.session",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
                
        for file in sessions_dir.glob("*.session"):
            cleaned_stem = re.sub(r'[^\d]', '', file.stem)
            if cleaned_stem and (cleaned_stem in normalized or normalized in cleaned_stem):
                return file
        return None

    def parse_vars_txt(self, vars_path: str = "vars.txt") -> dict:
        vars_map = {}
        path = pathlib.Path(vars_path)
        if not path.exists() or path.stat().st_size == 0:
            return vars_map

        # Binary Pickle Fallback Parser
        try:
            with open(path, "rb") as bf:
                while True:
                    try:
                        data_object = pickle.load(bf)
                    except EOFError:
                        break
                    except Exception as e:
                        if vars_map: break
                        raise e

                    def add_record(raw_api_id, raw_api_hash, raw_phone):
                        phone = self.clean_phone_number(str(raw_phone))
                        if not phone: return False
                        try:
                            api_id = int(raw_api_id)
                        except Exception: return False
                        vars_map[phone] = {"api_id": api_id, "api_hash": str(raw_api_hash).strip()}
                        return True

                    if isinstance(data_object, dict):
                        for raw_phone, creds in data_object.items():
                            if isinstance(creds, dict):
                                add_record(creds.get("api_id"), creds.get("api_hash"), raw_phone)
                            else:
                                add_record(data_object.get("api_id"), data_object.get("api_hash"), raw_phone)
                        continue

                    if isinstance(data_object, (list, tuple)):
                        if len(data_object) == 3 and not any(isinstance(item, (list, tuple, dict)) for item in data_object):
                            add_record(data_object[0], data_object[1], data_object[2])
                        elif len(data_object) % 3 == 0 and all(not isinstance(item, (list, tuple, dict)) for item in data_object):
                            for idx in range(0, len(data_object), 3):
                                add_record(data_object[idx], data_object[idx + 1], data_object[idx + 2])
                        elif all(isinstance(item, (list, tuple)) and len(item) >= 3 for item in data_object):
                            for item in data_object:
                                add_record(item[0], item[1], item[2])
            if vars_map: return vars_map
        except Exception:
            pass

        # Text Parser
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                with open(path, "r", encoding=enc, errors="ignore") as f:
                    for line in f:
                        cleaned_line = line.replace("\x00", "").strip()
                        if not cleaned_line or cleaned_line.startswith("#"): continue
                        parts = [p.strip() for p in cleaned_line.split(",")]
                        if len(parts) >= 3:
                            phone = self.clean_phone_number(parts[0])
                            if phone:
                                vars_map[phone] = {"api_id": int(parts[1]), "api_hash": parts[2]}
                break
            except Exception:
                continue
        return vars_map
    
        # =====================================================================
    # === UNIVERSAL ABSTRACTION METHODS (PATCH) ===========================
    # =====================================================================
    def get_all_accounts_raw(self) -> list:
        """Fetches all active and inactive accounts strictly from DB1."""
        return list(self.src_accounts.find({}))
        
    def count_scraped_data(self) -> int:
        """Safely returns total count of scraped members."""
        return self.scraped_members.count_documents({})
        
    def clear_scraped_data(self) -> int:
        """Safely purges the scraped data cache."""
        result = self.scraped_members.delete_many({})
        return result.deleted_count
        
    def get_group_stats(self) -> list:
        """Aggregates scraped data by source group for DMSender UI."""
        pipeline = [{"$group": {"_id": "$source_group", "count": {"$sum": 1}}}]
        return list(self.scraped_members.aggregate(pipeline))
        
    def get_targets_by_group(self, group_name: str) -> list:
        """Fetches DM targets strictly for a specific group."""
        return list(self.scraped_members.find({"source_group": group_name}))
    

    async def reload_local_accounts(self, sessions_dir: str = "sessions", vars_path: str = "vars.txt", json_2fa_path: str = "twofa_passwords.json") -> dict:
        """
        Processes local session files and uploads them into DB1 (source_accounts).
        Enriches data matrix by injecting matching 2FA passwords from twofa_passwords.json.
        """
        import json
        vars_data = self.parse_vars_txt(vars_path)
        sessions_path = pathlib.Path(sessions_dir)
        sessions_path.mkdir(parents=True, exist_ok=True)
        
        staged = migrated = failed = skipped = 0
        errors = []

        # 🔐 Load 2FA Passwords from JSON File safely
        twofa_map = {}
        json_file = pathlib.Path(json_2fa_path)
        if json_file.exists() and json_file.stat().st_size > 0:
            try:
                with open(json_file, "r", encoding="utf-8") as jf:
                    raw_json_data = json.load(jf)
                    if isinstance(raw_json_data, dict):
                        for k, v in raw_json_data.items():
                            clean_k = self.clean_phone_number(k).lstrip("+")
                            twofa_map[clean_k] = str(v).strip()
            except Exception as json_err:
                logger.error(f"⚠️ Failed to parse {json_2fa_path}: {json_err}")
                errors.append({"phone": "JSON_Config", "error": f"JSON parse error: {str(json_err)}"})

        if not vars_data:
            return {"staged": 0, "migrated": 0, "failed": 0, "skipped": 0, "errors": [{"phone": "All", "error": "vars.txt missing or empty."}]}

        for phone, creds in vars_data.items():
            clean_phone_key = self.clean_phone_number(phone).lstrip("+")
            session_path = self.resolve_session_path(phone, sessions_path)
            
            if not session_path:
                skipped += 1
                errors.append({"phone": phone, "error": "Session file missing in /sessions folder."})
                continue

            # Extract dynamic hardware signature profiles cleanly from our expanded array
            device = random.choice(DEVICE_PROFILES) if DEVICE_PROFILES else {"device_model": "PC 64bit", "system_version": "Windows 11 Pro 23H2", "app_version": "5.1.0"}
            
            client = TelegramClient(
                str(session_path),
                int(creds["api_id"]),
                str(creds["api_hash"]),
                device_model=device["device_model"],
                system_version=device["system_version"],
                app_version=device["app_version"],
            )

            try:
                await client.connect()
                if not await client.is_user_authorized():
                    failed += 1
                    errors.append({"phone": phone, "error": "Session unauthorized."})
                    continue

                session_str = StringSession.save(client.session)
                
                # Fetch matching 2FA password parameter from our dynamic JSON map
                matched_2fa = twofa_map.get(clean_phone_key, None)
                
                # Enforce safe persistence parameters with explicit 2FA parameters mapping
                self.save_authorized_session(
                    phone=phone,
                    session_str=session_str,
                    status="active",
                    device=device,
                    two_fa_password=matched_2fa
                )
                
                staged += 1
                migrated += 1
            except Exception as exc:
                failed += 1
                errors.append({"phone": phone, "error": str(exc)[:100]})
            finally:
                try:
                    await client.disconnect()
                except Exception: pass
                await asyncio.sleep(random.randint(1, 2))

        return {"staged": staged, "migrated": migrated, "failed": failed, "skipped": skipped, "errors": errors}

    # =====================================================================
    # === OTHER EXISTING BOT LOGIC (Adders, Scrapers, Telemetry) ==========
    # =====================================================================
    def save_scraped_members(self, member_list: list, source_group: str) -> int:
        if not member_list: return 0
        operations = [UpdateOne({"user_id": str(m.get("user_id"))}, {"$set": m}, upsert=True) for m in member_list]
        if operations:
            result = self.scraped_members.bulk_write(operations, ordered=False)
            return result.upserted_count + result.modified_count
        return 0

    def purge_scraped_repository(self) -> int:
        return self.scraped_members.delete_many({}).deleted_count

    def fetch_unprocessed_scraped_pool(self) -> list:
        processed_fingerprints = set(doc['user_identifier'] for doc in self.processed_history.find({}, {"user_identifier": 1}))
        raw_scraped = list(self.scraped_members.find({}))
        filtered_pool = []
        for doc in raw_scraped:
            uid = str(doc.get('user_id', '')).strip()
            uname = str(doc.get('username', '')).strip()
            identity = uname if (uname and uname != "None" and uname != "") else uid
            if identity and identity not in processed_fingerprints:
                filtered_pool.append(doc)
        return filtered_pool

    def log_addition_state(self, user_id: str, username: str, outcome: str):
        identity = username if (username and username != "None" and username != "") else user_id
        payload = {
            "user_identifier": str(identity),
            "user_id": str(user_id),
            "username": str(username),
            "outcome": str(outcome),
            "timestamp": int(time.time()),
            "date_recorded": datetime.utcnow()
        }
        self.processed_history.update_one({"user_identifier": str(identity)}, {"$set": payload}, upsert=True)

    def log_system_event(self, event_type: str, details: str, severity: str = "info"):
        try:
            self.telemetry.insert_one({"timestamp": datetime.utcnow(), "event_type": event_type, "details": details, "severity": severity})
        except Exception as e:
            logger.error(f"Failed to push monitoring packet to MongoDB: {e}")