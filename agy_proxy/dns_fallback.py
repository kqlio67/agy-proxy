"""
Resilient DNS Fallback Resolver.
Provides an in-memory cached DNS resolver using public DNS servers (8.8.8.8, 1.1.1.1)
when the local system resolver (e.g. Android hotspot or broken router DNS) fails
with socket.gaierror / [Errno -3] Temporary failure in name resolution.
"""

import logging
import socket
import struct
import threading
import time

logger = logging.getLogger("agy_proxy.dns_fallback")

PUBLIC_DNS_SERVERS = ["8.8.8.8", "1.1.1.1", "8.8.4.4", "1.0.0.1"]

# Cache: hostname -> (list of ip strings, expire_timestamp)
_DNS_CACHE: dict[str, tuple[list[str], float]] = {}
_CACHE_LOCK = threading.Lock()
_DEFAULT_TTL = 300.0  # 5 minutes

# Permanent Last-Known-Good IP store (RFC 8767 Serve-Stale pattern).
# Populated with Google Anycast seed IPs and dynamically updated on every successful resolution.
_LAST_KNOWN_IPS: dict[str, list[str]] = {
    "daily-cloudcode-pa.googleapis.com": ["172.217.115.4", "172.217.116.4", "172.217.112.4", "172.217.114.4", "172.217.118.4"],
    "cloudcode-pa.googleapis.com": ["172.217.115.4", "172.217.116.4", "172.217.112.4", "172.217.114.4", "172.217.118.4"],
    "oauth2.googleapis.com": ["142.251.127.95", "142.250.180.10"],
    "generativelanguage.googleapis.com": ["172.217.115.4", "172.217.116.4", "172.217.112.4", "172.217.114.4"],
    "accounts.google.com": ["142.250.180.13", "172.217.16.205"],
    "gemini.google.com": ["142.250.180.14", "172.217.16.206"],
    "www.googleapis.com": ["142.250.180.10", "172.217.16.202"],
}

# Negative backoff tracker: hostname -> last_failure_timestamp
_RECENT_FAILED_QUERIES: dict[str, float] = {}

# Flag indicating system resolver is dropping/failing DNS requests
_SYSTEM_DNS_BROKEN = False

# Saved original functions
_ORIG_GETADDRINFO = socket.getaddrinfo
_INSTALLED = False

CRITICAL_GOOGLE_HOSTS = [
    "oauth2.googleapis.com",
    "daily-cloudcode-pa.googleapis.com",
    "cloudcode-pa.googleapis.com",
    "generativelanguage.googleapis.com",
    "accounts.google.com",
    "gemini.google.com",
    "www.googleapis.com",
]


def _build_dns_query(hostname: str, qtype: int = 1) -> bytes:
    """Builds a standard DNS recursive query packet for an A (qtype=1) record."""
    tid = 0x51A0
    flags = 0x0100  # standard query with recursion desired
    qdcount = 1
    header = struct.pack("!HHHHHH", tid, flags, qdcount, 0, 0, 0)
    labels = hostname.strip(".").split(".")
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in labels) + b"\x00"
    return header + qname + struct.pack("!HH", qtype, 1)  # Class 1 = IN


def query_public_dns_a(hostname: str, timeout: float = 0.8) -> list[str]:
    """Queries public DNS over UDP directly for A records (fast timeout)."""
    packet = _build_dns_query(hostname, qtype=1)
    # Query primary fast servers first
    for server in PUBLIC_DNS_SERVERS[:2]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)
            s.sendto(packet, (server, 53))
            data, _ = s.recvfrom(1024)
            s.close()

            if len(data) < 12:
                continue
            r_tid, r_flags, qdcount, ancount, _, _ = struct.unpack("!HHHHHH", data[:12])
            if ancount == 0:
                continue

            offset = 12
            # Skip question section
            for _ in range(qdcount):
                while offset < len(data) and data[offset] != 0:
                    if (data[offset] & 0xC0) == 0xC0:
                        offset += 2
                        break
                    offset += 1 + data[offset]
                else:
                    offset += 1
                offset += 4  # qtype, qclass

            ips: list[str] = []
            ttl_found = _DEFAULT_TTL
            for _ in range(ancount):
                if offset >= len(data):
                    break
                if (data[offset] & 0xC0) == 0xC0:
                    offset += 2
                else:
                    while offset < len(data) and data[offset] != 0:
                        offset += 1 + data[offset]
                    offset += 1
                rtype, _, ttl, rdlen = struct.unpack("!HHIH", data[offset : offset + 10])
                offset += 10
                if rtype == 1 and rdlen == 4:
                    ip = socket.inet_ntoa(data[offset : offset + 4])
                    ips.append(ip)
                    if ttl > 0:
                        ttl_found = min(float(ttl), 600.0)
                offset += rdlen

            if ips:
                clean_host = hostname.strip().lower()
                with _CACHE_LOCK:
                    _DNS_CACHE[clean_host] = (ips, time.time() + ttl_found)
                    _LAST_KNOWN_IPS[clean_host] = ips
                    _RECENT_FAILED_QUERIES.pop(clean_host, None)
                return ips
        except Exception:
            continue

    return []


def resolve_hostname_fallback(hostname: str) -> list[str]:
    """Resolves a hostname using cache, public DNS query, or last-known-good stale IP."""
    clean_host = hostname.strip().lower()
    now = time.time()
    with _CACHE_LOCK:
        if clean_host in _DNS_CACHE:
            ips, exp = _DNS_CACHE[clean_host]
            if exp > now:
                return ips

        # Negative backoff check: if public DNS failed within last 5 seconds, use stale IP immediately
        last_failed = _RECENT_FAILED_QUERIES.get(clean_host, 0.0)
        if now - last_failed < 5.0 and clean_host in _LAST_KNOWN_IPS:
            return _LAST_KNOWN_IPS[clean_host]

    ips = query_public_dns_a(clean_host)
    if ips:
        return ips

    # Record negative failure timestamp
    with _CACHE_LOCK:
        _RECENT_FAILED_QUERIES[clean_host] = now
        if clean_host in _LAST_KNOWN_IPS:
            return _LAST_KNOWN_IPS[clean_host]

    return []


def _is_ip_or_local(host: str | bytes | None) -> bool:
    if not host:
        return True
    if isinstance(host, bytes):
        try:
            h = host.decode("ascii", errors="ignore").strip().lower()
        except Exception:
            return False
    else:
        h = str(host).strip().lower()
    if h in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "::"):
        return True
    # Fast check for IPv4
    parts = h.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return True
    # Fast check for IPv6
    if ":" in h:
        return True
    return False


def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    global _SYSTEM_DNS_BROKEN

    if _is_ip_or_local(host):
        return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)

    if isinstance(host, bytes):
        try:
            clean_host = host.decode("ascii", errors="ignore").strip().lower()
        except Exception:
            clean_host = ""
    else:
        clean_host = str(host).strip().lower()

    now = time.time()
    with _CACHE_LOCK:
        cached = _DNS_CACHE.get(clean_host)
        stale_ips = _LAST_KNOWN_IPS.get(clean_host)

    # 1. Fresh cache hit: instant return
    if cached and cached[1] > now and cached[0]:
        resolved_ip = cached[0][0]
        try:
            return _ORIG_GETADDRINFO(resolved_ip, port, family, type, proto, flags)
        except Exception:
            pass

    # 2. If system DNS was already determined broken, try fallback resolver first
    if _SYSTEM_DNS_BROKEN:
        fallback_ips = resolve_hostname_fallback(clean_host)
        if fallback_ips:
            try:
                return _ORIG_GETADDRINFO(fallback_ips[0], port, family, type, proto, flags)
            except Exception:
                pass

    # 3. Try original system getaddrinfo
    try:
        res = _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
        # Learn resolved IP to keep last-known-good cache fresh
        if res and isinstance(res, list):
            resolved_ips = []
            for item in res:
                if len(item) > 4 and isinstance(item[4], tuple) and item[4]:
                    resolved_ips.append(item[4][0])
            if resolved_ips:
                with _CACHE_LOCK:
                    _LAST_KNOWN_IPS[clean_host] = resolved_ips
                    _DNS_CACHE[clean_host] = (resolved_ips, now + _DEFAULT_TTL)
                    _RECENT_FAILED_QUERIES.pop(clean_host, None)
        return res
    except (socket.gaierror, socket.herror, TimeoutError, OSError) as exc:
        _SYSTEM_DNS_BROKEN = True
        logger.debug("System DNS resolution failed for %s (%s). Attempting resilient fallback...", host, exc)

        # 4. Immediate serve-stale fallback if known IP exists (RFC 8767)
        if stale_ips:
            try:
                return _ORIG_GETADDRINFO(stale_ips[0], port, family, type, proto, flags)
            except Exception:
                pass

        # 5. Last resort: query public DNS
        fallback_ips = resolve_hostname_fallback(clean_host)
        if fallback_ips:
            try:
                return _ORIG_GETADDRINFO(fallback_ips[0], port, family, type, proto, flags)
            except Exception:
                pass

        raise


def prewarm_critical_dns():
    """Background worker to pre-populate and periodically refresh DNS cache for critical Google endpoints."""
    def _worker():
        while True:
            for h in CRITICAL_GOOGLE_HOSTS:
                try:
                    # Try system resolver first to update cache
                    res = _ORIG_GETADDRINFO(h, 443, socket.AF_INET)
                    ips = [x[4][0] for x in res if len(x) > 4 and x[4]]
                    if ips:
                        with _CACHE_LOCK:
                            _LAST_KNOWN_IPS[h] = ips
                            _DNS_CACHE[h] = (ips, time.time() + _DEFAULT_TTL)
                except Exception:
                    # Fall back to public DNS
                    try:
                        query_public_dns_a(h)
                    except Exception:
                        pass
            time.sleep(180.0)  # Refresh every 3 minutes

    t = threading.Thread(target=_worker, daemon=True, name="dns_prewarm")
    t.start()


def install_dns_fallback():
    """Installs the fallback DNS resolver into socket.getaddrinfo and asyncio."""
    global _INSTALLED
    if _INSTALLED:
        return

    socket.getaddrinfo = patched_getaddrinfo

    try:
        from asyncio.base_events import BaseEventLoop
        import functools
        async def _patched_loop_gai(self, host, port, *args, **kwargs):
            call_fn = functools.partial(patched_getaddrinfo, host, port, *args, **kwargs)
            return await self.run_in_executor(None, call_fn)

        BaseEventLoop.getaddrinfo = _patched_loop_gai  # type: ignore[method-assign]
    except Exception as e:
        logger.debug("Could not patch BaseEventLoop.getaddrinfo: %s", e)

    _INSTALLED = True
    prewarm_critical_dns()
    logger.info("Resilient DNS fallback resolver installed.")
