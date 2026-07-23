#!/usr/bin/env python3
"""
Ultimate Enterprise Telegram Suite - DM Sender Engine (Database Integrated)
Filename: dmsender.py

REFACTOR v2.0 — Enterprise-Grade Production DM Engine
--------------------------------------------------------
- Multi-field entity resolution (user_id + access_hash + username + phone)
- Anti-ban with intelligent rate limiting
- Persistent task queue with checkpointing
- Bounded concurrency with semaphores
- Account health monitoring & automatic rotation
- LRU + TTL caching layers
- Structured logging with metrics
- Batched database operations
- Graceful shutdown / restart
"""

import os
import re
import asyncio
import logging
import random
import gc
import time
import json
import hashlib
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Set, Callable, Union
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from weakref import WeakSet, WeakValueDictionary

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import (
    DocumentAttributeAudio, InputPeerUser, InputPeerChannel, InputPeerChat,
    PeerUser, PeerChannel, PeerChat, User as TelethonUser
)
from telethon.tl.functions.contacts import ResolveUsernameRequest, GetContactsRequest
from telethon.tl.functions.users import GetUsersRequest
from telethon.errors import (
    PeerIdInvalidError, FloodWaitError, UserBannedInChannelError,
    UserDeactivatedError, AuthKeyUnregisteredError, SessionRevokedError,
    UserIsBlockedError, UserPrivacyRestrictedError, UserDeactivatedBanError,
    UsernameNotOccupiedError, InputUserDeactivatedError,
    AuthKeyDuplicatedError, PhoneNumberBannedError,
)
from telethon import utils as telethon_utils
from pymongo import MongoClient, UpdateOne, DeleteOne

from config import CONFIG, DEVICE_PROFILES
from proxy_manager import RobustProxyManager, ProxyEntry

logger = logging.getLogger("DMSenderEngine")

ADMIN_ID = os.environ.get("ADMIN_ID")

# =====================================================================
# CONSTANTS
# =====================================================================
MAX_ENTITY_CACHE_SIZE = 200
MAX_USERNAME_CACHE_SIZE = 500
DEFAULT_TTL_CACHE = 1800        # 30 minutes
ENTITY_RESOLVE_TTL = 3600       # 1 hour
DEFAULT_BATCH_SIZE = 25
MAX_CONCURRENT_SENDS = 15       # Per-account semaphore
MAX_CONCURRENT_CONNECTIONS = 8  # Global connection semaphore
HEALTH_CHECK_INTERVAL = 300     # 5 minutes
IDLE_CLEANUP_INTERVAL = 600     # 10 minutes
METRICS_LOG_INTERVAL = 60       # 1 minute
ACCOUNT_HOURLY_CAP = 40         # Added missing constant

# Anti-ban safe limits
ACCOUNT_DAILY_CAP_SOFT = 80     # Soft cap per account per day
ACCOUNT_DAILY_CAP_HARD = 120    # Hard cap per account per day
MIN_DELAY_BETWEEN_MSGS = 3.0    # Minimum seconds between messages (same account)
MAX_DELAY_BETWEEN_MSGS = 8.0    # Maximum seconds between messages (same account)
MICRO_BREAK_INTERVAL = 15       # Micro-break every N messages
MICRO_BREAK_DURATION = (30, 90) # Micro-break duration range (seconds)
FLOOD_WAIT_BACKOFF_MULTIPLIER = 1.5
MAX_FLOOD_RECOVER_WAIT = 7200   # Max 2 hours flood wait


# =====================================================================
# ENUMS & DATA CLASSES
# =====================================================================
class DeliveryStatus(Enum):
    PENDING = auto()
    QUEUED = auto()
    SENDING = auto()
    DELIVERED = auto()
    FAILED = auto()
    RETRYING = auto()
    DEAD_LETTER = auto()
    SKIPPED = auto()

class AccountState(Enum):
    ACTIVE = auto()
    COOLDOWN = auto()
    FLOODED = auto()
    BANNED = auto()
    REVOKED = auto()
    LIMITED = auto()

@dataclass
class DeliveryRecord:
    """Immutable delivery record for checkpointing."""
    target_user_id: Optional[int] = None
    target_username: Optional[str] = None
    target_phone: Optional[str] = None
    access_hash: Optional[int] = None
    original_index: int = 0
    status: DeliveryStatus = DeliveryStatus.PENDING
    account_phone: Optional[str] = None
    attempts: int = 0
    last_error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    delivered_at: Optional[datetime] = None
    checkpoint_id: Optional[str] = None  # For resume support

    def to_dict(self) -> dict:
        d = asdict(self)
        d['status'] = self.status.name
        d['timestamp'] = self.timestamp.isoformat()
        if self.delivered_at:
            d['delivered_at'] = self.delivered_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'DeliveryRecord':
        d['status'] = DeliveryStatus[d['status']]
        if isinstance(d['timestamp'], str):
            d['timestamp'] = datetime.fromisoformat(d['timestamp'])
        if d.get('delivered_at') and isinstance(d['delivered_at'], str):
            d['delivered_at'] = datetime.fromisoformat(d['delivered_at'])
        return cls(**d)

    @property
    def checkpoint_key(self) -> str:
        """Unique identifier for checkpoint storage."""
        raw = f"{self.target_user_id or ''}|{self.target_username or ''}|{self.original_index}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

@dataclass
class AccountMetrics:
    """Per-account runtime metrics."""
    phone: str
    state: AccountState = AccountState.ACTIVE
    proxy_entry: Optional[ProxyEntry] = None
    proxy_manager: Optional[RobustProxyManager] = None
    proxy_country: str = ""
    total_sent: int = 0
    total_failed: int = 0
    consecutive_failures: int = 0
    flood_waits: int = 0
    last_flood_wait: float = 0.0
    last_activity: float = 0.0
    daily_count: int = 0
    daily_reset: datetime = field(default_factory=datetime.utcnow)
    hourly_count: int = 0
    hourly_reset: datetime = field(default_factory=datetime.utcnow)
    cooldown_until: float = 0.0
    is_connected: bool = False
    api_id: int = 0
    api_hash: str = ""
    device: dict = field(default_factory=dict)
    session_string: str = ""
    _client: Optional[TelegramClient] = None
    db: Any = None

    def can_send(self) -> bool:
        now = time.monotonic()
        if self.state == AccountState.BANNED or self.state == AccountState.REVOKED:
            return False
        if self.state == AccountState.COOLDOWN and now < self.cooldown_until:
            return False
        if self.state == AccountState.FLOODED and now < self.cooldown_until:
            return False
        if self.daily_count >= ACCOUNT_DAILY_CAP_HARD:
            return False
        if self.hourly_count >= ACCOUNT_HOURLY_CAP:
            return False
        return True

    def record_send(self):
        self.total_sent += 1
        self.daily_count += 1
        self.hourly_count += 1
        self.consecutive_failures = 0
        self.last_activity = time.monotonic()
        now_utc = datetime.utcnow()
        if now_utc.date() > self.daily_reset.date():
            self.daily_count = 0
            self.daily_reset = now_utc
        if now_utc.hour != self.hourly_reset.hour or now_utc.date() > self.hourly_reset.date():
            self.hourly_count = 0
            self.hourly_reset = now_utc

    def record_failure(self, error: str):
        self.total_failed += 1
        self.consecutive_failures += 1
        self.last_activity = time.monotonic()
        err_lower = error.lower()
        if any(x in err_lower for x in ["banned", "deactivated", "unregistered", "revoked"]):
            self.state = AccountState.BANNED
        elif self.consecutive_failures >= 5:
            self.state = AccountState.LIMITED
            self.cooldown_until = time.monotonic() + 3600

    def mark_flooded(self, seconds: int):
        self.flood_waits += 1
        self.last_flood_wait = time.monotonic()
        wait = min(seconds * FLOOD_WAIT_BACKOFF_MULTIPLIER, MAX_FLOOD_RECOVER_WAIT)
        self.cooldown_until = time.monotonic() + wait + random.uniform(10, 60)
        self.state = AccountState.FLOODED

    def recover(self):
        if self.state == AccountState.FLOODED:
            self.state = AccountState.COOLDOWN
            self.cooldown_until = time.monotonic() + 300
        elif self.state in (AccountState.COOLDOWN, AccountState.LIMITED):
            self.state = AccountState.ACTIVE
            self.consecutive_failures = 0

    async def get_client(self) -> Optional[TelegramClient]:
        if self._client and self._client.is_connected():
            return self._client

        # 🔥 Proxy management with region preference
        proxy_entry = self.proxy_entry
        if proxy_entry and hasattr(proxy_entry, 'is_dead') and proxy_entry.is_dead:
            self.proxy_entry = None
            proxy_entry = None

        if not proxy_entry and self.proxy_manager:
            # Try to get proxy from the same country first
            if self.proxy_country:
                proxy_entry = self.proxy_manager.get_proxy_by_preference(self.proxy_country)
            if not proxy_entry:
                proxy_entry = self.proxy_manager.get_proxy("socks5") or self.proxy_manager.get_proxy("any")
            if proxy_entry:
                self.proxy_entry = proxy_entry
                self.proxy_country = getattr(proxy_entry, 'country', '') or ""
                logger.debug(f"[{self.phone}] Assigned proxy {getattr(proxy_entry, 'host', '')}:{getattr(proxy_entry, 'port', '')} (country={self.proxy_country})")
            else:
                logger.warning(f"[{self.phone}] No proxy available, using direct.")
                self.record_failure("Proxy Pool Empty - IP Protected")
                return None

        proxy_dict = proxy_entry.dict if proxy_entry and hasattr(proxy_entry, 'dict') else None

        try:
            client = TelegramClient(
                StringSession(self.session_string),
                self.api_id,
                self.api_hash,
                device_model=self.device.get("device_model", "PC 64bit"),
                system_version=self.device.get("system_version", "Windows 11"),
                app_version=self.device.get("app_version", "4.8.4"),
                entity_cache_limit=30,
                sequential_updates=False,
                proxy=proxy_dict,
            )
            await client.connect()
            if not await client.is_user_authorized():
                self.state = AccountState.REVOKED
                return None
            self._client = client
            self.is_connected = True
            if self.proxy_manager and self.db and proxy_entry:
                self.db.update_account_proxy(self.phone, proxy_entry.dict)
            return client
        except Exception as e:
            logger.error(f"[{self.phone}] Client creation failed: {e}")
            self.record_failure(str(e))
            if proxy_entry and hasattr(proxy_entry, 'record_failure'):
                proxy_entry.record_failure()
                self.proxy_entry = None
                self.proxy_country = ""   # reset country
            return None

# ... rest of the code remains unchanged ...


# =====================================================================
# CACHE LAYER
# =====================================================================
class LRUCache:
    """Generic LRU cache with TTL support."""
    def __init__(self, maxsize: int = 200, ttl: int = DEFAULT_TTL_CACHE):
        self._maxsize = maxsize
        self._ttl = ttl
        self._cache: OrderedDict = OrderedDict()
        self._timestamps: Dict[str, float] = {}

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        # Check TTL
        if time.monotonic() - self._timestamps.get(key, 0) > self._ttl:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def set(self, key: str, value: Any):
        self._cache[key] = value
        self._timestamps[key] = time.monotonic()
        self._cache.move_to_end(key)
        if len(self._cache) > self._maxsize:
            oldest = next(iter(self._cache))
            self._cache.pop(oldest, None)
            self._timestamps.pop(oldest, None)

    def remove(self, key: str):
        self._cache.pop(key, None)
        self._timestamps.pop(key, None)

    def clear(self):
        self._cache.clear()
        self._timestamps.clear()

    def __len__(self) -> int:
        return len(self._cache)


# =====================================================================
# ENTITY RESOLVER
# =====================================================================
class EntityResolver:
    """
    Multi-field entity resolution engine.
    Resolves user by: username → user_id+access_hash → phone → @username
    with aggressive caching.
    """
    def __init__(self):
        self._entity_cache = LRUCache(maxsize=MAX_ENTITY_CACHE_SIZE, ttl=ENTITY_RESOLVE_TTL)
        self._username_cache = LRUCache(maxsize=MAX_USERNAME_CACHE_SIZE, ttl=ENTITY_RESOLVE_TTL)
        self._stats = {"resolved": 0, "cache_hits": 0, "failed": 0}

    async def resolve(
        self,
        client: TelegramClient,
        target: Union[dict, str, int],
        phone_contacts: Optional[Set[str]] = None
    ) -> Optional[Any]:
        """
        Resolve a target to a Telegram entity using all available fields.
        Fallback chain: username → (user_id + access_hash) → phone → ID-only
        """
        if isinstance(target, (str, int)):
            return await self._resolve_simple(client, target)

        user_id = target.get("user_id")
        access_hash = target.get("access_hash")
        username = target.get("username")
        phone = target.get("phone")

        # Try username first (fastest, works for any account)
        if username and str(username).strip() and str(username).lower() not in ("none", "null", ""):
            cache_key = f"un:{username}"
            cached = self._username_cache.get(cache_key)
            if cached:
                self._stats["cache_hits"] += 1
                return cached
            try:
                u_str = str(username).strip()
                if not u_str.startswith("@"):
                    u_str = f"@{u_str}"
                entity = await client.get_entity(u_str)
                self._username_cache.set(cache_key, entity)
                self._stats["resolved"] += 1
                return entity
            except (ValueError, UsernameNotOccupiedError, PeerIdInvalidError):
                pass

        # Try user_id + access_hash (fast if we have both)
        if user_id and access_hash and str(access_hash) not in ("0", "None", ""):
            cache_key = f"peer:{user_id}:{access_hash}"
            cached = self._entity_cache.get(cache_key)
            if cached:
                self._stats["cache_hits"] += 1
                return cached
            try:
                peer = InputPeerUser(int(user_id), int(access_hash))
                entity = await client.get_entity(peer)
                self._entity_cache.set(cache_key, entity)
                self._stats["resolved"] += 1
                return entity
            except Exception:
                pass

        # Try phone number (if in contacts)
        if phone and phone_contacts and str(phone).strip():
            clean_phone = str(phone).strip().replace("+", "")
            if clean_phone in phone_contacts:
                try:
                    entity = await client.get_entity(f"+{clean_phone}")
                    if entity:
                        # Cache with username if available
                        if hasattr(entity, 'username') and entity.username:
                            self._username_cache.set(f"un:{entity.username}", entity)
                        self._stats["resolved"] += 1
                        return entity
                except Exception:
                    pass

        # Try user_id only (blind ID resolution)
        if user_id and str(user_id).strip() not in ("0", "None", ""):
            try:
                blind_peer = InputPeerUser(int(user_id), access_hash=0)
                entity = await client.get_entity(blind_peer)
                self._stats["resolved"] += 1
                return entity
            except Exception:
                pass

            # Last resort: try as PeerUser with GetUsersRequest
            try:
                result = await client(GetUsersRequest([InputPeerUser(int(user_id), 0)]))
                if result and not isinstance(result[0], type(None)):
                    self._stats["resolved"] += 1
                    return result[0]
            except Exception:
                pass

        self._stats["failed"] += 1
        return None

    async def _resolve_simple(self, client: TelegramClient, target: Union[str, int]) -> Optional[Any]:
        """Resolve a simple string or int target."""
        cache_key = f"simple:{target}"
        cached = self._entity_cache.get(cache_key)
        if cached:
            self._stats["cache_hits"] += 1
            return cached
        try:
            entity = await client.get_entity(target)
            self._entity_cache.set(cache_key, entity)
            self._stats["resolved"] += 1
            return entity
        except Exception:
            self._stats["failed"] += 1
            return None

    def get_stats(self) -> dict:
        return dict(self._stats)


# =====================================================================
# CHECKPOINT MANAGER
# =====================================================================
class CheckpointManager:
    """
    Persistent checkpointing for campaign resume support.
    Stores delivery progress in database.
    """
    def __init__(self, db, campaign_id: str):
        self.db = db
        self.campaign_id = campaign_id
        self._checkpoints: Dict[str, DeliveryRecord] = {}
        self._dirty = False

    def save_record(self, record: DeliveryRecord):
        key = record.checkpoint_key
        self._checkpoints[key] = record
        self._dirty = True

    def get_record(self, key: str) -> Optional[DeliveryRecord]:
        return self._checkpoints.get(key)

    def has_been_delivered(self, target_idx: int, target_username: Optional[str] = None,
                           target_user_id: Optional[int] = None) -> bool:
        """Check if a target was already successfully delivered."""
        for rec in self._checkpoints.values():
            if rec.status != DeliveryStatus.DELIVERED:
                continue
            if rec.original_index == target_idx:
                return True
            if target_username and rec.target_username == target_username:
                return True
            if target_user_id and rec.target_user_id == target_user_id:
                return True
        return False

    def get_pending_count(self) -> int:
        return sum(1 for r in self._checkpoints.values()
                   if r.status not in (DeliveryStatus.DELIVERED, DeliveryStatus.SKIPPED, DeliveryStatus.DEAD_LETTER))

    def flush_to_db(self):
        """Persist to database (batch)."""
        if not self._dirty:
            return
        try:
            collection = self.db.src_db["campaign_checkpoints"]
            batch = []
            for record in self._checkpoints.values():
                doc = record.to_dict()
                doc["campaign_id"] = self.campaign_id
                doc["_checkpoint_key"] = record.checkpoint_key
                batch.append(UpdateOne(
                    {"campaign_id": self.campaign_id, "_checkpoint_key": record.checkpoint_key},
                    {"$set": doc},
                    upsert=True
                ))
            if batch:
                collection.bulk_write(batch)
            self._dirty = False
        except Exception as e:
            logger.error(f"Checkpoint flush failed: {e}")

    @classmethod
    def load_from_db(cls, db, campaign_id: str) -> 'CheckpointManager':
        mgr = cls(db, campaign_id)
        try:
            collection = db.src_db["campaign_checkpoints"]
            for doc in collection.find({"campaign_id": campaign_id}):
                doc.pop("campaign_id", None)
                doc.pop("_id", None)
                key = doc.pop("_checkpoint_key", None)
                record = DeliveryRecord.from_dict(doc)
                if key:
                    mgr._checkpoints[key] = record
        except Exception as e:
            logger.warning(f"Checkpoint load failed (first run?): {e}")
        return mgr

    @classmethod
    def cleanup_old_campaigns(cls, db, max_age_hours: int = 48):
        """Remove old checkpoint data."""
        try:
            cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
            db.src_db["campaign_checkpoints"].delete_many({"timestamp": {"$lt": cutoff.isoformat()}})
        except Exception:
            pass


# =====================================================================
# ACCOUNT POOL
# =====================================================================
class AccountPool:
    """
    Manages account lifecycle, health checks, and connection pooling.
    Uses bounded concurrency and automatic recovery.
    """
    def __init__(self, db):
        self.db = db
        self._accounts: Dict[str, AccountMetrics] = {}
        self._connection_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONNECTIONS)
        self._health_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._contact_cache: Optional[Set[str]] = None  # Phone numbers in contacts
        self._contact_cache_time: float = 0
        self._lock = asyncio.Lock()
        self._entity_resolver = EntityResolver()

    async def initialize(self, all_accounts: List[dict], proxy_manager: Optional[RobustProxyManager] = None):
        """Initialize pool from database documents."""
        for acc in all_accounts:
            phone = str(acc.get("phone", "")).strip()
            if not phone:
                continue
            if phone in self._accounts:
                continue
            device = acc.get("device_metadata") or acc.get("device_fingerprint") or random.choice(DEVICE_PROFILES)
            account = AccountMetrics(
                phone=phone,
                api_id=int(acc.get("api_id", CONFIG["API_ID"])),
                api_hash=str(acc.get("api_hash", CONFIG["API_HASH"])),
                session_string=str(acc.get("session_string") or acc.get("session", "")),
                device=device if isinstance(device, dict) else random.choice(DEVICE_PROFILES),
                proxy_manager=proxy_manager,
                db=self.db,
                state=AccountState.ACTIVE,
            )
            stored_proxy = acc.get("proxy")
            if stored_proxy:
                try:
                    account.proxy_entry = ProxyEntry(
                        host=stored_proxy.get("addr"),
                        port=int(stored_proxy.get("port")),
                        protocol=stored_proxy.get("proxy_type", "socks5"),
                        username=stored_proxy.get("username") or None,
                        password=stored_proxy.get("password") or None,
                        is_working=True,
                    )
                except (AttributeError, TypeError, ValueError):
                    logger.warning(f"[AccountPool] Ignoring invalid stored proxy for {phone}")
            self._accounts[phone] = account

        # Start background tasks
        self._health_task = asyncio.create_task(self._health_loop())
        self._cleanup_task = asyncio.create_task(self._idle_cleanup_loop())
        logger.info(f"[AccountPool] Initialized {len(self._accounts)} accounts")

    @property
    def active_accounts(self) -> List[AccountMetrics]:
        return [a for a in self._accounts.values() if a.can_send()]

    @property
    def total_accounts(self) -> int:
        return len(self._accounts)

    def get_account_by_phone(self, phone: str) -> Optional[AccountMetrics]:
        return self._accounts.get(phone)

    def get_ready_accounts(self) -> List[AccountMetrics]:
        """Get accounts that can send right now."""
        ready = [a for a in self._accounts.values() if a.can_send()]
        # Sort by: least total_sent first (distribute load), then least daily_count
        ready.sort(key=lambda a: (a.total_sent, a.daily_count, a.consecutive_failures))
        return ready

    async def acquire_client(self, account: AccountMetrics) -> Optional[TelegramClient]:
        """Thread-safe client acquisition with connection semaphore."""
        async with self._connection_semaphore:
            client = await account.get_client()
            return client

    async def refresh_contacts(self, client: TelegramClient):
        """Fetch phone contacts for phone-number based entity resolution."""
        now = time.monotonic()
        if self._contact_cache and (now - self._contact_cache_time) < 600:
            return
        try:
            contacts = await client(GetContactsRequest(0))
            self._contact_cache = set()
            for user in contacts.users:
                if hasattr(user, 'phone') and user.phone:
                    self._contact_cache.add(user.phone)
            self._contact_cache_time = now
            logger.debug(f"[AccountPool] Cached {len(self._contact_cache)} contact phones")
        except Exception as e:
            logger.warning(f"[AccountPool] Contact refresh failed: {e}")

    def get_contact_set(self) -> Set[str]:
        return self._contact_cache or set()

    async def resolve_entity(self, client: TelegramClient, target: Union[dict, str, int]) -> Optional[Any]:
        """Resolve entity using the pool's resolver."""
        return await self._entity_resolver.resolve(client, target, self._contact_cache)

    async def _health_loop(self):
        """Periodic health check for all accounts."""
        while True:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            try:
                now = time.monotonic()
                for account in list(self._accounts.values()):
                    # Recover flooded accounts after cooldown expires
                    if account.state in (AccountState.FLOODED, AccountState.COOLDOWN, AccountState.LIMITED):
                        if now >= account.cooldown_until:
                            account.recover()
                            logger.info(f"[AccountPool] {account.phone} recovered from {account.state.name}")

                    # Disconnect idle clients (no activity for > 10 min)
                    if account._client and account.is_connected:
                        if now - account.last_activity > IDLE_CLEANUP_INTERVAL:
                            await account.disconnect_client()
                            logger.debug(f"[AccountPool] {account.phone} disconnected (idle)")
            except Exception as e:
                logger.error(f"[AccountPool] Health loop error: {e}")

    async def _idle_cleanup_loop(self):
        """Periodic cleanup of stale clients and cache."""
        while True:
            await asyncio.sleep(IDLE_CLEANUP_INTERVAL)
            try:
                # Force entity cache cleanup
                self._entity_resolver._entity_cache.clear()
                self._entity_resolver._username_cache.clear()

                # Force garbage collection
                gc.collect(0)
                gc.collect(1)

                logger.debug("[AccountPool] Cache & GC cleanup completed")
            except Exception as e:
                logger.error(f"[AccountPool] Cleanup error: {e}")

    async def shutdown_all(self):
        """Graceful shutdown of all accounts."""
        if self._health_task:
            self._health_task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        for account in list(self._accounts.values()):
            await account.disconnect_client()
        self._accounts.clear()
        logger.info("[AccountPool] All accounts shut down")

    def get_stats(self) -> dict:
        active = len([a for a in self._accounts.values() if a.state == AccountState.ACTIVE])
        cooldown = len([a for a in self._accounts.values() if a.state == AccountState.COOLDOWN])
        flooded = len([a for a in self._accounts.values() if a.state == AccountState.FLOODED])
        banned = len([a for a in self._accounts.values() if a.state in (AccountState.BANNED, AccountState.REVOKED)])
        total_sent = sum(a.total_sent for a in self._accounts.values())
        total_failed = sum(a.total_failed for a in self._accounts.values())
        return {
            "total": len(self._accounts),
            "active": active,
            "cooldown": cooldown,
            "flooded": flooded,
            "banned": banned,
            "total_sent": total_sent,
            "total_failed": total_failed,
            "entity_resolver": self._entity_resolver.get_stats(),
        }


# =====================================================================
# CAMPAIGN MANAGER
# =====================================================================
class CampaignManager:
    """
    Orchestrates DM campaigns with persistent queue, checkpointing,
    multi-field resolution, and anti-ban throttle.
    """
    def __init__(self, db, account_pool: AccountPool):
        self.db = db
        self.pool = account_pool
        self.is_running = False
        self._campaign_id: Optional[str] = None
        self._checkpoint: Optional[CheckpointManager] = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._dead_letter_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._workers: List[asyncio.Task] = []
        self._stats_lock = asyncio.Lock()
        self._campaign_start_time: float = 0.0
        self._last_metrics_log: float = 0.0
        self._total_targets: int = 0
        self._active_workers: int = 0
        self._message_template: str = ""
        self._media_path: Optional[str] = None
        self._ui_callback: Optional[Callable] = None
        self._accounts_down: int = 0

    async def start_campaign(
        self,
        target_list: list,
        message_text: str,
        media_path: Optional[str],
        limit: int,
        ui_callback: Callable,
    ):
        """Start a new DM campaign with full pipeline."""
        if self.is_running:
            await ui_callback("⚠️ A campaign is already running. Stop it first with /stop_dmsender")
            return

        # Generate unique campaign ID
        campaign_raw = f"{datetime.utcnow().isoformat()}_{random.randint(1000, 9999)}"
        self._campaign_id = hashlib.md5(campaign_raw.encode()).hexdigest()[:12]
        self._checkpoint = CheckpointManager(self.db, self._campaign_id)
        self._ui_callback = ui_callback
        self._campaign_start_time = time.monotonic()
        self._total_targets = min(limit, len(target_list)) if limit > 0 else len(target_list)
        self._message_template = message_text
        self._media_path = media_path
        self._accounts_down = 0
        self.is_running = True

        # Wait for at least one active account
        if not self.pool.active_accounts:
            await ui_callback("❌ **Campaign Aborted:** Koi active account nahi mila.")
            self.is_running = False
            return

        await ui_callback(
            f"🚀 **DM Campaign Started!**\n"
            f"📊 Targets: `{self._total_targets}` | Accounts: `{len(self.pool.active_accounts)}`\n"
            f"🆔 Campaign ID: `{self._campaign_id}`"
        )

        # Populate queue from checkpoint (skip already delivered)
        enqueued = 0
        for idx, target in enumerate(target_list[:self._total_targets]):
            if self._checkpoint.has_been_delivered(idx):
                continue

            record = DeliveryRecord(
                target_user_id=target.get("user_id") if isinstance(target, dict) else None,
                target_username=target.get("username") if isinstance(target, dict) else (str(target) if isinstance(target, str) else None),
                target_phone=target.get("phone") if isinstance(target, dict) else None,
                access_hash=target.get("access_hash") if isinstance(target, dict) else None,
                original_index=idx,
                status=DeliveryStatus.QUEUED,
            )
            self._checkpoint.save_record(record)
            await self._queue.put(record)
            enqueued += 1

        # Update UI with queue size
        await ui_callback(f"📦 **Queue Ready:** `{enqueued}` targets queued for delivery.")

        # Start worker pool (bounded concurrency)
        num_workers = min(len(self.pool.active_accounts), MAX_CONCURRENT_CONNECTIONS * 2)
        self._workers = []
        for i in range(num_workers):
            worker = asyncio.create_task(self._worker_loop(i))
            self._workers.append(worker)

        # Start checkpoint flusher
        flusher = asyncio.create_task(self._checkpoint_flush_loop())
        self._workers.append(flusher)

        # Wait for completion
        await asyncio.gather(*self._workers, return_exceptions=True)

        # Final cleanup
        self.is_running = False
        if self._checkpoint:
            self._checkpoint.flush_to_db()

        # Generate final report
        sent = sum(1 for r in self._checkpoint._checkpoints.values()
                   if r.status == DeliveryStatus.DELIVERED)
        failed = sum(1 for r in self._checkpoint._checkpoints.values()
                     if r.status in (DeliveryStatus.FAILED, DeliveryStatus.DEAD_LETTER))
        skipped = sum(1 for r in self._checkpoint._checkpoints.values()
                      if r.status == DeliveryStatus.SKIPPED)

        final_msg = (
            f"✅ **Campaign Completed** ✅\n"
            f"📨 Delivered: `{sent}/{self._total_targets}`\n"
            f"❌ Failed: `{failed}` | Skipped: `{skipped}`\n"
            f"⚡ Accounts Used: `{len(self.pool.active_accounts)}`\n"
            f"💀 Accounts Down: `{self._accounts_down}`\n"
            f"⏱ Duration: `{int((time.monotonic() - self._campaign_start_time) / 60)} min`\n"
            f"🆔 Campaign: `{self._campaign_id}`"
        )
        await ui_callback(final_msg)

        # Cleanup media
        if media_path and os.path.exists(str(media_path)):
            try:
                os.remove(str(media_path))
            except Exception:
                pass

    async def halt_campaign(self):
        """Immediate graceful halt."""
        if not self.is_running:
            return
        self.is_running = False
        # Cancel all workers
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        # Drain queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        if self._checkpoint:
            self._checkpoint.flush_to_db()
        logger.info(f"[Campaign] {self._campaign_id} halted by user")

    async def _worker_loop(self, worker_id: int):
        """Individual worker that picks targets and sends via best available account."""
        while self.is_running:
            try:
                record = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                if self._queue.empty() and self.is_running:
                    # Check if any records are still pending
                    if self._checkpoint and self._checkpoint.get_pending_count() == 0:
                        # All done, signal shutdown
                        remaining_empty = all(
                            worker.done() for worker in self._workers
                            if worker is not asyncio.current_task()
                        )
                        if remaining_empty:
                            break
                continue
            except asyncio.CancelledError:
                break

            if not self.is_running:
                break

            self._active_workers += 1
            await self._process_delivery(record, worker_id)
            self._active_workers -= 1
            self._queue.task_done()

    async def _process_delivery(self, record: DeliveryRecord, worker_id: int):
        """Process a single delivery record through the best available account."""
        if record.status == DeliveryStatus.DELIVERED:
            return
        
        if record.attempts >= 3:
            record.status = DeliveryStatus.DEAD_LETTER
            record.last_error = "Max delivery attempts reached."
            self._checkpoint.save_record(record)
            return

        record.status = DeliveryStatus.SENDING
        record.attempts += 1

        # Find best account for this delivery
        ready_accounts = self.pool.get_ready_accounts()
        if not ready_accounts:
            record.status = DeliveryStatus.DEAD_LETTER
            record.last_error = "No available accounts"
            self._checkpoint.save_record(record)
            await self._dead_letter_queue.put(record)
            return

        account = ready_accounts[0]
        record.account_phone = account.phone

        # Acquire client
        client = await self.pool.acquire_client(account)
        if not client:
            account.record_failure("Client acquisition failed")
            record.status = DeliveryStatus.FAILED
            record.last_error = "Client unavailable"
            self._checkpoint.save_record(record)
            await self._queue.put(record)  # Re-queue
            return

        try:
            # Resolve entity using multi-field resolver
            target_data = {
                "user_id": record.target_user_id,
                "access_hash": record.access_hash,
                "username": record.target_username,
                "phone": record.target_phone,
            }
            entity = await self.pool.resolve_entity(client, target_data)

            if not entity:
                # If entity not found, try get_input_entity with raw string
                if record.target_username:
                    try:
                        entity = await client.get_input_entity(
                            record.target_username if record.target_username.startswith("@")
                            else f"@{record.target_username}"
                        )
                    except Exception:
                        pass

            if not entity and record.target_user_id:
                try:
                    entity = InputPeerUser(int(record.target_user_id), access_hash=0)
                except Exception:
                    pass

            if not entity:
                # If still no entity, skip (permanent failure)
                record.status = DeliveryStatus.DEAD_LETTER
                record.last_error = "Entity resolution failed after all methods"
                self._checkpoint.save_record(record)
                account.record_failure("Entity resolution failed")
                return

            # Send message
            if self._media_path and os.path.exists(str(self._media_path)):
                is_voice = str(self._media_path).lower().endswith(('.ogg', '.mp3', '.m4a'))
                attributes = [DocumentAttributeAudio(voice=True)] if is_voice else None
                await client.send_file(
                    entity,
                    str(self._media_path),
                    caption=self._message_template or None,
                    voice_note=is_voice,
                    attributes=attributes,
                )
            else:
                await client.send_message(entity, self._message_template)

            # Success
            record.status = DeliveryStatus.DELIVERED
            record.delivered_at = datetime.utcnow()
            account.record_send()
            self._checkpoint.save_record(record)

            # Anti-ban: smart delay based on account stats and daily load
            base_delay = MIN_DELAY_BETWEEN_MSGS
            if account.total_sent > 30:
                base_delay += 1.0
            if account.daily_count > 50:
                base_delay += 2.0
            if account.daily_count > 80:
                base_delay += 3.0

            delay = random.uniform(base_delay, base_delay + MAX_DELAY_BETWEEN_MSGS - MIN_DELAY_BETWEEN_MSGS)

            # Micro-break every N messages
            if account.total_sent > 0 and account.total_sent % MICRO_BREAK_INTERVAL == 0:
                micro_delay = random.randint(*MICRO_BREAK_DURATION)
                logger.debug(f"[Worker {worker_id}] Micro-break for {account.phone}: {micro_delay}s")
                await asyncio.sleep(micro_delay)
            else:
                await asyncio.sleep(delay)

        except FloodWaitError as e:
            seconds = e.seconds
            logger.warning(f"[Worker {worker_id}] FloodWait {seconds}s for {account.phone}")
            account.mark_flooded(seconds)
            record.status = DeliveryStatus.RETRYING
            record.last_error = f"FloodWait {seconds}s"
            self._checkpoint.save_record(record)

            # Respect flood wait time (non-blocking for other workers)
            wait_time = min(seconds * FLOOD_WAIT_BACKOFF_MULTIPLIER, MAX_FLOOD_RECOVER_WAIT)
            if wait_time < 60:
                await asyncio.sleep(wait_time)
                await self._queue.put(record)  # Re-queue
            else:
                # Long flood: put back in queue with delay
                asyncio.create_task(self._delayed_requeue(record, wait_time))

        except (UserIsBlockedError, UserPrivacyRestrictedError):
            record.status = DeliveryStatus.SKIPPED
            record.last_error = "User blocked or privacy restricted"
            self._checkpoint.save_record(record)
            # Don't penalize the account for target-side restrictions

        except (PeerIdInvalidError, ValueError) as e:
            # Try one more time with direct ID resolution
            if record.target_user_id and record.attempts < 2:
                try:
                    peer = InputPeerUser(int(record.target_user_id), 0)
                    if self._media_path and os.path.exists(str(self._media_path)):
                        await client.send_file(peer, str(self._media_path), caption=self._message_template or None)
                    else:
                        await client.send_message(peer, self._message_template)
                    record.status = DeliveryStatus.DELIVERED
                    record.delivered_at = datetime.utcnow()
                    account.record_send()
                    self._checkpoint.save_record(record)
                    return
                except Exception:
                    pass
            record.status = DeliveryStatus.FAILED
            record.last_error = f"PeerIdInvalid: {e}"[:80]
            self._checkpoint.save_record(record)
            account.record_failure(str(e))

        except (UserDeactivatedError, UserDeactivatedBanError, InputUserDeactivatedError):
            record.status = DeliveryStatus.SKIPPED
            record.last_error = "Target account deactivated"
            self._checkpoint.save_record(record)

        except (AuthKeyUnregisteredError, SessionRevokedError, AuthKeyDuplicatedError,
                PhoneNumberBannedError) as e:
            # Account is dead
            self._accounts_down += 1
            account.state = AccountState.REVOKED
            try:
                self.db.mark_account_revoked(record.account_phone, f"Runtime Drop: {str(e)[:40]}")
            except Exception:
                pass
            record.status = DeliveryStatus.FAILED
            record.last_error = f"Account revoked: {type(e).__name__}"
            self._checkpoint.save_record(record)
            # Re-queue for another account
            if record.attempts < 3:
                record.account_phone = None
                await self._queue.put(record)

        except Exception as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ["banned", "deactivated", "unregistered", "revoked", "mute"]):
                self._accounts_down += 1
                account.state = AccountState.BANNED
                try:
                    self.db.mark_account_revoked(record.account_phone, f"Runtime Drop: {err_str[:40]}")
                except Exception:
                    pass
                record.status = DeliveryStatus.FAILED
                record.last_error = f"Account banned: {err_str[:40]}"
                self._checkpoint.save_record(record)
                # Re-queue for another account
                if record.attempts < 3:
                    record.account_phone = None
                    await self._queue.put(record)
            elif record.attempts < 3:
                # Transient error, retry
                account.record_failure(str(e))
                record.status = DeliveryStatus.RETRYING
                record.last_error = str(e)[:60]
                self._checkpoint.save_record(record)
                await asyncio.sleep(random.uniform(2, 5))
                await self._queue.put(record)
            else:
                # Max retries exceeded
                record.status = DeliveryStatus.DEAD_LETTER
                record.last_error = f"Max retries exceeded: {str(e)[:60]}"
                self._checkpoint.save_record(record)
                account.record_failure(str(e))

        finally:
            # Log metrics periodically
            now = time.monotonic()
            if now - self._last_metrics_log > METRICS_LOG_INTERVAL:
                self._last_metrics_log = now
                logger.info(f"[Campaign {self._campaign_id}] Queue: {self._queue.qsize()}, "
                            f"Active: {self._active_workers}, "
                            f"Pool: {self.pool.get_stats()}")

    async def _delayed_requeue(self, record: DeliveryRecord, delay: float):
        """Re-queue a record after a delay (for long flood waits)."""
        await asyncio.sleep(delay)
        if self.is_running:
            await self._queue.put(record)

    async def _checkpoint_flush_loop(self):
        """Periodically flush checkpoints to database."""
        while self.is_running:
            await asyncio.sleep(30)  # Flush every 30 seconds
            if self._checkpoint:
                self._checkpoint.flush_to_db()

    def get_live_status(self) -> str:
        """Generate live status string for UI."""
        if not self._checkpoint:
            return "ℹ️ No active campaign"

        pool_stats = self.pool.get_stats()
        sent = sum(1 for r in self._checkpoint._checkpoints.values()
                   if r.status == DeliveryStatus.DELIVERED)
        failed = sum(1 for r in self._checkpoint._checkpoints.values()
                     if r.status in (DeliveryStatus.FAILED, DeliveryStatus.DEAD_LETTER))
        pending = self._queue.qsize()

        elapsed = 0
        if self._campaign_start_time:
            elapsed = int((time.monotonic() - self._campaign_start_time) / 60)

        return (
            "📊 **LIVE DM ENGINE TRACKER**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📨 Delivered: `{sent} / {self._total_targets}`\n"
            f"⏳ Queued: `{pending}` | Failed: `{failed}`\n"
            f"⚡ Accounts: Active `{pool_stats['active']}` | "
            f"Cooldown `{pool_stats['cooldown']}` | "
            f"Banned `{pool_stats['banned']}`\n"
            f"💀 Down This Run: `{self._accounts_down}`\n"
            f"🆔 Campaign: `{self._campaign_id}` | ⏱ `{elapsed} min`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: `{'🟢 RUNNING' if self.is_running else '🔴 STOPPED'}`"
        )


# =====================================================================
# ENTERPRISE DM SENDER (LEGACY COMPATIBLE WRAPPER)
# =====================================================================
class EnterpriseDMSender:
    """
    Wrapper class that maintains 100% backward compatibility.
    Internal implementation uses the new CampaignManager + AccountPool.
    """
    def __init__(self, db, proxy_manager: Optional[RobustProxyManager] = None):
        self.db = db
        self.proxy_manager = proxy_manager
        self._account_pool = AccountPool(db)
        self._campaign_manager = CampaignManager(db, self._account_pool)

        self.is_running = False
        self.active_task = None
        self.wizard_state: Dict[int, Dict[str, Any]] = {}

        # Initialize new architecture
        self._account_pool = AccountPool(db)
        self._campaign_manager = CampaignManager(db, self._account_pool)

        # Stats (legacy)
        self.stats = {
            "total_sent": 0,
            "failed": 0,
            "accounts_used": 0,
            "accounts_down": 0,
            "total_targets": 0,
        }

        try:
            print("🎯 [DM Engine] Enterprise v2.0 initialized with multi-field resolver, anti-ban, and checkpointing.")
        except Exception as e:
            logger.error(f"❌ Fatal Mapping Fault: {e}")

    def reset_stats(self):
        self.stats = {
            "total_sent": 0,
            "failed": 0,
            "accounts_used": 0,
            "accounts_down": 0,
            "total_targets": 0,
        }

    def halt_campaign(self):
        """Stops the DM campaign immediately (legacy compatible)."""
        self.is_running = False
        if self.active_task:
            self.active_task.cancel()
        # Also halt via new architecture
        asyncio.ensure_future(self._campaign_manager.halt_campaign())

    async def execute_dm_campaign(self, target_list: list, message_text: str, media_path: str,
                                   limit: int, ui_callback):
        """
        Legacy compatible DM campaign execution.
        Now uses the production-grade pipeline internally.
        """
        self.is_running = True
        self.reset_stats()

        # Validate inputs
        final_text = str(message_text).strip() if message_text else ""
        if final_text.lower() == "skip" or not final_text:
            final_text = None

        if not final_text and (not media_path or not os.path.exists(str(media_path))):
            await ui_callback("❌ **Campaign Aborted:** Both text and media cannot be empty.")
            self.is_running = False
            return

        # Load accounts into pool
        all_accounts = self.db.get_active_target_sessions()
        if not all_accounts:
            await ui_callback("❌ **Campaign Aborted:** Koi active verified session nahi mila.")
            self.is_running = False
            return

        # Initialize account pool
        await self._account_pool.initialize(all_accounts, proxy_manager=self.proxy_manager)

        # Update legacy stats
        self.stats["accounts_used"] = len(self._account_pool.active_accounts)
        self.stats["total_targets"] = min(limit, len(target_list)) if limit > 0 else len(target_list)

        # Launch campaign via new manager
        self.active_task = asyncio.create_task(
            self._campaign_manager.start_campaign(
                target_list, message_text, media_path, limit, ui_callback
            )
        )

        try:
            await self.active_task
        except asyncio.CancelledError:
            pass
        finally:
            self.is_running = self._campaign_manager.is_running

            # Sync legacy stats from campaign
            if self._campaign_manager._checkpoint:
                self.stats["total_sent"] = sum(
                    1 for r in self._campaign_manager._checkpoint._checkpoints.values()
                    if r.status == DeliveryStatus.DELIVERED
                )
                self.stats["failed"] = sum(
                    1 for r in self._campaign_manager._checkpoint._checkpoints.values()
                    if r.status in (DeliveryStatus.FAILED, DeliveryStatus.DEAD_LETTER)
                )
            self.stats["accounts_down"] = self._campaign_manager._accounts_down

            # Shutdown pool
            await self._account_pool.shutdown_all()

    def _generate_live_status(self) -> str:
        """Legacy status generation delegates to new CampaignManager."""
        return self._campaign_manager.get_live_status()


# =====================================================================
# BOT HANDLERS (PRESERVED — 100% BACKWARD COMPATIBLE)
# =====================================================================
def setup_dmsender_handlers(bot: TelegramClient, db, proxy_manager: Optional[RobustProxyManager] = None):
    sender_engine = EnterpriseDMSender(db, proxy_manager)

    def is_admin(sender_id):
        if ADMIN_ID:
            return str(sender_id) == str(ADMIN_ID)
        return True

    @bot.on(events.NewMessage(pattern='/send_dmsender'))
    async def wizard_start(event):
        if not is_admin(event.sender_id):
            return

        if sender_engine.is_running:
            await event.reply("⚠️ **Engine Occupied:** Campaign background me active hai.")
            return

        try:
            group_stats = sender_engine.db.get_group_stats()

            if not group_stats:
                msg = "📊 `scraped_data` collection is empty.\n\n👉 Direct single profile target karne ke liye `@username` type karein."
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
                "limit": 0,
            }
            await event.reply(msg)

        except Exception as e:
            await event.reply(f"❌ **Database Connection Error:** {e}")

    @bot.on(events.NewMessage)
    async def wizard_steps(event):
        if not is_admin(event.sender_id):
            return
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
                        "phone": doc.get("phone"),
                    })

                if not extracted_targets:
                    await event.reply("❌ Is group me valid lines nahi mili. Phir se chunein.")
                    return

                state["targets"] = extracted_targets
                state["step"] = "AWAITING_LIMIT"
                await event.reply(
                    f"✅ **{len(state['targets'])} users extracted successfully!**\n\n"
                    f"Kitne logo ko message bhejna chahte hain? (Number daalein ya `all` likhein):"
                )
            else:
                state["targets"] = [inp]
                state["limit"] = 1
                state["step"] = "AWAITING_TEXT"
                await event.reply(
                    f"🎯 **Targeting:** {inp}\n\n"
                    "📝 Apna promotional message bhejein.\n"
                    "*(Agar sirf media bhejna hai toh `skip` likhein)*"
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
                await event.reply("❌ Invalid format. Number ya 'all' likhein.")
                return

            state["limit"] = limit
            state["step"] = "AWAITING_TEXT"
            await event.reply(
                f"⚙️ **Limit Set:** {limit}\n\n"
                "📝 Ab apna promotional message bhejein.\n"
                "*(Agar sirf media bhejna hai toh `skip` likhein)*"
            )

        elif step == "AWAITING_TEXT":
            msg_text = event.text.strip()
            state["text"] = msg_text

            state["step"] = "AWAITING_MEDIA"
            await event.reply(
                "🖼️ **Message Cached!**\n\n"
                "Ab media upload karein ya `skip` type karein:"
            )

        elif step == "AWAITING_MEDIA":
            if event.text and event.text.strip().lower() == "skip":
                state["media"] = None
            elif event.media:
                media_path = await bot.download_media(event.media)
                state["media"] = media_path
            else:
                await event.reply("❌ Media nahi mila. Re-send ya `skip` likhein.")
                return

            ui_msg = await event.reply("⚡ Deploying DM Engine...")

            async def update_ui_status(text_payload):
                try:
                    await ui_msg.edit(text_payload)
                except Exception:
                    pass

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
        if not is_admin(event.sender_id):
            return
        if not sender_engine.is_running:
            await event.reply("ℹ️ Koi DM process running nahi hai.")
            return
        sender_engine.halt_campaign()
        await event.reply("🛑 **Emergency Brake Engaged!** Engine fully stopped.")

    return sender_engine