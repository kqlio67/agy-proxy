"""
Built-in Web Search Engine for Antigravity Proxy.
Executes live internet searches via DuckDuckGo with multi-tier fallbacks without requiring external API keys.
"""

import asyncio
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger("agy_proxy.search")


async def search_duckduckgo(
    query: str,
    allowed_domains: Optional[List[str]] = None,
    blocked_domains: Optional[List[str]] = None,
    max_results: int = 8,
    timeout: float = 8.0,
) -> List[Dict[str, str]]:
    """
    Performs real-time web search using DuckDuckGo HTML and Lite endpoints.
    Extracts titles, URLs, and snippets.
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    # If allowed_domains are specified, append site: filter if not already present
    effective_query = clean_query
    if allowed_domains and not any(f"site:{d}" in effective_query.lower() for d in allowed_domains):
        if len(allowed_domains) == 1:
            effective_query = f"site:{allowed_domains[0]} {effective_query}"
        else:
            domain_filter = " OR ".join(f"site:{d}" for d in allowed_domains[:3])
            effective_query = f"({domain_filter}) {effective_query}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,uk;q=0.8",
    }

    results: List[Dict[str, str]] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        # Tier 1: html.duckduckgo.com POST
        try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(effective_query)}"
            resp = await client.post(url, headers=headers)
            if resp.status_code == 200:
                html = resp.text
                results = _parse_duckduckgo_html(html, allowed_domains, blocked_domains, max_results)
        except Exception as e:
            logger.debug("DuckDuckGo HTML POST failed: %s", e)

        # Tier 2: html.duckduckgo.com GET fallback
        if not results:
            try:
                url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(effective_query)}"
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    html = resp.text
                    results = _parse_duckduckgo_html(html, allowed_domains, blocked_domains, max_results)
            except Exception as e:
                logger.debug("DuckDuckGo HTML GET failed: %s", e)

        # Tier 3: lite.duckduckgo.com fallback
        if not results:
            try:
                url = "https://lite.duckduckgo.com/lite/"
                resp = await client.post(url, data={"q": effective_query}, headers=headers)
                if resp.status_code == 200:
                    html = resp.text
                    results = _parse_duckduckgo_lite(html, allowed_domains, blocked_domains, max_results)
            except Exception as e:
                logger.debug("DuckDuckGo Lite failed: %s", e)

    logger.info("🔍 WebSearch executed: '%s' -> %d result(s)", clean_query, len(results))
    return results


def _parse_duckduckgo_html(html: str, allowed_domains: Optional[List[str]], blocked_domains: Optional[List[str]], max_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    link_matches = re.findall(r'<a[^>]+class="result__a"[^>]+href="(.*?)"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippet_matches = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)

    for i in range(len(link_matches)):
        if len(results) >= max_results:
            break

        raw_url, raw_title = link_matches[i]
        snippet = snippet_matches[i] if i < len(snippet_matches) else ""

        clean_title = re.sub(r"<[^>]+>", "", raw_title).strip()
        clean_snippet = re.sub(r"<[^>]+>", "", snippet).strip()

        actual_url = raw_url
        if "uddg=" in raw_url:
            m = re.search(r"uddg=(.*?)(&|$)", raw_url)
            if m:
                actual_url = urllib.parse.unquote(m.group(1))

        if blocked_domains and any(bd.lower() in actual_url.lower() for bd in blocked_domains):
            continue
        if allowed_domains and not any(ad.lower() in actual_url.lower() for ad in allowed_domains):
            continue

        results.append({
            "title": clean_title,
            "url": actual_url,
            "snippet": clean_snippet,
        })
    return results


def _parse_duckduckgo_lite(html: str, allowed_domains: Optional[List[str]], blocked_domains: Optional[List[str]], max_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    # Lite version uses table rows with result-link and result-snippet
    link_matches = re.findall(r'<a[^>]+class="result-link"[^>]+href="(.*?)"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippet_matches = re.findall(r'<td[^>]+class="result-snippet"[^>]*>(.*?)</td>', html, re.DOTALL)

    for i in range(len(link_matches)):
        if len(results) >= max_results:
            break

        raw_url, raw_title = link_matches[i]
        snippet = snippet_matches[i] if i < len(snippet_matches) else ""

        clean_title = re.sub(r"<[^>]+>", "", raw_title).strip()
        clean_snippet = re.sub(r"<[^>]+>", "", snippet).strip()

        actual_url = raw_url
        if "uddg=" in raw_url:
            m = re.search(r"uddg=(.*?)(&|$)", raw_url)
            if m:
                actual_url = urllib.parse.unquote(m.group(1))

        if blocked_domains and any(bd.lower() in actual_url.lower() for bd in blocked_domains):
            continue
        if allowed_domains and not any(ad.lower() in actual_url.lower() for ad in allowed_domains):
            continue

        results.append({
            "title": clean_title,
            "url": actual_url,
            "snippet": clean_snippet,
        })
    return results
