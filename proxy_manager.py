#!/usr/bin/env python3
"""
Robust Telegram Proxy Manager — Validates & rotates proxies for 10,000+ account scale.
- Scrapes 12+ free proxy sources (daily updated)
- Validates with strict latency checks (< 7s, rejects high latency)
- SOCKS5/SOCKS4/HTTP support
- Automatic dead proxy eviction
- Telethon-compatible output
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
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
        "https://raw.githubusercontent.com/Ian-Lusule/Proxies/main/proxies/http.txt",
        "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/http.txt",
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
}

# Telegram datacenters for validation
TELEGRAM_VALIDATION_HOSTS = [
    "149.154.175.50",
    "149.154.167.50",
    "149.154.175.100",
    "149.154.167.91",
    "91.108.56.100",
]

IP_ECHO_SERVICES = [
    "http://api.iplocate.io/ip",
    "http://httpbin.org/ip",
    "http://ifconfig.me/ip",
]

# ────────────────────────────────────────────────────────────────
# DATACLASSES
# ────────────────────────────────────────────────────────────────

@dataclass
class ProxyEntry:
    """Normalized proxy with health tracking."""
    host: str
    port: int
    protocol: str
    username: Optional[str] = None
    password: Optional[str] = None
    source: str = "unknown"

    added_at: float = field(default_factory=time.time)
    last_checked: float = 0.0
    latency_ms: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    is_working: bool = False
    consecutive_fails: int = 0
    country: str = ""

    @property
    def url(self) -> str:
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"

    @property
    def dict(self) -> dict:
        """Telethon-compatible dict."""
        return {
            "proxy_type": self.protocol,
            "addr": self.host,
            "port": self.port,
            "username": self.username or "",
            "password": self.password or "",
            "rdns": True,
        }

    @property
    def is_dead(self) -> bool:
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
    """Pool statistics."""
    total: int = 0
    working: int = 0
    http: int = 0
    socks4: int = 0
    socks5: int = 0
    avg_latency_ms: float = 0.0
    last_scan: float = 0.0
    sources_used: int = 0


# ────────────────────────────────────────────────────────────────
# PROXY MANAGER
# ────────────────────────────────────────────────────────────────

class RobustProxyManager:
    """Enterprise proxy manager for Telegram bot at scale."""

    def __init__(self, proxy_file: str = "proxy.txt", cache_file: str = "proxies_cache.json"):
        self.proxy_file = Path(proxy_file)
        self.cache_file = Path(cache_file)

        self._proxies: Dict[str, ProxyEntry] = OrderedDict()
        self._working_proxies: List[str] = []
        self._lock = asyncio.Lock()

        self.stats = ProxyStats()
        self._validation_concurrency: int = 50
        self._last_auto_scrape: float = 0.0

        self._load_from_file()
        self._load_from_cache()

        logger.info(f"ProxyManager initialized: {len(self._proxies)} total, {len(self._working_proxies)} working.")

    def _load_from_file(self) -> None:
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
            logger.info(f"Loaded {len(self._proxies)} proxies from {self.proxy_file}")
        except Exception as e:
            logger.debug(f"Could not load {self.proxy_file}: {e}")

    def _load_from_cache(self) -> None:
        if not self.cache_file.exists():
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

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

            logger.info(f"Loaded {len([e for e in self._proxies.values() if e.is_working])} working proxies from cache.")
        except Exception as e:
            logger.debug(f"Cache load error: {e}")

    def _save_cache(self) -> None:
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
        except Exception as e:
            logger.error(f"Cache save error: {e}")

    @staticmethod
    def _parse_proxy_line(line: str, source: str = "unknown") -> Optional[ProxyEntry]:
        """Parse proxy from any standard format."""
        line = line.strip()
        if not line or line.startswith("#"):
            return None

        cleaned = re.sub(r'^(http|https|socks4|socks5)://', '', line, flags=re.IGNORECASE)
        parts = cleaned.split(":")

        try:
            if len(parts) == 4:
                host, port_str, user, passwd = parts
                return ProxyEntry(host=host, port=int(port_str), protocol="socks5",
                                  username=user, password=passwd, source=source)
            elif len(parts) == 2:
                host, port_str = parts
                port = int(port_str)
                protocol = "socks5" if port in (1080, 1081) else "http"
                return ProxyEntry(host=host, port=port, protocol=protocol, source=source)
            elif '@' in line:
                match = re.match(r'(.+?):(.+?)@(.+?):(\d+)', line)
                if match:
                    user, passwd, host, port_str = match.groups()
                    port = int(port_str)
                    return ProxyEntry(host=host, port=port, protocol="socks5",
                                      username=user, password=passwd, source=source)
            return None
        except (ValueError, IndexError):
            return None

    async def scrape_all_sources(self, timeout: int = 15) -> int:
        """Scrape all proxy sources. Returns new proxy count."""
        async with self._lock:
            connector = aiohttp.TCPConnector(limit=20, force_close=True)
            timeout_obj = aiohttp.ClientTimeout(total=timeout)

            new_count = 0
            source_count = 0

            async with aiohttp.ClientSession(connector=connector, timeout=timeout_obj) as session:
                tasks = []
                seen_urls = set()
                all_urls = []

                for protocol, urls in PROXY_SOURCES.items():
                    for url in urls:
                        if url not in seen_urls:
                            seen_urls.add(url)
                            all_urls.append((protocol, url))

                for protocol, url in all_urls:
                    tasks.append(self._scrape_single_source(session, url, protocol))

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, list):
                        for entry in result:
                            key = f"{entry.host}:{entry.port}:{entry.protocol}"
                            if key not in self._proxies:
                                self._proxies[key] = entry
                                new_count += 1
                        if result:
                            source_count += 1

            logger.info(f"Scraped {new_count} new proxies from {source_count} sources. Pool: {len(self._proxies)}")
            return new_count

    async def _scrape_single_source(
        self, session: aiohttp.ClientSession, url: str, default_protocol: str
    ) -> List[ProxyEntry]:
        """Scrape a single source URL."""
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
                        match = re.match(r'^(\d+\.\d+\.\d+\.\d+):(\d+)$', line)
                        if match:
                            host, port_str = match.groups()
                            try:
                                port = int(port_str)
                                protocol = default_protocol if default_protocol != "all" else "http"
                                entries.append(ProxyEntry(host=host, port=port, protocol=protocol, source=url.split("/")[2]))
                            except ValueError:
                                pass

                return entries
        except Exception as e:
            logger.debug(f"Source {url.split('/')[2]} error: {str(e)[:50]}")
            return []

    # ────────────────────────────────────────────────────────────
    # STRICT VALIDATION WITH LATENCY CHECKS
    # ────────────────────────────────────────────────────────────

    async def validate_proxy(self, entry: ProxyEntry, timeout: int = 7) -> bool:
        """
        Strict proxy validation:
        - Must respond within 7 seconds
        - Must have latency < 7000ms (leaves buffer for Telethon's 10s timeout)
        - Must successfully connect to Telegram datacenter

        Returns True only if proxy is reliable.
        """
        # Measure latency first (fastest check)
        latency = await self._measure_latency(entry, timeout=5)

        if latency >= 7000:
            entry.record_failure()
            return False

        # Test Telegram connectivity
        telegram_ok = await self._test_telegram_connection(entry, timeout=5)

        if telegram_ok:
            entry.record_success(latency)
            return True

        entry.record_failure()
        return False

    def _test_telegram_connection_sync(self, entry: ProxyEntry, timeout: int) -> bool:
        """Test connection to Telegram DC (runs in thread executor)."""
        try:
            import socks
        except ImportError:
            logger.error("PySocks missing! Run: pip install PySocks")
            return False

        proto_map = {
            "socks5": socks.SOCKS5,
            "socks4": socks.SOCKS4,
            "http": socks.HTTP,
        }
        proto = proto_map.get(entry.protocol, socks.SOCKS5)

        # Try 3 DCs, not just 2
        for tg_host in random.sample(TELEGRAM_VALIDATION_HOSTS, min(3, len(TELEGRAM_VALIDATION_HOSTS))):
            try:
                s = socks.socksocket()
                s.settimeout(timeout)

                if entry.username and entry.password:
                    s.set_proxy(proto, entry.host, entry.port, True, entry.username, entry.password)
                else:
                    s.set_proxy(proto, entry.host, entry.port, True)

                s.connect((tg_host, 443))
                s.send(b"\x00" * 4)
                s.close()
                return True
            except Exception:
                continue

        return False

    async def _test_telegram_connection(self, entry: ProxyEntry, timeout: int) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._test_telegram_connection_sync, entry, timeout)

    def _measure_latency_sync(self, entry: ProxyEntry, timeout: int) -> float:
        """Measure actual latency (runs in thread executor)."""
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

                parsed = urlparse(echo_url)
                s.connect((parsed.hostname, parsed.port or 80))

                request = f"GET {parsed.path or '/'} HTTP/1.1\r\nHost: {parsed.hostname}\r\nConnection: close\r\n\r\n"
                s.send(request.encode())
                s.recv(1024)
                s.close()

                elapsed = (time.time() - start) * 1000
                return elapsed
            except Exception:
                continue

        return 9999.0

    async def _measure_latency(self, entry: ProxyEntry, timeout: int) -> float:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._measure_latency_sync, entry, timeout)

    async def validate_pool(self, max_proxies: int = 500, concurrency: int = None) -> Tuple[int, int]:
        """Validate pool. Returns (working, dead) count."""
        if concurrency is None:
            concurrency = self._validation_concurrency

        semaphore = asyncio.Semaphore(concurrency)

        to_validate = [
            (k, e) for k, e in self._proxies.items()
            if e.consecutive_fails < 5  # Skip completely dead
        ]

        if len(to_validate) > max_proxies:
            unvalidated = [(k, e) for k, e in to_validate if e.last_checked == 0]
            working = [(k, e) for k, e in to_validate if e.is_working]
            dead = [(k, e) for k, e in to_validate if not e.is_working and e.last_checked > 0]

            prioritized = unvalidated[:max_proxies]
            remaining = max_proxies - len(prioritized)
            if remaining > 0:
                prioritized.extend(working[:remaining])
            to_validate = prioritized

        logger.info(f"Validating {len(to_validate)} proxies...")

        async def _validate_one(key: str, entry: ProxyEntry) -> bool:
            async with semaphore:
                return await self.validate_proxy(entry)

        tasks = [_validate_one(k, e) for k, e in to_validate]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        working_keys = set()
        for i, (key, entry) in enumerate(to_validate):
            if isinstance(results[i], bool) and results[i]:
                working_keys.add(key)

        self._working_proxies = [k for k in self._working_proxies if k in working_keys]
        for key in working_keys:
            if key not in self._working_proxies:
                self._working_proxies.append(key)

        # Remove completely dead proxies
        dead_keys = [k for k, e in self._proxies.items() if e.consecutive_fails >= 7]
        for k in dead_keys:
            self._proxies.pop(k, None)
            if k in self._working_proxies:
                self._working_proxies.remove(k)

        self.stats.total = len(self._proxies)
        self.stats.working = len(self._working_proxies)
        self.stats.last_scan = time.time()

        latencies = [e.latency_ms for e in self._proxies.values() if e.latency_ms > 0]
        self.stats.avg_latency_ms = sum(latencies) / len(latencies) if latencies else 0

        self.stats.http = sum(1 for e in self._proxies.values() if e.protocol == "http")
        self.stats.socks4 = sum(1 for e in self._proxies.values() if e.protocol == "socks4")
        self.stats.socks5 = sum(1 for e in self._proxies.values() if e.protocol == "socks5")

        self._save_cache()

        logger.info(f"Validation done: {len(working_keys)} working, {len(to_validate) - len(working_keys)} dead.")
        return (len(working_keys), len(dead_keys))

    def get_proxy(self, protocol: str = "socks5") -> Optional[ProxyEntry]:
        """Get a working proxy (weighted by latency)."""
        if not self._working_proxies:
            return None

        candidates = []
        for key in self._working_proxies:
            entry = self._proxies.get(key)
            if entry and entry.is_working and not entry.is_dead:
                if protocol == "any" or entry.protocol == protocol:
                    candidates.append(entry)

        if not candidates:
            candidates = [self._proxies[k] for k in self._working_proxies if self._proxies.get(k) and self._proxies[k].is_working]

        if not candidates:
            return None

        max_latency = max(e.latency_ms for e in candidates) or 1
        weights = [max(1, max_latency - e.latency_ms + 1) for e in candidates]
        total_weight = sum(weights)
        weights = [w / total_weight for w in weights]

        return random.choices(candidates, weights=weights, k=1)[0]

    def get_secured_proxy(self) -> Optional[dict]:
        """Return Telethon-compatible proxy dict."""
        entry = self.get_proxy("socks5")
        if not entry:
            entry = self.get_proxy("any")
        if not entry:
            logger.warning("No working proxies available!")
            return None

        logger.debug(f"Using proxy: {entry.host}:{entry.port} ({entry.protocol}, latency={entry.latency_ms:.0f}ms)")
        return entry.dict

    def get_random_working_proxy(self) -> Optional[str]:
        """Return proxy URL string."""
        entry = self.get_proxy("any")
        return entry.url if entry else None

    def mark_proxy_failed(self, proxy_url: str) -> None:
        """Mark proxy as failed when Telethon connection fails."""
        cleaned = re.sub(r'^(http|https|socks4|socks5)://', '', proxy_url)
        if ":" in cleaned:
            parts = cleaned.split(":")
            host = parts[0]
            try:
                port = int(parts[1])
            except (ValueError, IndexError):
                return

            for proto in ("socks5", "socks4", "http"):
                key = f"{host}:{port}:{proto}"
                if key in self._proxies:
                    self._proxies[key].record_failure()
                    logger.warning(f"Marked proxy as failed: {host}:{port} (fails={self._proxies[key].consecutive_fails})")
                    if self._proxies[key].is_dead:
                        if key in self._working_proxies:
                            self._working_proxies.remove(key)
                    break

    def get_status_text(self) -> str:
        """Human-readable status."""
        stats = self.stats
        return (
            f"🛡️ **Proxy Infrastructure**\n"
            f"• Total Pool: `{stats.total}`\n"
            f"• ✅ Working: `{stats.working}`\n"
            f"• HTTP: `{stats.http}` | SOCKS4: `{stats.socks4}` | SOCKS5: `{stats.socks5}`\n"
            f"• Avg Latency: `{stats.avg_latency_ms:.0f}ms`\n"
            f"• Last Scan: `{datetime.fromtimestamp(stats.last_scan).strftime('%H:%M:%S') if stats.last_scan else 'Never'}`"
        )

    async def shutdown(self) -> None:
        self._save_cache()
        logger.info("ProxyManager shutdown.")
