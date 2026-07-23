#!/usr/bin/env python3
"""
Ultimate Enterprise Telegram Suite - High-Performance Multi-Account Rotating Member Adder
FIXED: Removed duplicate get_client method, ensured DB lock integration.
"""
import os, sys, time, asyncio, random, logging, gc, json, hashlib, math
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple, Set, Callable, Union
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from weakref import WeakSet, WeakValueDictionary
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import InviteToChannelRequest, JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.tl.functions.contacts import ResolveUsernameRequest, GetContactsRequest
from telethon.tl.functions.users import GetUsersRequest
from telethon.tl.types import InputPeerChannel, InputPeerUser, ChatInviteAlready
from telethon.errors import *
from config import CONFIG, DEVICE_PROFILES
from database import SuiteDatabase
from proxy_manager import RobustProxyManager, ProxyEntry
import proxy_manager
from scraper import MemberScraper

logger = logging.getLogger("SuiteAdder")

# =====================================================================
# CONSTANTS
# =====================================================================
# Cache
MAX_ENTITY_CACHE = 500
MAX_USERNAME_CACHE = 1000
ENTITY_CACHE_TTL = 3600          # 1 hour
NEGATIVE_CACHE_TTL = 600         # 10 min for failed lookups

# Account Health
MAX_CONSECUTIVE_FAILURES = 5
HEALTH_SCORE_DECAY = 0.95        # Exponential decay per successful add
FLOOD_PENALTY_MULTIPLIER = 2.0
PEERFLOOD_PENALTY_MULTIPLIER = 3.0
ACCOUNT_DAILY_CAP_SOFT = 40      # Per account per day
ACCOUNT_DAILY_CAP_HARD = 60
ACCOUNT_HOURLY_CAP = 15

# Timing (Anti-Ban)
MIN_HUMAN_INTERVAL = 10.0
MAX_HUMAN_INTERVAL = 25.0
BURST_LIMIT_DEFAULT = 5
BURST_COOLDOWN_MIN = 45
BURST_COOLDOWN_MAX = 90
MICRO_PAUSE_INTERVAL = 3
MICRO_PAUSE_DURATION = (15, 30)
MEAL_BREAK_INTERVAL = 25         # Every N adds, longer break
MEAL_BREAK_DURATION = (120, 300) # 2-5 min
NIGHT_SLOWDOWN_HOUR_START = 1    # 1 AM
NIGHT_SLOWDOWN_HOUR_END = 7      # 7 AM
NIGHT_MULTIPLIER = 2.5

# Workers & Pool
MAX_WORKER_DEFAULT = 10
MAX_CONCURRENT_CONNECTIONS = 15
CONNECTION_IDLE_TIMEOUT = 300    # 5 min
WORKER_HEALTH_INTERVAL = 120     # 2 min check
METRICS_INTERVAL = 30            # Log metrics every 30s
DB_FLUSH_INTERVAL = 15           # Batch DB writes every 15s

# Recovery
MAX_FLOOD_WAIT_RECOVER = 7200    # Max 2 hours
FLOOD_RECOVER_GRACE = 60         # Extra seconds after flood wait
NETWORK_RETRY_DELAY = (5, 30)
PROXY_FAIL_RETRY_DELAY = (10, 60)


# =====================================================================
# ENUMS
# =====================================================================
class AddStatus(Enum):
    SUCCESS = auto()
    ALREADY_MEMBER = auto()
    PRIVACY_RESTRICTED = auto()
    INVALID_IDENTITY = auto()
    FLOOD_WAIT = auto()
    PEER_FLOOD = auto()
    ACCOUNT_BANNED = auto()
    ACCOUNT_REVOKED = auto()
    NETWORK_ERROR = auto()
    UNKNOWN_ERROR = auto()

class AccountState(Enum):
    ACTIVE = auto()
    COOLDOWN = auto()
    FLOODED = auto()
    PEER_FLOODED = auto()
    LIMITED = auto()
    BANNED = auto()
    REVOKED = auto()
    IDLE = auto()

class ResolveMethod(Enum):
    CACHED = auto()
    USERNAME = auto()
    USER_ID_HASH = auto()
    USER_ID_BLIND = auto()
    PHONE_CONTACT = auto()
    RESOLVE_USERNAME_API = auto()
    GET_USERS_API = auto()
    NEGATIVE_CACHE = auto()


# =====================================================================
# DATA CLASSES
# =====================================================================
@dataclass
class AddRecord:
    """Tracks a single member addition attempt."""
    user_id: Optional[str] = None
    username: Optional[str] = None
    access_hash: Optional[str] = None
    phone: Optional[str] = None
    status: AddStatus = AddStatus.SUCCESS
    account_phone: Optional[str] = None
    attempt: int = 0
    duration_ms: float = 0.0
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    resolve_method: Optional[ResolveMethod] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d['status'] = self.status.name
        d['timestamp'] = self.timestamp.isoformat()
        if self.resolve_method:
            d['resolve_method'] = self.resolve_method.name
        return d

@dataclass
class AccountHealth:
    """Runtime health metrics for a single account."""
    phone: str
    api_id: int = 0
    api_hash: str = ""
    session_string: str = ""
    device: dict = field(default_factory=dict)
    state: AccountState = AccountState.ACTIVE
    proxy_entry: Optional[ProxyEntry] = None
    proxy_manager: Optional[RobustProxyManager] = None
    proxy_country: str = ""
    health_score: float = 100.0
    total_added: int = 0
    total_failed: int = 0
    consecutive_failures: int = 0
    flood_waits: int = 0
    peer_floods: int = 0
    privacy_skips: int = 0
    already_members: int = 0
    last_activity: float = 0.0
    last_flood_time: float = 0.0
    cooldown_until: float = 0.0
    daily_count: int = 0
    hourly_count: int = 0
    daily_reset: datetime = field(default_factory=datetime.utcnow)
    hourly_reset: datetime = field(default_factory=datetime.utcnow)
    burst_count: int = 0
    is_connected: bool = False
    _client: Optional[TelegramClient] = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    db: Any = None

    def can_add(self) -> bool:
        now = time.monotonic()
        if self.state in (AccountState.BANNED, AccountState.REVOKED):
            return False
        if self.state in (AccountState.COOLDOWN, AccountState.FLOODED,
                          AccountState.PEER_FLOODED, AccountState.LIMITED):
            if now < self.cooldown_until:
                return False
            # Auto-recover when cooldown expires
            self.state = AccountState.ACTIVE
            self.consecutive_failures = 0
        # Daily/hourly caps
        if self.daily_count >= ACCOUNT_DAILY_CAP_HARD:
            return False
        if self.hourly_count >= ACCOUNT_HOURLY_CAP:
            return False
        return True

    def record_success(self):
        self.total_added += 1
        self.daily_count += 1
        self.hourly_count += 1
        self.burst_count += 1
        self.consecutive_failures = 0
        self.last_activity = time.monotonic()
        self.health_score = min(100.0, self.health_score + 0.5)
        # Reset counters on day/hour change
        now_utc = datetime.utcnow()
        if now_utc.date() > self.daily_reset.date():
            self.daily_count = 0
            self.daily_reset = now_utc
        if now_utc.hour != self.hourly_reset.hour or now_utc.date() > self.hourly_reset.date():
            self.hourly_count = 0
            self.hourly_reset = now_utc

    def record_failure(self, status: AddStatus, error: str = ""):
        self.total_failed += 1
        self.consecutive_failures += 1
        self.last_activity = time.monotonic()
        self.health_score = max(0.0, self.health_score * HEALTH_SCORE_DECAY - 2.0)
        err_lower = error.lower()
        if any(x in err_lower for x in ["banned", "deactivated", "revoked", "disabled"]):
            self.state = AccountState.BANNED
        elif self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.state = AccountState.LIMITED
            self.cooldown_until = time.monotonic() + 1800  # 30 min
        if status == AddStatus.FLOOD_WAIT:
            self.flood_waits += 1
        elif status == AddStatus.PEER_FLOOD:
            self.peer_floods += 1
        elif status == AddStatus.PRIVACY_RESTRICTED:
            self.privacy_skips += 1
        elif status == AddStatus.ALREADY_MEMBER:
            self.already_members += 1

    def mark_flooded(self, seconds: int):
        self.flood_waits += 1
        self.last_flood_time = time.monotonic()
        wait = min(seconds * FLOOD_PENALTY_MULTIPLIER, MAX_FLOOD_WAIT_RECOVER) + FLOOD_RECOVER_GRACE
        self.cooldown_until = time.monotonic() + wait + random.uniform(5, 30)
        self.state = AccountState.FLOODED
        self.health_score = max(0.0, self.health_score * 0.6)

    def mark_peer_flooded(self):
        self.peer_floods += 1
        wait = PEERFLOOD_PENALTY_MULTIPLIER * (60 + random.uniform(0, 60) * min(self.peer_floods, 5))
        self.cooldown_until = time.monotonic() + wait
        self.state = AccountState.PEER_FLOODED
        self.health_score = max(0.0, self.health_score * 0.4)

    def get_effective_delay(self) -> float:
        """Calculate dynamic delay based on health and history."""
        base = random.uniform(MIN_HUMAN_INTERVAL, MAX_HUMAN_INTERVAL)
        modifiers = 1.0
        if self.health_score < 50:
            modifiers *= 1.5
        if self.consecutive_failures > 0:
            modifiers *= 1.0 + (self.consecutive_failures * 0.3)
        if self.flood_waits > 0:
            modifiers *= 1.0 + (min(self.flood_waits, 5) * 0.2)
        if self.daily_count > ACCOUNT_DAILY_CAP_SOFT:
            modifiers *= 2.0
        # Night slowdown
        current_hour = datetime.utcnow().hour
        if NIGHT_SLOWDOWN_HOUR_START <= current_hour < NIGHT_SLOWDOWN_HOUR_END:
            modifiers *= NIGHT_MULTIPLIER
        return base * modifiers

    async def get_client(self) -> Optional[TelegramClient]:
        async with self._lock:
            if self._client and self._client.is_connected():
                return self._client

            # 🔥 Proxy management with region preference
            proxy_entry = self.proxy_entry
            if proxy_entry and proxy_entry.is_dead:
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
                    self.proxy_country = proxy_entry.country or ""
                    logger.debug(f"[{self.phone}] Assigned proxy {proxy_entry.host}:{proxy_entry.port} (country={self.proxy_country})")
                else:
                    logger.error(f"[{self.phone}] No proxy available! Direct connection blocked to prevent Mass IP Ban.")
                if self.db:
                    self.db.release_lock(self.phone)
                self.record_failure(AddStatus.NETWORK_ERROR, "Proxy Pool Empty - IP Protected")
                return None

            proxy_dict = proxy_entry.dict if proxy_entry else None

            try:
                client = TelegramClient(
                    StringSession(self.session_string), self.api_id, self.api_hash,
                    device_model=self.device.get("device_model", "PC 64bit"),
                    system_version=self.device.get("system_version", "Windows 11"),
                    app_version=self.device.get("app_version", "4.8.4"),
                    proxy=proxy_dict,
                    entity_cache_limit=30,
                    sequential_updates=False,
                )
                await client.connect()
                if not await client.is_user_authorized():
                    self.state = AccountState.REVOKED
                    if self.db:
                        self.db.release_lock(self.phone)
                    return None
                self._client = client
                self.is_connected = True
                if self.proxy_manager and self.db and proxy_entry:
                    self.db.update_account_proxy(self.phone, proxy_entry.dict)
                return client
            except Exception as e:
                logger.error(f"[{self.phone}] Client init failed: {e}")
                if proxy_entry:
                    proxy_entry.record_failure()
                    self.proxy_entry = None
                    self.proxy_country = ""
                self.record_failure(AddStatus.NETWORK_ERROR, str(e))
                if self.db:
                    self.db.release_lock(self.phone)
                return None

    async def disconnect_client(self):
        async with self._lock:
            if self._client:
                try: await self._client.disconnect()
                except: pass
                self._client = None
                self.is_connected = False
            # 🔥 SYNC FIX: Release the global lock so main_bot can audit it again
            if self.db:
                self.db.release_lock(self.phone)


# =====================================================================
# ENTERPRISE ENTITY CACHE
# =====================================================================
class EnterpriseEntityCache:
    """
    Multi-layer entity cache with:
    - LRU eviction
    - TTL expiration (positive and negative)
    - Background cleanup
    - Memory bounded
    """
    def __init__(self, maxsize: int = MAX_ENTITY_CACHE, ttl: int = ENTITY_CACHE_TTL):
        self._maxsize = maxsize
        self._ttl = ttl
        self._positive: OrderedDict = OrderedDict()
        self._negative: OrderedDict = OrderedDict()  # Failed lookups
        self._timestamps: Dict[str, float] = {}
        self._negative_ttl = NEGATIVE_CACHE_TTL
        self._stats = {"hits": 0, "misses": 0, "negative_hits": 0, "evictions": 0}
        self._cleanup_task: Optional[asyncio.Task] = None

    def start_background_cleanup(self):
        """Periodic cleanup of expired entries."""
        async def _cleanup():
            while True:
                await asyncio.sleep(300)  # Every 5 min
                self._evict_expired()
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(_cleanup())

    def stop_background_cleanup(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        # Check negative cache first
        if key in self._negative:
            if now - self._timestamps.get(f"neg:{key}", 0) < self._negative_ttl:
                self._stats["negative_hits"] += 1
                return None  # Known invalid
            self._negative.pop(key, None)
            self._timestamps.pop(f"neg:{key}", None)

        if key not in self._positive:
            self._stats["misses"] += 1
            return None
        if now - self._timestamps.get(key, 0) > self._ttl:
            self._positive.pop(key, None)
            self._timestamps.pop(key, None)
            self._stats["misses"] += 1
            return None
        self._positive.move_to_end(key)
        self._stats["hits"] += 1
        return self._positive[key]

    def set(self, key: str, value: Any):
        self._positive[key] = value
        self._timestamps[key] = time.monotonic()
        self._positive.move_to_end(key)
        # Remove from negative cache if present
        self._negative.pop(key, None)
        self._timestamps.pop(f"neg:{key}", None)
        self._evict_if_needed()

    def set_negative(self, key: str):
        """Cache that a key is invalid (negative caching)."""
        self._negative[key] = True
        self._timestamps[f"neg:{key}"] = time.monotonic()
        self._positive.pop(key, None)
        self._timestamps.pop(key, None)

    def invalidate(self, key: str):
        self._positive.pop(key, None)
        self._negative.pop(key, None)
        self._timestamps.pop(key, None)
        self._timestamps.pop(f"neg:{key}", None)

    def clear(self):
        self._positive.clear()
        self._negative.clear()
        self._timestamps.clear()

    def _evict_if_needed(self):
        while len(self._positive) > self._maxsize:
            self._positive.popitem(last=False)
            self._stats["evictions"] += 1

    def _evict_expired(self):
        now = time.monotonic()
        expired_pos = [k for k, v in list(self._timestamps.items())
                       if not k.startswith("neg:") and now - v > self._ttl]
        for k in expired_pos:
            self._positive.pop(k, None)
            self._timestamps.pop(k, None)
        expired_neg = [k.replace("neg:", "") for k, v in list(self._timestamps.items())
                       if k.startswith("neg:") and now - v > self._negative_ttl]
        for k in expired_neg:
            self._negative.pop(k, None)
            self._timestamps.pop(f"neg:{k}", None)

    def get_stats(self) -> dict:
        return dict(self._stats)


# =====================================================================
# ENTERPRISE ENTITY RESOLVER
# =====================================================================
class EnterpriseEntityResolver:
    """
    Multi-field entity resolution engine with intelligent fallback chain.
    Priority: cached → username → user_id+access_hash → phone contact → resolveUsername API → GetUsersRequest
    """
    def __init__(self):
        self._cache = EnterpriseEntityCache()
        self._stats = {
            "resolved": 0, "failed": 0,
            "by_method": defaultdict(int),
        }
        self._contact_set: Set[str] = set()
        self._contact_cache_time: float = 0.0

    def start_background_cleanup(self):
        self._cache.start_background_cleanup()

    def stop_background_cleanup(self):
        self._cache.stop_background_cleanup()

    async def resolve(
        self,
        client: TelegramClient,
        member: dict,
    ) -> Tuple[Optional[Any], ResolveMethod]:
        """
        Resolve a member dict to an InputUser using every available field.
        Returns (entity, method_used) or (None, ResolveMethod.NEGATIVE_CACHE).
        """
        username = str(member.get("username", "")).strip()
        user_id = str(member.get("user_id", "")).strip()
        access_hash = str(member.get("access_hash", "0")).strip()
        phone = str(member.get("phone", "")).strip()

        # Build cache keys
        keys = []
        if username and username.lower() not in ("none", "null", ""):
            keys.append(f"un:{username.lower()}")
        if user_id and user_id not in ("0", "None", ""):
            keys.append(f"uid:{user_id}")
            if access_hash and access_hash not in ("0", "None", ""):
                keys.append(f"peer:{user_id}:{access_hash}")

        # Check cache first
        for key in keys:
            cached = self._cache.get(key)
            if cached is not None:
                self._stats["by_method"][ResolveMethod.CACHED.name] += 1
                return cached, ResolveMethod.CACHED
            elif cached is None and key.startswith("un:"):
                # Check negative cache for usernames
                neg_check = self._cache.get(f"neg:{key}")
                if neg_check is None and key in self._cache._negative:
                    pass  # Known invalid username, skip

        # 1. Try username (fastest path)
        if username and username.lower() not in ("none", "null", ""):
            try:
                u_str = f"@{username}" if not username.startswith("@") else username
                entity = await client.get_input_entity(u_str)
                if entity:
                    self._cache.set(f"un:{username.lower()}", entity)
                    if hasattr(entity, 'user_id'):
                        self._cache.set(f"uid:{entity.user_id}", entity)
                    self._stats["by_method"][ResolveMethod.USERNAME.name] += 1
                    self._stats["resolved"] += 1
                    return entity, ResolveMethod.USERNAME
            except (ValueError, UsernameNotOccupiedError, PeerIdInvalidError):
                # Cache negative result briefly
                self._cache.set_negative(f"un:{username.lower()}")
            except FloodWaitError:
                raise
            except Exception:
                pass

        # 2. Try user_id + access_hash
        if user_id and user_id not in ("0", "None", "") and access_hash and access_hash not in ("0", "None", ""):
            try:
                peer = InputPeerUser(int(user_id), int(access_hash))
                entity = await client.get_input_entity(peer)
                if entity:
                    cache_key = f"peer:{user_id}:{access_hash}"
                    self._cache.set(cache_key, entity)
                    self._cache.set(f"uid:{user_id}", entity)
                    self._stats["by_method"][ResolveMethod.USER_ID_HASH.name] += 1
                    self._stats["resolved"] += 1
                    return entity, ResolveMethod.USER_ID_HASH
            except Exception:
                pass

        # 3. Try phone number (if in contacts)
        if phone and phone not in ("None", "") and phone in self._contact_set:
            try:
                entity = await client.get_input_entity(f"+{phone.replace('+', '')}")
                if entity:
                    self._cache.set(f"uid:{getattr(entity, 'user_id', '')}", entity)
                    self._stats["by_method"][ResolveMethod.PHONE_CONTACT.name] += 1
                    self._stats["resolved"] += 1
                    return entity, ResolveMethod.PHONE_CONTACT
            except Exception:
                pass

        # 4. Try ResolveUsernameRequest API
        if username and username.lower() not in ("none", "null", ""):
            try:
                result = await client(ResolveUsernameRequest(username))
                if result.users:
                    user = result.users[0]
                    input_user = InputPeerUser(user.id, user.access_hash)
                    self._cache.set(f"un:{username.lower()}", input_user)
                    self._cache.set(f"uid:{user.id}", input_user)
                    self._cache.set(f"peer:{user.id}:{user.access_hash}", input_user)
                    self._stats["by_method"][ResolveMethod.RESOLVE_USERNAME_API.name] += 1
                    self._stats["resolved"] += 1
                    return input_user, ResolveMethod.RESOLVE_USERNAME_API
            except FloodWaitError:
                raise
            except Exception:
                self._cache.set_negative(f"un:{username.lower()}")

        # 5. Try GetUsersRequest with user_id (blind)
        if user_id and user_id not in ("0", "None", ""):
            try:
                result = await client(GetUsersRequest([InputPeerUser(int(user_id), 0)]))
                if result and not isinstance(result[0], type(None)):
                    user = result[0]
                    input_user = InputPeerUser(user.id, user.access_hash)
                    self._cache.set(f"uid:{user.id}", input_user)
                    if hasattr(user, 'username') and user.username:
                        self._cache.set(f"un:{user.username.lower()}", input_user)
                    self._stats["by_method"][ResolveMethod.GET_USERS_API.name] += 1
                    self._stats["resolved"] += 1
                    return input_user, ResolveMethod.GET_USERS_API
            except FloodWaitError:
                raise
            except Exception:
                pass

        # 6. Try user_id blind via get_input_entity
        if user_id and user_id not in ("0", "None", ""):
            try:
                entity = await client.get_input_entity(int(user_id))
                if entity:
                    self._cache.set(f"uid:{user_id}", entity)
                    self._stats["by_method"][ResolveMethod.USER_ID_BLIND.name] += 1
                    self._stats["resolved"] += 1
                    return entity, ResolveMethod.USER_ID_BLIND
            except Exception:
                pass

        self._stats["failed"] += 1
        return None, ResolveMethod.NEGATIVE_CACHE

    async def refresh_contacts(self, client: TelegramClient):
        """Fetch contact phone numbers for phone-based resolution."""
        now = time.monotonic()
        if now - self._contact_cache_time < 600:
            return
        try:
            contacts = await client(GetContactsRequest(0))
            self._contact_set = set()
            for user in contacts.users:
                if hasattr(user, 'phone') and user.phone:
                    self._contact_set.add(user.phone.replace("+", ""))
            self._contact_cache_time = now
            logger.debug(f"[EntityResolver] Cached {len(self._contact_set)} contact phones")
        except Exception as e:
            logger.warning(f"[EntityResolver] Contact refresh failed: {e}")

    def get_stats(self) -> dict:
        stats = dict(self._stats)
        stats["cache"] = self._cache.get_stats()
        stats["by_method"] = dict(self._stats["by_method"])
        return stats

    def clear(self):
        self._cache.clear()
        self._stats = {"resolved": 0, "failed": 0, "by_method": defaultdict(int)}


# =====================================================================
# ACCOUNT POOL WITH HEALTH MONITORING
# =====================================================================
class AccountPool:
    """
    Manages account lifecycle, health scoring, connection reuse,
    automatic recovery, and dynamic scheduling.
    """
    def __init__(self, db: SuiteDatabase):
        self.db = db
        self._accounts: Dict[str, AccountHealth] = {}
        self._connection_sem = asyncio.Semaphore(MAX_CONCURRENT_CONNECTIONS)
        self._health_task: Optional[asyncio.Task] = None
        self._entity_resolver = EnterpriseEntityResolver()

    async def initialize(self, account_docs: List[dict], proxy_manager: Optional[RobustProxyManager] = None):
        """Initialize pool from database documents with proxy bindings."""
        for acc in account_docs:
            phone = str(acc.get("phone", "")).strip()
            if not phone or phone in self._accounts:
                continue
            device = acc.get("device_metadata") or acc.get("device_fingerprint") or random.choice(DEVICE_PROFILES)
            stored_proxy = acc.get("proxy")
            
            account = AccountHealth(
                phone=phone,
                api_id=int(acc.get("api_id", CONFIG["API_ID"])),
                api_hash=str(acc.get("api_hash", CONFIG["API_HASH"])),
                session_string=str(acc.get("session_string") or acc.get("session", "")),
                device=device if isinstance(device, dict) else random.choice(DEVICE_PROFILES),
                proxy_manager=proxy_manager,
                proxy_country=stored_proxy.get("country") if stored_proxy else "",
                db=self.db,
                state=AccountState.ACTIVE,
            )
            
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
                except Exception:
                    logger.warning(f"[AccountPool] Ignoring invalid stored proxy for {phone}")
                    
            self._accounts[phone] = account

        self._health_task = asyncio.create_task(self._health_monitor_loop())
        self._entity_resolver.start_background_cleanup()
        logger.info(f"[AccountPool] Initialized {len(self._accounts)} accounts")

    @property
    def total(self) -> int:
        return len(self._accounts)

    def get_ready_accounts(self) -> List[AccountHealth]:
        """Returns accounts sorted by health (best first) that can add right now."""
        ready = [a for a in self._accounts.values() if a.can_add()]
        # Sort: highest health score first, then lowest daily count
        ready.sort(key=lambda a: (-a.health_score, a.daily_count, a.total_added))
        return ready

    def get_account(self, phone: str) -> Optional[AccountHealth]:
        return self._accounts.get(phone)

    async def acquire_client(self, account: AccountHealth) -> Optional[TelegramClient]:
        async with self._connection_sem:
            return await account.get_client()

    async def resolve_entity(self, client: TelegramClient, member: dict) -> Tuple[Optional[Any], ResolveMethod]:
        return await self._entity_resolver.resolve(client, member)

    async def refresh_contacts(self, client: TelegramClient):
        await self._entity_resolver.refresh_contacts(client)

    async def _health_monitor_loop(self):
        while True:
            await asyncio.sleep(WORKER_HEALTH_INTERVAL)
            try:
                now = time.monotonic()
                for acc in list(self._accounts.values()):
                    # Recover from cooldowns
                    if acc.state in (AccountState.FLOODED, AccountState.PEER_FLOODED,
                                     AccountState.COOLDOWN, AccountState.LIMITED):
                        if now >= acc.cooldown_until:
                            old = acc.state.name
                            acc.state = AccountState.ACTIVE
                            acc.consecutive_failures = 0
                            logger.info(f"[Health] {acc.phone} recovered: {old} → ACTIVE")
                    # Disconnect idle connections
                    if acc._client and acc.is_connected:
                        if now - acc.last_activity > CONNECTION_IDLE_TIMEOUT:
                            await acc.disconnect_client()
                            logger.debug(f"[Health] {acc.phone} disconnected (idle)")
                    # Reset daily/hourly counters
                    now_utc = datetime.utcnow()
                    if now_utc.date() > acc.daily_reset.date():
                        acc.daily_count = 0
                        acc.daily_reset = now_utc
                    if now_utc.hour != acc.hourly_reset.hour or now_utc.date() > acc.hourly_reset.date():
                        acc.hourly_count = 0
                        acc.hourly_reset = now_utc
            except Exception as e:
                logger.error(f"[HealthMonitor] Error: {e}")

    async def shutdown_all(self):
        if self._health_task:
            self._health_task.cancel()
        self._entity_resolver.stop_background_cleanup()
        for acc in list(self._accounts.values()):
            await acc.cleanup()
        self._accounts.clear()
        self._entity_resolver.clear()
        logger.info("[AccountPool] All accounts shut down")

    def get_stats(self) -> dict:
        states = defaultdict(int)
        total_added = 0
        total_failed = 0
        for acc in self._accounts.values():
            states[acc.state.name] += 1
            total_added += acc.total_added
            total_failed += acc.total_failed
        return {
            "total": len(self._accounts),
            "states": dict(states),
            "total_added": total_added,
            "total_failed": total_failed,
            "entity_resolver": self._entity_resolver.get_stats(),
        }


# =====================================================================
# QUEUE ARCHITECTURE
# =====================================================================
class PriorityMemberQueue:
    """
    Fair-scheduling queue with backpressure, worker stealing, and monitoring.
    """
    def __init__(self, maxsize: int = 10000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._total_enqueued: int = 0
        self._total_dequeued: int = 0

    async def put(self, item: dict):
        await self._queue.put(item)
        self._total_enqueued += 1

    async def get(self) -> dict:
        item = await self._queue.get()
        self._total_dequeued += 1
        return item

    def get_nowait(self) -> dict:
        item = self._queue.get_nowait()
        self._total_dequeued += 1
        return item

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    def get_metrics(self) -> dict:
        return {
            "qsize": self.qsize(),
            "enqueued": self._total_enqueued,
            "dequeued": self._total_dequeued,
        }


# =====================================================================
# ENTERPRISE MEMBER ADDER (MAIN ENGINE)
# =====================================================================
class EnterpriseMemberAdder:
    """
    Enterprise-grade member adder with:
    - Multi-field entity resolution
    - Adaptive scheduling with human behaviour simulation
    - Account health scoring
    - Flood protection engine
    - Bounded worker pool with automatic recovery
    - Metrics and monitoring
    - 100% backward compatible
    """
    def __init__(self, db: SuiteDatabase, proxy_manager: Optional[RobustProxyManager] = None):
        self.db = db
        self.proxy_manager = proxy_manager
        self.scraper_helper = MemberScraper(db)
        self.is_running = False

        # New architecture
        self._account_pool = AccountPool(db)
        self._member_queue = PriorityMemberQueue()
        self._workers: List[asyncio.Task] = []
        self._metrics_task: Optional[asyncio.Task] = None
        self._target_peer: Optional[InputPeerChannel] = None
        self._target_group_link: str = ""
        self._update_callback: Optional[Callable] = None

        # Telemetry (backward compatible)
        self.total_added = 0
        self.accounts_down = 0
        self.privacy_skips = 0

        # Internal tracking
        self._start_time: float = 0.0
        self._add_records: List[AddRecord] = []
        self._active_workers: int = 0
        self._processed_count: int = 0

    async def execute_adding_pipeline(self, target_group_link: str, update_callback) -> str:
        """
        Enterprise pipeline with adaptive scheduling and anti-ban.
        100% backward compatible signature and return format.
        """
        self.is_running = True
        self.total_added = 0
        self.accounts_down = 0
        self.privacy_skips = 0
        self._processed_count = 0
        self._add_records.clear()
        self._start_time = time.monotonic()
        self._target_group_link = target_group_link
        self._update_callback = update_callback

        # Pull scraped members
        scraped_pool = self.db.fetch_unprocessed_scraped_pool()
        if not scraped_pool:
            self.is_running = False
            return "⚠️ **Operation Aborted:** Scraped members database empty ya already processed hai! Pehle `/scrape` commands run karein."

        # Pull active sessions
        active_accounts = self.db.get_active_target_sessions()
        if not active_accounts:
            self.is_running = False
            return "❌ **Operation Failed:** Source DB (`source_accounts`) me active sessions nahi mile."

        # Resolve target group
        is_private, resolved_token = self.scraper_helper.resolve_group_link(target_group_link)
        target_entity_identifier = resolved_token if is_private else target_group_link
        private_hash = resolved_token if is_private else None

        # Initialize account pool
        await self._account_pool.initialize(active_accounts, proxy_manager=self.proxy_manager)

        # Get config values (with fallbacks)
        max_workers = min(len(active_accounts), CONFIG.get("ADDER_MAX_WORKER_SESSIONS", MAX_WORKER_DEFAULT))
        burst_limit = int(CONFIG.get("ADDER_BURST_ADD_LIMIT", BURST_LIMIT_DEFAULT))
        burst_cooldown = tuple(CONFIG.get("ADDER_BURST_COOLDOWN_TIME", (BURST_COOLDOWN_MIN, BURST_COOLDOWN_MAX)))
        progress_interval = int(CONFIG.get("ADDER_PROGRESS_UPDATE_INTERVAL", 10))

        # Populate queue
        shuffled_pool = list(scraped_pool)
        random.shuffle(shuffled_pool)
        for member in shuffled_pool:
            await self._member_queue.put(member)

        # Resolve target entity using first available account
        first_account = self._account_pool.get_ready_accounts()
        if not first_account:
            await update_callback("❌ **Operation Failed:** Koi ready account nahi mila.")
            await self._cleanup()
            return "❌ **Operation Failed:** No healthy accounts available."

        client = await self._account_pool.acquire_client(first_account[0])
        if not client:
            await self._account_pool.shutdown_all()
            self.is_running = False
            return "❌ **Operation Failed:** Could not initialize any client."

        try:
            self._target_peer = await self._resolve_target_peer(client, is_private, resolved_token, target_entity_identifier)
            if not self._target_peer:
                await update_callback("❌ **Operation Failed:** Target group/channel resolve nahi ho paya.")
                await self._cleanup()
                return "❌ **Operation Failed:** Target entity could not be resolved."
        except Exception as e:
            await update_callback(f"❌ **Operation Failed:** Entity resolution error: {e}")
            await self._cleanup()
            return f"❌ **Operation Failed:** {e}"

        # Refresh contacts from a healthy account for phone resolution
        try:
            healthy = self._account_pool.get_ready_accounts()
            if healthy:
                contact_client = await self._account_pool.acquire_client(healthy[0])
                if contact_client:
                    await self._account_pool.refresh_contacts(contact_client)
        except Exception:
            pass

        await update_callback(
            f"🚀 **Adding Engine Started!**\n"
            f"👥 Members: `{self._member_queue.qsize()}` | "
            f"Accounts: `{self._account_pool.total}` | "
            f"Workers: `{max_workers}`\n"
            f"🏁 Target resolved. Initializing workers..."
        )

        # 1. Start worker tasks
        self._workers = []
        for i in range(max_workers):
            worker = asyncio.create_task(self._worker_loop(
                i, burst_limit, burst_cooldown, progress_interval
            ))
            self._workers.append(worker)

        # 2. Start background metrics logger
        self._metrics_task = asyncio.create_task(self._metrics_loop(update_callback))

        # 3. Wait for workers to finish or queue to drain cleanly
        await asyncio.gather(*self._workers, return_exceptions=True)

        # 4. Final cleanup
        if self._metrics_task:
            self._metrics_task.cancel()

        await self._cleanup()

        # 5. Build final report message output format
        if self.accounts_down >= len(active_accounts) and not self._member_queue.empty():
            result = (
                f"⚠️ **All Active Workers Stopped!** Limit reached or sessions blocked.\n\n"
                f"📊 **Final Metrics Summary:**\n"
                f"- Total Added: `{self.total_added}`\n"
                f"- Banned/Down Nodes: `{self.accounts_down}`\n"
                f"- Privacy Skips: `{self.privacy_skips}`"
            )
        else:
            result = (
                f"🏁 **Adding Process Completed Successfully!**\n\n"
                f"📊 **Final Session Summary Details:**\n"
                f"- Total New Inhabitants: `{self.total_added}`\n"
                f"- Total Filtered Skips: `{self.privacy_skips}`\n"
                f"- Restructured Accounts Down: `{self.accounts_down}`"
            )

        self._add_records.clear()
        
        return result
            

    async def _resolve_target_peer(
        self,
        client: TelegramClient,
        is_private: bool,
        resolved_token: str,
        target_entity_identifier: str,
    ) -> Optional[InputPeerChannel]:
        """Resolve target group/channel to InputPeerChannel."""
        target_entity = None

        try:
            if is_private:
                try:
                    check = await client(CheckChatInviteRequest(resolved_token))
                    if isinstance(check, ChatInviteAlready):
                        target_entity = check.chat
                    else:
                        updates = await client(ImportChatInviteRequest(resolved_token))
                        if getattr(updates, "chats", None):
                            target_entity = updates.chats[0]
                except Exception:
                    pass
            else:
                try:
                    await client(JoinChannelRequest(resolved_token))
                except UserAlreadyParticipantError:
                    pass
                target_entity = await client.get_entity(resolved_token)
        except Exception:
            pass

        # Fallback
        if not target_entity:
            try:
                target_entity = await client.get_entity(target_entity_identifier)
            except Exception:
                return None

        if hasattr(target_entity, 'broadcast') or hasattr(target_entity, 'megagroup'):
            return InputPeerChannel(target_entity.id, target_entity.access_hash)
            
        try:
            from telethon.tl.types import InputPeerChat
            if hasattr(target_entity, 'access_hash'):
                return InputPeerChannel(target_entity.id, target_entity.access_hash)
            return InputPeerChat(target_entity.id)
        except Exception:
            return None

    async def _worker_loop(
        self,
        worker_id: int,
        burst_limit: int,
        burst_cooldown: Tuple[int, int],
        progress_interval: int,
    ):
        """Individual worker that processes members from the queue."""
        account: Optional[AccountHealth] = None
        worker_active = True

        while self.is_running and worker_active:
            # Select best account for this worker
            if account is None or not account.can_add():
                ready = self._account_pool.get_ready_accounts()
                if not ready:
                    # No accounts available, wait and retry
                    await asyncio.sleep(random.uniform(5, 15))
                    continue
                account = ready[0]
                account.burst_count = 0

            # Get next member from queue
            try:
                member = await asyncio.wait_for(self._member_queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                if self._member_queue.empty():
                    worker_active = False
                continue
            except asyncio.CancelledError:
                break

            if not self.is_running:
                break

            # Process add
            await self._process_add(worker_id, account, member, burst_limit, burst_cooldown, progress_interval)
            self._member_queue._queue.task_done()

        # Cleanup this worker's account reference
        if account:
            await account.disconnect_client()

    async def _process_add(
        self,
        worker_id: int,
        account: AccountHealth,
        member: dict,
        burst_limit: int,
        burst_cooldown: Tuple[int, int],
        progress_interval: int,
    ):
        """Process a single member add attempt with full resolution and anti-ban."""
        record = AddRecord(
            user_id=str(member.get("user_id", "")),
            username=str(member.get("username", "")),
            access_hash=str(member.get("access_hash", "0")),
            phone=str(member.get("phone", "")),
            account_phone=account.phone,
        )

        start_time = time.monotonic()
        client = await self._account_pool.acquire_client(account)
        if not client:
            record.status = AddStatus.NETWORK_ERROR
            record.error = "Client unavailable"
            self._add_records.append(record)
            self.accounts_down += 1
            return

        try:
            # Resolve entity using enterprise resolver
            entity, resolve_method = await self._account_pool.resolve_entity(client, member)
            record.resolve_method = resolve_method

            if entity is None:
                record.status = AddStatus.INVALID_IDENTITY
                record.error = "Entity resolution failed after all methods"
                self._add_records.append(record)
                self.db.log_addition_state(
                    record.user_id, record.username, "invalid_identity"
                )
                self.privacy_skips += 1
                return

            # Execute InviteToChannel
            await client(InviteToChannelRequest(self._target_peer, [entity]))
            record.status = AddStatus.SUCCESS
            self.total_added += 1
            self._processed_count += 1
            account.record_success()
            self.db.log_addition_state(record.user_id, record.username, "success_added")

            # Progress callback
            if self._processed_count % progress_interval == 0 and self._update_callback:
                await self._update_callback(
                    f"📊 **Live Tracking:** `{self.total_added}` members added "
                    f"(Workers: {self._active_workers}, "
                    f"Queue: {self._member_queue.qsize()})"
                )

            # Anti-ban: dynamic delay with micro-breaks and meal breaks
            delay = account.get_effective_delay()

            # Burst cooldown
            if account.burst_count >= burst_limit:
                extra = random.uniform(*burst_cooldown)
                delay = max(delay, extra)
                account.burst_count = 0

            # Micro-break
            if account.total_added > 0 and account.total_added % MICRO_PAUSE_INTERVAL == 0:
                micro = random.randint(*MICRO_PAUSE_DURATION)
                delay += micro
                logger.debug(f"[Worker {worker_id}] Micro-pause for {account.phone}: {micro}s")

            # Meal break (longer pause)
            if account.total_added > 0 and account.total_added % MEAL_BREAK_INTERVAL == 0:
                meal = random.randint(*MEAL_BREAK_DURATION)
                delay += meal
                logger.debug(f"[Worker {worker_id}] Meal break for {account.phone}: {meal}s")

            await asyncio.sleep(delay)

        except UserPrivacyRestrictedError:
            record.status = AddStatus.PRIVACY_RESTRICTED
            record.error = "Privacy restricted"
            account.record_failure(AddStatus.PRIVACY_RESTRICTED)
            self.privacy_skips += 1
            self.db.log_addition_state(record.user_id, record.username, "privacy_restricted")
            await asyncio.sleep(random.uniform(3, 7))

        except UserAlreadyParticipantError:
            record.status = AddStatus.ALREADY_MEMBER
            account.record_failure(AddStatus.ALREADY_MEMBER)
            self.db.log_addition_state(record.user_id, record.username, "already_member")
            await asyncio.sleep(random.uniform(1.5, 4.0))

        except PeerFloodError:
            record.status = AddStatus.PEER_FLOOD
            record.error = "Peer flood"
            account.mark_peer_flooded()
            self.accounts_down += 1
            self.db.log_addition_state(record.user_id, record.username, "peer_flood")
            logger.warning(f"[Worker {worker_id}] PeerFlood on {account.phone}")
            
            # 🔥 BUG 2 FIXED: Removed the blocking sleep! Just disconnect and move on.
            await account.disconnect_client()
            account = None  # Force new account on next iteration
            await self._member_queue.put(member)  # Re-queue user back to line

        except FloodWaitError as e:
            record.status = AddStatus.FLOOD_WAIT
            record.error = f"FloodWait {e.seconds}s"
            account.mark_flooded(e.seconds)
            self.accounts_down += 1
            self.db.log_addition_state(record.user_id, record.username, "flood_wait")
            logger.warning(f"[Worker {worker_id}] FloodWait {e.seconds}s on {account.phone}")
            
            # 🔥 BUG 2 FIXED: No blocking sleep. The account will cool down globally.
            await account.disconnect_client()
            account = None
            await self._member_queue.put(member)  # Re-queue user back to line

        except (UserIdInvalidError, ValueError, UsernameNotOccupiedError,
                InputUserDeactivatedError, UserDeactivatedError, UserDeactivatedBanError):
            record.status = AddStatus.INVALID_IDENTITY
            record.error = "Invalid or deactivated user"
            self.db.log_addition_state(record.user_id, record.username, "invalid_identity")
            await asyncio.sleep(random.uniform(1.0, 3.0))

        except (ChannelPrivateError, ChatWriteForbiddenError, ChannelInvalidError) as e:
            record.status = AddStatus.UNKNOWN_ERROR
            record.error = f"Channel error: {e}"
            logger.error(f"[Worker {worker_id}] Channel error: {e}")
            await asyncio.sleep(random.uniform(10, 30))

        except (AuthKeyUnregisteredError, SessionRevokedError, AuthKeyDuplicatedError,
                PhoneNumberBannedError) as e:
            record.status = AddStatus.ACCOUNT_REVOKED
            record.error = f"Account revoked: {type(e).__name__}"
            account.state = AccountState.REVOKED
            self.accounts_down += 1
            try:
                if hasattr(self.db, "mark_account_failed"):
                    self.db.mark_account_failed(account.phone, f"Banned at runtime: {str(e)[:80]}")
                else:
                    self.db.mark_account_revoked(account.phone, f"Banned at runtime: {str(e)[:80]}")
            except Exception:
                pass
            await account.disconnect_client()
            account = None

        except Exception as e:
            err_msg = str(e).lower()
            if any(k in err_msg for k in ["banned", "deactivated", "revoked", "disabled"]):
                record.status = AddStatus.ACCOUNT_BANNED
                record.error = f"Account banned: {err_msg[:80]}"
                account.state = AccountState.BANNED
                self.accounts_down += 1
                try:
                    if hasattr(self.db, "mark_account_failed"):
                        self.db.mark_account_failed(account.phone, f"Banned at runtime: {err_msg[:80]}")
                    else:
                        self.db.mark_account_revoked(account.phone, f"Banned at runtime: {err_msg[:80]}")
                except Exception:
                    pass
                await account.disconnect_client()
                account = None
            else:
                record.status = AddStatus.NETWORK_ERROR
                record.error = str(e)[:80]
                logger.warning(f"[Worker {worker_id}] Transient error: {e}")
                await asyncio.sleep(random.uniform(*NETWORK_RETRY_DELAY))

        finally:
            record.duration_ms = (time.monotonic() - start_time) * 1000
            self._add_records.append(record)

    async def _metrics_loop(self, update_callback: Callable):
        """Periodic metrics logging."""
        last_log = time.monotonic()
        last_count = 0

        while self.is_running:
            await asyncio.sleep(METRICS_INTERVAL)
            try:
                now = time.monotonic()
                elapsed = now - self._start_time
                rate = (self.total_added - last_count) / (now - last_log) * 60 if (now - last_log) > 0 else 0
                last_count = self.total_added
                last_log = now

                pool_stats = self._account_pool.get_stats()
                logger.info(
                    f"[Metrics] Added: {self.total_added} | "
                    f"Rate: {rate:.1f}/min | "
                    f"Queue: {self._member_queue.qsize()} | "
                    f"Accounts: {pool_stats['states']} | "
                    f"Down: {self.accounts_down} | "
                    f"Skips: {self.privacy_skips} | "
                    f"Elapsed: {int(elapsed/60)}m"
                )

                # Brief status update to UI
                if update_callback and self.total_added % 50 == 0:
                    await update_callback(
                        f"📊 **Live Status:** `{self.total_added}` added "
                        f"(`{rate:.0f}/min`) | "
                        f"Queue: `{self._member_queue.qsize()}` | "
                        f"Accounts: `{pool_stats['states'].get('ACTIVE', 0)}` active"
                    )
            except Exception as e:
                logger.error(f"[Metrics] Error: {e}")

    async def _cleanup(self):
        """Graceful cleanup of all resources."""
        self.is_running = False

        # Cancel all workers
        for w in self._workers:
            w.cancel()
        self._workers.clear()

        if self._metrics_task:
            self._metrics_task.cancel()

        # Shutdown account pool
        await self._account_pool.shutdown_all()

        # Clear queue
        while not self._member_queue.empty():
            try:
                self._member_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        logger.info("[Adder] Cleanup complete")

    def halt_engine(self):
        """Kills active loop variables instantly safely."""
        self.is_running = False
        # Cancel active workers
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        logger.info("[Adder] Engine halted by user")