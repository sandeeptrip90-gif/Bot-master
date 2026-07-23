#!/usr/bin/env python3
"""
Ultimate Enterprise Telegram Suite - Proxy Management Engine v3.0
Multi-Source Scraper | Auto-Validation | SOCKS5/HTTP Support | 10,000+ Account Scale
Filename: proxy_manager.py
"""

import os
import re
import io
import sys
import time
import json
import random
import asyncio
import logging
import aiohttp
import traceback
import ipaddress
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Set, Any, Union
from dataclasses import dataclass, field
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

# Try to import socks for SOCKS5 validation
try:
    import socks
    SOCKS_SUPPORT = True
except ImportError:
    SOCKS_SUPPORT = False

logger = logging.getLogger("ProxyManager")

# ────────────────────────────────────────────────────────────────
# PROXY SOURCE CONFIGURATION — 12+ sources, daily-updated
# ────────────────────────────────────────────────────────────────

PROXY_SOURCES = {
    "http": [
        # GitHub: TheSpeedX (30k+ proxies, daily updated)
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        # GitHub: proxifly (3k+ verified, every 5 min)
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
        # ProxyScrape API (official, every 5 min)
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
        # GitHub: Ian-Lusule (updated every 30 min)
        "https://raw.githubusercontent.com/Ian-Lusule/Proxies/main/proxies/http.txt",
        # GitHub: Thordata (daily updated, verified)
        "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/http.txt",
        # GitHub: iplocate (verified, every 30 min)
        "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/proxies/http.txt",
    ],
    "socks4": [
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks4&timeout=10000&country=all",
        "https://raw.githubusercontent.com/Ian-Lusule/Proxies/main/proxies/socks4.txt",
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks4/data.txt",
    ],
    "socks5": [
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all",
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.txt",
        "https://raw.githubusercontent.com/Ian-Lusule/Proxies/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/socks5.txt",
    ],
    "all": [
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.txt",
        "https://raw.githubusercontent.com/Ian-Lusule/Proxies/main/proxies/all_proxies.txt",
    ],
}

# ── Proxy validation targets ──
TELEGRAM_VALIDATION_HOSTS = [
    "149.154.175.50",   # Telegram DC 1
    "149.154.167.50",   # Telegram DC 2
    "149.154.175.100",  # Telegram DC 3
    "149.154.167.91",   # Telegram DC 4
    "91.108.56.100",    # Telegram DC 5
]

IP_ECHO_SERVICES = [
    "http://api.iplocate.io/ip",
    "http://httpbin.org/ip",
    "http://ip-api.com/line",
    "http://ifconfig.me/ip",
]

# ────────────────────────────────────────────────────────────────
# DATACLASSES
# ────────────────────────────────────────────────────────────────

@dataclass
class ProxyEntry:
    """Normalized proxy entry with health tracking."""
    host: str
    port: int
    protocol: str  # http, socks4, socks5
    username: Optional[str] = None
    password: Optional[str] = None
    source: str = "unknown"
    
    # Health metrics
    added_at: float = field(default_factory=time.time)
    last_checked: float = 0.0
    latency_ms: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    is_working: bool = False
    consecutive_fails: int = 0
    
    # Geo
    country: str = ""
    
    @property
    def url(self) -> str:
        """Return proxy URL for Telethon/proxy usage."""
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"
    
    @property
    def dict(self) -> dict:
        """Return as Telethon-compatible proxy dict."""
        if self.protocol == "socks5":
            return {
                "proxy_type": "socks5",
                "addr": self.host,
                "port": self.port,
                "username": self.username or "",
                "password": self.password or "",
                "rdns": True,
            }
        elif self.protocol == "socks4":
            return {
                "proxy_type": "socks4",
                "addr": self.host,
                "port": self.port,
                "username": self.username or "",
                "password": self.password or "",
                "rdns": True,
            }
        else:  # http
            return {
                "proxy_type": "http",
                "addr": self.host,
                "port": self.port,
                "username": self.username or "",
                "password": self.password or "",
                "rdns": True,
            }
    
    @property
    def telethon_proxy(self) -> tuple:
        """Return (socks.SOCKS5, host, port) tuple for Telethon safely."""
        try:
            import socks as socks_module
        except ImportError:
            raise ImportError("PySocks library missing! Run: pip install PySocks")
            
        proto_map = {
            "socks5": socks_module.SOCKS5,
            "socks4": socks_module.SOCKS4,
            "http": socks_module.HTTP,
        }
        proto = proto_map.get(self.protocol, socks_module.SOCKS5)
        if self.username and self.password:
            return (proto, self.host, self.port, True, self.username, self.password)
        return (proto, self.host, self.port, True)
    
    @property
    def is_dead(self) -> bool:
        """Consider dead after 3 consecutive failures."""
        return self.consecutive_fails >= 3
    
    def record_success(self, latency: float = 0.0) -> None:
        self.success_count += 1
        self.latency_ms = latency
        self.is_working = True
        self.consecutive_fails = 0
        self.last_checked = time.time()
    
    def record_failure(self) -> None:
        self.fail_count += 1
        self.consecutive_fails += 1
        self.last_checked = time.time()
        if self.consecutive_fails >= 3:
            self.is_working = False


@dataclass
class ProxyStats:
    """Aggregate proxy pool statistics."""
    total: int = 0
    working: int = 0
    http: int = 0
    socks4: int = 0
    socks5: int = 0
    avg_latency_ms: float = 0.0
    last_scan: float = 0.0
    sources_used: int = 0


# ────────────────────────────────────────────────────────────────
# ROBUST PROXY MANAGER
# ────────────────────────────────────────────────────────────────

class RobustProxyManager:
    """
    Enterprise-Grade Proxy Manager for 10,000+ Telegram Accounts.
    
    Features:
    - Scrapes 12+ free proxy sources (TheSpeedX, ProxyScrape, proxifly, etc.)
    - Validates proxies against Telegram DCs
    - SOCKS5 / SOCKS4 / HTTP support
    - Automatic rotation and dead proxy eviction
    - Weighted random selection (faster proxies chosen more often)
    - Geo-tracking
    - Persistent cache (proxies.json)
    """
    
    def __init__(self, proxy_file: str = "proxy.txt", cache_file: str = "proxies_cache.json"):
        self.proxy_file = Path(proxy_file)
        self.cache_file = Path(cache_file)
        
        # ── Proxy pool ──
        self._proxies: Dict[str, ProxyEntry] = OrderedDict()  # key = "host:port:protocol"
        self._working_proxies: List[str] = []  # keys of working proxies
        self._rotation_index: int = 0
        self._lock = asyncio.Lock()
        
        # ── Stats ──
        self.stats = ProxyStats()
        self._last_auto_scrape: float = 0.0
        self._auto_scrape_interval: int = 1800  # 30 min
        self._validation_concurrency: int = 50  # Parallel validation
        
        # ── Background task ──
        self._scan_task: Optional[asyncio.Task] = None
        self._is_scanning: bool = False
        
        # ── Load existing proxies ──
        self._load_from_file()
        self._load_from_cache()
        
        logger.info(f"🛡️ ProxyManager initialized. Pool: {len(self._proxies)} total, {len(self._working_proxies)} working.")
    
    # proxy_manager.py

    def get_proxy_by_preference(self, preferred_country: str = None) -> Optional[ProxyEntry]:
        """Get a working proxy, optionally preferring a country."""
        if not self._working_proxies:
            return None
        
        # If a country is given, try to match it
        candidates = []
        for key in self._working_proxies:
            entry = self._proxies.get(key)
            if entry and entry.country and entry.country.lower() == preferred_country.lower():
                candidates.append(entry)
        
        if not candidates:
            # Fallback to any working proxy
            candidates = [self._proxies[k] for k in self._working_proxies if self._proxies.get(k)]
        
        if not candidates:
            return None
        
        # Weighted random by latency (fastest preferred)
        max_latency = max(e.latency_ms for e in candidates) or 1
        weights = [max(1, max_latency - e.latency_ms + 1) for e in candidates]
        total = sum(weights)
        weights = [w/total for w in weights]
        return random.choices(candidates, weights=weights, k=1)[0]


    # ────────────────────────────────────────────────────────────
    # PUBLIC PROPERTIES (backward-compatible)
    # ────────────────────────────────────────────────────────────
    
    @property
    def working_count(self) -> int:
        """Number of verified working proxies."""
        return len(self._working_proxies)
    
    @property
    def total_count(self) -> int:
        """Total proxies in pool (including dead)."""
        return len(self._proxies)
    
    @property
    def healthy_proxies(self) -> int:
        """Alias for working_count (backward compat)."""
        return self.working_count
    
    # ────────────────────────────────────────────────────────────
    # FILE & CACHE PERSISTENCE
    # ────────────────────────────────────────────────────────────
    
    def _load_from_file(self) -> None:
        """Load proxies from proxy.txt (existing format support)."""
        if not self.proxy_file.exists():
            return
        
        try:
            with open(self.proxy_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    entry = self._parse_proxy_line(line, source="proxy.txt")
                    if entry:
                        key = f"{entry.host}:{entry.port}:{entry.protocol}"
                        if key not in self._proxies:
                            self._proxies[key] = entry
                            if entry.is_working:
                                self._working_proxies.append(key)
            
            logger.info(f"📂 Loaded {len(self._proxies)} proxies from {self.proxy_file}")
        except Exception as e:
            logger.debug(f"Could not load {self.proxy_file}: {e}")
    
    def _load_from_cache(self) -> None:
        """Load previously validated proxies from JSON cache."""
        if not self.cache_file.exists():
            return
        
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            loaded = 0
            for item in data:
                entry = ProxyEntry(
                    host=item.get("host", ""),
                    port=item.get("port", 0),
                    protocol=item.get("protocol", "http"),
                    username=item.get("username"),
                    password=item.get("password"),
                    source=item.get("source", "cache"),
                    is_working=item.get("is_working", False),
                    latency_ms=item.get("latency_ms", 0.0),
                    success_count=item.get("success_count", 0),
                    fail_count=item.get("fail_count", 0),
                    consecutive_fails=item.get("consecutive_fails", 0),
                    country=item.get("country", ""),
                    last_checked=item.get("last_checked", 0),
                )
                key = f"{entry.host}:{entry.port}:{entry.protocol}"
                if key not in self._proxies:
                    self._proxies[key] = entry
                    if entry.is_working and not entry.is_dead:
                        self._working_proxies.append(key)
                    loaded += 1
            
            if loaded:
                logger.info(f"📦 Loaded {loaded} cached proxies.")
        except Exception as e:
            logger.debug(f"Cache load error: {e}")
    
    def _save_cache(self) -> None:
        """Persist current proxy pool to JSON cache."""
        try:
            data = []
            for entry in self._proxies.values():
                data.append({
                    "host": entry.host,
                    "port": entry.port,
                    "protocol": entry.protocol,
                    "username": entry.username,
                    "password": entry.password,
                    "source": entry.source,
                    "is_working": entry.is_working,
                    "latency_ms": entry.latency_ms,
                    "success_count": entry.success_count,
                    "fail_count": entry.fail_count,
                    "consecutive_fails": entry.consecutive_fails,
                    "country": entry.country,
                    "last_checked": entry.last_checked,
                })
            
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            
            logger.debug(f"💾 Saved {len(data)} proxies to cache.")
        except Exception as e:
            logger.error(f"Cache save error: {e}")
    
    # ────────────────────────────────────────────────────────────
    # PROXY PARSING
    # ────────────────────────────────────────────────────────────
    
    @staticmethod
    def _parse_proxy_line(line: str, source: str = "unknown") -> Optional[ProxyEntry]:
        """Parse a single proxy line from any format."""
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        
        # Remove protocol prefix if present
        cleaned = re.sub(r'^(http|https|socks4|socks5)://', '', line, flags=re.IGNORECASE)
        
        # Format: host:port:user:pass OR host:port OR user:pass@host:port
        parts = cleaned.split(":")
        
        try:
            if len(parts) == 4:
                # host:port:user:pass
                host, port_str, user, passwd = parts
                port = int(port_str)
                protocol = "socks5"  # default for authenticated
                return ProxyEntry(host=host, port=port, protocol=protocol,
                                  username=user, password=passwd, source=source)
            elif len(parts) == 2:
                # host:port
                host, port_str = parts
                port = int(port_str)
                # Auto-detect protocol based on port
                if port in (1080, 1081):
                    protocol = "socks5"
                elif port == 1085:
                    protocol = "socks4"
                else:
                    protocol = "http"
                return ProxyEntry(host=host, port=port, protocol=protocol, source=source)
            elif len(parts) == 3 and '@' not in line:
                # Could be host:port:protocol or socks5://host:port
                host, port_str, proto_or_extra = parts
                port = int(port_str)
                if proto_or_extra.lower() in ("http", "https", "socks4", "socks5"):
                    return ProxyEntry(host=host, port=port, protocol=proto_or_extra.lower(), source=source)
                # Treat extra as password? No, skip.
                return None
            elif '@' in line:
                # user:pass@host:port
                match = re.match(r'(.+?):(.+?)@(.+?):(\d+)', line)
                if match:
                    user, passwd, host, port_str = match.groups()
                    port = int(port_str)
                    protocol = "socks5" if port in (1080, 1081) else "http"
                    return ProxyEntry(host=host, port=port, protocol=protocol,
                                      username=user, password=passwd, source=source)
            return None
        except (ValueError, IndexError):
            return None
    
    # ────────────────────────────────────────────────────────────
    # PROXY SCRAPING (from free sources)
    # ────────────────────────────────────────────────────────────
    
    async def scrape_all_sources(self, timeout: int = 15) -> int:
        """
        Scrape proxies from ALL configured sources.
        Returns count of new unique proxies discovered.
        """
        async with self._lock:
            return await self._scrape_all_sources_locked(timeout)
    
    async def _scrape_all_sources_locked(self, timeout: int = 15) -> int:
        """Internal scrape (lock already held)."""
        connector = aiohttp.TCPConnector(limit=20, force_close=True)
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        
        new_count = 0
        source_count = 0
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout_obj) as session:
            tasks = []
            
            # Collect all unique URLs across all protocols
            seen_urls = set()
            all_urls = []
            for protocol, urls in PROXY_SOURCES.items():
                for url in urls:
                    if url not in seen_urls:
                        seen_urls.add(url)
                        all_urls.append((protocol, url))
            
            # Scrape all sources concurrently
            for protocol, url in all_urls:
                tasks.append(self._scrape_single_source(session, url, protocol))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, Exception):
                    continue
                if isinstance(result, list):
                    for entry in result:
                        key = f"{entry.host}:{entry.port}:{entry.protocol}"
                        if key not in self._proxies:
                            self._proxies[key] = entry
                            new_count += 1
                    source_count += 1 if result else 0
        
        self.stats.sources_used = source_count
        self._last_auto_scrape = time.time()
        
        logger.info(f"🌐 Scraped {new_count} new proxies from {source_count}/{len(PROXY_SOURCES)} sources. Pool: {len(self._proxies)}")
        return new_count
    
    async def _scrape_single_source(
        self, session: aiohttp.ClientSession, url: str, default_protocol: str
    ) -> List[ProxyEntry]:
        """Scrape proxies from a single URL source."""
        try:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status != 200:
                    return []
                
                text = await resp.text()
                entries = []
                
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    
                    entry = self._parse_proxy_line(line, source=url.split("/")[2])
                    if entry:
                        entries.append(entry)
                    else:
                        # Try direct format: ip:port
                        match = re.match(r'^(\d+\.\d+\.\d+\.\d+):(\d+)$', line)
                        if match:
                            host, port_str = match.groups()
                            try:
                                port = int(port_str)
                                protocol = default_protocol if default_protocol != "all" else (
                                    "socks5" if port in (1080, 1081) else "http"
                                )
                                entries.append(ProxyEntry(host=host, port=port, protocol=protocol, source=url.split("/")[2]))
                            except ValueError:
                                pass
                
                logger.debug(f"  📡 {url.split('/')[2]}: {len(entries)} proxies")
                return entries
                
        except (asyncio.TimeoutError, aiohttp.ClientError, Exception) as e:
            logger.debug(f"  ⚠️ {url.split('/')[2]}: {str(e)[:50]}")
            return []
    
    # ────────────────────────────────────────────────────────────
    # PROXY VALIDATION (PATCHED: ASYNC-SAFE & THREADED)
    # ────────────────────────────────────────────────────────────
    
    async def validate_proxy(self, entry: ProxyEntry, timeout: int = 8) -> bool:
        """
        Validate a single proxy by connecting to Telegram DCs and echo services.
        Returns True if proxy is working.
        """
        # Test against Telegram DCs first (fast path via Thread Executor)
        telegram_ok = await self._test_telegram_connection(entry, timeout)
        
        if telegram_ok:
            # Measure latency via echo service (via Thread Executor)
            latency = await self._measure_latency(entry, timeout)
            entry.record_success(latency)
            return True
        
        entry.record_failure()
        return False
    
    def _test_telegram_connection_sync(self, entry: ProxyEntry, timeout: int) -> bool:
        """Synchronous blocking connection logic (runs safely in a background thread)."""
        try:
            import socks
        except ImportError:
            logger.error("PySocks library missing! Run: pip install PySocks")
            return False
            
        proto_map = {
            "socks5": socks.SOCKS5,
            "socks4": socks.SOCKS4,
            "http": socks.HTTP,
        }
        proto = proto_map.get(entry.protocol, socks.SOCKS5)
        
        for tg_host in random.sample(TELEGRAM_VALIDATION_HOSTS, min(2, len(TELEGRAM_VALIDATION_HOSTS))):
            try:
                s = socks.socksocket()
                s.settimeout(timeout)
                
                if entry.username and entry.password:
                    s.set_proxy(proto, entry.host, entry.port, True, entry.username, entry.password)
                else:
                    s.set_proxy(proto, entry.host, entry.port, True)
                
                s.connect((tg_host, 443))
                s.send(b"\x00" * 4)  # Minimal Telegram MTProto probe
                s.close()
                return True
            except Exception:
                continue
        
        return False

    async def _test_telegram_connection(self, entry: ProxyEntry, timeout: int) -> bool:
        """Test proxy against Telegram datacenters without blocking the asyncio loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._test_telegram_connection_sync, entry, timeout)
    
    def _measure_latency_sync(self, entry: ProxyEntry, timeout: int) -> float:
        """Synchronous blocking latency logic (runs safely in a background thread)."""
        try:
            import socks
        except ImportError:
            return 9999.0
            
        proto_map = {
            "socks5": socks.SOCKS5,
            "socks4": socks.SOCKS4,
            "http": socks.HTTP,
        }
        proto = proto_map.get(entry.protocol, socks.SOCKS5)
        
        for echo_url in IP_ECHO_SERVICES:
            try:
                start = time.time()
                
                s = socks.socksocket()
                s.settimeout(timeout)
                
                if entry.username and entry.password:
                    s.set_proxy(proto, entry.host, entry.port, True, entry.username, entry.password)
                else:
                    s.set_proxy(proto, entry.host, entry.port, True)
                
                # Parse URL
                parsed = urlparse(echo_url)
                s.connect((parsed.hostname, parsed.port or 80))
                
                request = f"GET {parsed.path or '/'} HTTP/1.1\r\nHost: {parsed.hostname}\r\nConnection: close\r\n\r\n"
                s.send(request.encode())
                response = s.recv(1024)
                s.close()
                
                elapsed = (time.time() - start) * 1000  # ms
                return elapsed
            except Exception:
                continue
        
        return 9999.0  # High latency if all echo services fail

    async def _measure_latency(self, entry: ProxyEntry, timeout: int) -> float:
        """Measure proxy latency without blocking the asyncio loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._measure_latency_sync, entry, timeout)
    
    async def validate_pool(
        self, max_proxies: int = 500, concurrency: int = None
    ) -> Tuple[int, int]:
        """
        Validate all proxies in the pool. 
        Returns (working_count, dead_count).
        """
        if concurrency is None:
            concurrency = self._validation_concurrency
        
        semaphore = asyncio.Semaphore(concurrency)
        
        # Prioritize unvalidated proxies first, then previously working, then dead
        to_validate = []
        for key, entry in self._proxies.items():
            if entry.is_dead and entry.consecutive_fails >= 5:
                continue  # Skip completely dead proxies
            to_validate.append((key, entry))
        
        # Limit batch size
        if len(to_validate) > max_proxies:
            # Prioritize: unvalidated > working > dead
            unvalidated = [(k, e) for k, e in to_validate if e.last_checked == 0]
            working = [(k, e) for k, e in to_validate if e.is_working]
            dead = [(k, e) for k, e in to_validate if not e.is_working and e.last_checked > 0]
            
            # Take all unvalidated, then fill with working, then dead
            prioritized = unvalidated[:max_proxies]
            remaining = max_proxies - len(prioritized)
            if remaining > 0:
                prioritized.extend(working[:remaining])
                remaining = max_proxies - len(prioritized)
                if remaining > 0:
                    prioritized.extend(dead[:remaining])
            to_validate = prioritized
        
        logger.info(f"🔍 Validating {len(to_validate)} proxies (concurrency={concurrency})...")
        
        async def _validate_one(key: str, entry: ProxyEntry) -> bool:
            async with semaphore:
                return await self.validate_proxy(entry)
        
        tasks = [_validate_one(k, e) for k, e in to_validate]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Update working list
        working_keys = set()
        for i, (key, entry) in enumerate(to_validate):
            result = results[i]
            if isinstance(result, bool) and result:
                working_keys.add(key)
            elif isinstance(result, Exception):
                entry.record_failure()
        
        self._working_proxies = [k for k in self._working_proxies if k in working_keys or self._proxies.get(k, ProxyEntry("", 0, "")).is_working]
        for key in working_keys:
            if key not in self._working_proxies:
                self._working_proxies.append(key)
        
        # Evict completely dead proxies (7+ consecutive fails)
        dead_keys = [k for k, e in self._proxies.items() if e.consecutive_fails >= 7]
        for k in dead_keys:
            self._proxies.pop(k, None)
            if k in self._working_proxies:
                self._working_proxies.remove(k)
        
        # Update stats
        self.stats.total = len(self._proxies)
        self.stats.working = len(self._working_proxies)
        self.stats.last_scan = time.time()
        
        # Compute avg latency
        latencies = [e.latency_ms for e in self._proxies.values() if e.latency_ms > 0]
        self.stats.avg_latency_ms = sum(latencies) / len(latencies) if latencies else 0
        
        # Count protocols
        self.stats.http = sum(1 for e in self._proxies.values() if e.protocol == "http")
        self.stats.socks4 = sum(1 for e in self._proxies.values() if e.protocol == "socks4")
        self.stats.socks5 = sum(1 for e in self._proxies.values() if e.protocol == "socks5")
        
        # Save cache
        self._save_cache()
        
        logger.info(f"✅ Validation complete: {len(working_keys)} working, {len(to_validate) - len(working_keys)} dead. Pool: {len(self._proxies)}")
        return (len(working_keys), len(to_validate) - len(working_keys))
    
    # ────────────────────────────────────────────────────────────
    # PROXY ROTATION & SELECTION
    # ────────────────────────────────────────────────────────────
    
    def get_proxy(self, protocol: str = "socks5") -> Optional[ProxyEntry]:
        """
        Get a working proxy using weighted random selection.
        Faster proxies are selected more frequently.
        Returns None if no working proxies available.
        """
        if not self._working_proxies:
            return None
        
        # Filter by protocol preference
        candidates = []
        for key in self._working_proxies:
            entry = self._proxies.get(key)
            if entry and entry.is_working and not entry.is_dead:
                if protocol == "any" or entry.protocol == protocol:
                    candidates.append(entry)
        
        if not candidates:
            # Fallback: any working proxy
            candidates = [self._proxies[k] for k in self._working_proxies if self._proxies.get(k) and self._proxies[k].is_working]
        
        if not candidates:
            return None
        
        # Weighted random: lower latency = higher weight
        max_latency = max(e.latency_ms for e in candidates) or 1
        weights = [max(1, max_latency - e.latency_ms + 1) for e in candidates]
        total_weight = sum(weights)
        weights = [w / total_weight for w in weights]
        
        chosen = random.choices(candidates, weights=weights, k=1)[0]
        return chosen
    
    def get_secured_proxy(self) -> Optional[dict]:
        """
        Return a Telethon-compatible proxy dict for use with TelegramClient.
        This is the primary method used by master.py's shared_login_process.
        Returns None if no proxy available.
        """
        entry = self.get_proxy("socks5")
        if not entry:
            entry = self.get_proxy("any")
        if not entry:
            return None
        
        return entry.dict
    
    def get_random_working_proxy(self) -> Optional[str]:
        """
        Return proxy URL string (backward-compatible method).
        """
        entry = self.get_proxy("any")
        if entry:
            return entry.url
        return None
    
    def get_proxy_sync(self) -> Optional[dict]:
        """
        Synchronous proxy getter (for non-async contexts).
        """
        return self.get_secured_proxy()
    
    # ────────────────────────────────────────────────────────────
    # BACKGROUND AUTO-SCAN PIPELINE
    # ────────────────────────────────────────────────────────────
    
    async def run_pipeline_scan(self) -> Dict[str, int]:
        """
        Full pipeline: scrape → validate → evict → cache.
        Designed to run as a background task.
        """
        self._is_scanning = True
        start_time = time.time()
        
        logger.info("🚀 Starting proxy pipeline scan...")
        
        # Phase 1: Scrape new proxies
        new_count = await self.scrape_all_sources()
        
        # Phase 2: Validate pool
        working, dead = await self.validate_pool(max_proxies=500)
        
        # Phase 3: If we still have too few, scrape and validate more aggressively
        if working < 50:
            logger.info(f"⚠️ Only {working} working proxies. Scraping again for more...")
            more_new = await self._scrape_additional_sources()
            if more_new > 0:
                working2, dead2 = await self.validate_pool(max_proxies=300)
                working += max(0, working2 - working)
                dead += dead2
        
        elapsed = time.time() - start_time
        
        self._is_scanning = False
        
        result = {
            "new_scraped": new_count,
            "working": working,
            "dead": dead,
            "total_pool": self.stats.total,
            "elapsed_seconds": round(elapsed, 1),
            "sources_scraped": self.stats.sources_used,
        }
        
        logger.info(
            f"✅ Pipeline complete: {result['working']}W/{result['dead']}D "
            f"({result['new_scraped']} new) in {result['elapsed_seconds']}s"
        )
        
        return result
    
    async def _scrape_additional_sources(self) -> int:
        """Scrape from fallback/alternative sources when pool is low."""
        fallback_urls = [
            # Additional sources only hit when needed
            "https://openproxylist.xyz/http.txt",
            "https://multiproxy.org/txt_all/proxy.txt",
            "https://www.proxy-list.download/api/v1/get?type=http",
            "https://www.proxy-list.download/api/v1/get?type=socks5",
            "https://proxyspace.pro/http.txt",
            "https://proxyspace.pro/socks5.txt",
        ]
        
        connector = aiohttp.TCPConnector(limit=10, force_close=True)
        timeout_obj = aiohttp.ClientTimeout(total=10)
        
        new_count = 0
        async with aiohttp.ClientSession(connector=connector, timeout=timeout_obj) as session:
            tasks = []
            for url in fallback_urls:
                proto = "socks5" if "socks" in url else "http"
                tasks.append(self._scrape_single_source(session, url, proto))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, list):
                    for entry in result:
                        key = f"{entry.host}:{entry.port}:{entry.protocol}"
                        if key not in self._proxies:
                            self._proxies[key] = entry
                            new_count += 1
        
        logger.info(f"🔄 Fallback scrape: {new_count} new proxies")
        return new_count
    
    # ────────────────────────────────────────────────────────────
    # MANUAL PROXY MANAGEMENT
    # ────────────────────────────────────────────────────────────
    
    def add_proxy(self, line: str, source: str = "manual") -> bool:
        """Add a single proxy from a line string."""
        entry = self._parse_proxy_line(line, source=source)
        if not entry:
            return False
        key = f"{entry.host}:{entry.port}:{entry.protocol}"
        if key not in self._proxies:
            self._proxies[key] = entry
            return True
        return False
    
    def add_proxies_from_file(self, filepath: str, source: str = "file") -> int:
        """Bulk add proxies from a file."""
        path = Path(filepath)
        if not path.exists():
            return 0
        
        count = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if self.add_proxy(line, source=source):
                    count += 1
        
        return count
    
    def remove_proxy(self, host: str, port: int, protocol: str = "http") -> bool:
        """Remove a specific proxy from the pool."""
        key = f"{host}:{port}:{protocol}"
        if key in self._proxies:
            del self._proxies[key]
            if key in self._working_proxies:
                self._working_proxies.remove(key)
            return True
        return False
    
    def mark_proxy_failed(self, proxy_url: str) -> None:
        """
        Mark a proxy as failed (called when Telethon connection fails with this proxy).
        proxy_url format: socks5://host:port or http://host:port
        """
        cleaned = re.sub(r'^(http|https|socks4|socks5)://', '', proxy_url)
        if ":" in cleaned:
            parts = cleaned.split(":")
            host = parts[0]
            try:
                port = int(parts[1])
            except (ValueError, IndexError):
                return
            
            # Try all protocols for this host:port
            for proto in ("socks5", "socks4", "http"):
                key = f"{host}:{port}:{proto}"
                if key in self._proxies:
                    self._proxies[key].record_failure()
                    if self._proxies[key].is_dead:
                        if key in self._working_proxies:
                            self._working_proxies.remove(key)
                    break
    
    # ────────────────────────────────────────────────────────────
    # POOL STATISTICS & REPORTING
    # ────────────────────────────────────────────────────────────
    
    def get_stats(self) -> ProxyStats:
        """Get current proxy pool statistics."""
        self.stats.total = len(self._proxies)
        self.stats.working = len(self._working_proxies)
        self.stats.http = sum(1 for e in self._proxies.values() if e.protocol == "http")
        self.stats.socks4 = sum(1 for e in self._proxies.values() if e.protocol == "socks4")
        self.stats.socks5 = sum(1 for e in self._proxies.values() if e.protocol == "socks5")
        
        latencies = [e.latency_ms for e in self._proxies.values() if e.latency_ms > 0]
        self.stats.avg_latency_ms = sum(latencies) / len(latencies) if latencies else 0
        
        return self.stats
    
    def get_status_text(self) -> str:
        """Human-readable status for Telegram UI."""
        stats = self.get_stats()
        return (
            f"🛡️ **Proxy Infrastructure**\n"
            f"• Total Pool: `{stats.total}`\n"
            f"• ✅ Working: `{stats.working}`\n"
            f"• HTTP: `{stats.http}` | SOCKS4: `{stats.socks4}` | SOCKS5: `{stats.socks5}`\n"
            f"• Avg Latency: `{stats.avg_latency_ms:.0f}ms`\n"
            f"• Sources: `{stats.sources_used}` active\n"
            f"• Last Scan: `{datetime.fromtimestamp(stats.last_scan).strftime('%H:%M:%S') if stats.last_scan else 'Never'}`"
        )
    
    # ────────────────────────────────────────────────────────────
    # CLEANUP & SHUTDOWN
    # ────────────────────────────────────────────────────────────
    
    async def shutdown(self) -> None:
        """Graceful shutdown: save cache, cancel tasks."""
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        
        self._save_cache()
        logger.info("🛑 ProxyManager shutdown complete.")
    
    async def cleanup_dead_proxies(self, max_consecutive_fails: int = 5) -> int:
        """Remove proxies with excessive consecutive failures."""
        dead_keys = [
            k for k, e in self._proxies.items()
            if e.consecutive_fails >= max_consecutive_fails
        ]
        for k in dead_keys:
            self._proxies.pop(k, None)
            if k in self._working_proxies:
                self._working_proxies.remove(k)
        
        if dead_keys:
            logger.info(f"🧹 Cleaned {len(dead_keys)} dead proxies.")
            self._save_cache()
        
        return len(dead_keys)