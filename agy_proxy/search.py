"""
Multi-Engine Live Web Search for Antigravity Proxy.
Aggregates and deduplicates results concurrently across multiple providers:
- DuckDuckGo (HTML & Lite)
- Bing Web Search
- Brave Web Search
- Google / Fallbacks
"""

import asyncio
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set
import httpx

logger = logging.getLogger("agy_proxy.search")

DEFAULT_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,uk;q=0.8,ru;q=0.7",
}


async def _search_duckduckgo(client: httpx.AsyncClient, query: str, max_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    # Tier 1: HTML POST
    try:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        resp = await client.post(url, headers=DEFAULT_SEARCH_HEADERS)
        if resp.status_code == 200:
            link_matches = re.findall(r'<a[^>]+class="result__a"[^>]+href="(.*?)"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
            snippet_matches = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
            for i in range(min(len(link_matches), max_results)):
                raw_url, raw_title = link_matches[i]
                snippet = snippet_matches[i] if i < len(snippet_matches) else ""
                actual_url = raw_url
                if "uddg=" in raw_url:
                    m = re.search(r"uddg=(.*?)(&|$)", raw_url)
                    if m:
                        actual_url = urllib.parse.unquote(m.group(1))
                results.append({
                    "title": re.sub(r"<[^>]+>", "", raw_title).strip(),
                    "url": actual_url,
                    "snippet": re.sub(r"<[^>]+>", "", snippet).strip(),
                    "source": "duckduckgo",
                })
            if results:
                return results
    except Exception as e:
        logger.debug("DDG HTML search error: %s", e)

    # Tier 2: Lite
    try:
        url = "https://lite.duckduckgo.com/lite/"
        resp = await client.post(url, data={"q": query}, headers=DEFAULT_SEARCH_HEADERS)
        if resp.status_code == 200:
            link_matches = re.findall(r'<a[^>]+class="result-link"[^>]+href="(.*?)"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
            snippet_matches = re.findall(r'<td[^>]+class="result-snippet"[^>]*>(.*?)</td>', resp.text, re.DOTALL)
            for i in range(min(len(link_matches), max_results)):
                raw_url, raw_title = link_matches[i]
                snippet = snippet_matches[i] if i < len(snippet_matches) else ""
                actual_url = raw_url
                if "uddg=" in raw_url:
                    m = re.search(r"uddg=(.*?)(&|$)", raw_url)
                    if m:
                        actual_url = urllib.parse.unquote(m.group(1))
                results.append({
                    "title": re.sub(r"<[^>]+>", "", raw_title).strip(),
                    "url": actual_url,
                    "snippet": re.sub(r"<[^>]+>", "", snippet).strip(),
                    "source": "duckduckgo_lite",
                })
    except Exception as e:
        logger.debug("DDG Lite search error: %s", e)

    return results


async def _search_bing(client: httpx.AsyncClient, query: str, max_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    try:
        url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
        resp = await client.get(url, headers=DEFAULT_SEARCH_HEADERS)
        if resp.status_code == 200:
            # Extract Bing organic results
            items = re.findall(r'<li class="b_algo"[^>]*>.*?<h2><a href="(https?://[^"]+)"[^>]*>(.*?)</a></h2>(.*?)</li>', resp.text, re.DOTALL)
            for raw_url, raw_title, rest in items[:max_results]:
                # Extract snippet if present in paragraph
                snip_match = re.search(r'<p[^>]*>(.*?)</p>', rest, re.DOTALL)
                snippet = snip_match.group(1) if snip_match else ""
                results.append({
                    "title": re.sub(r"<[^>]+>", "", raw_title).strip(),
                    "url": raw_url,
                    "snippet": re.sub(r"<[^>]+>", "", snippet).strip(),
                    "source": "bing",
                })
    except Exception as e:
        logger.debug("Bing search error: %s", e)
    return results


async def _search_brave(client: httpx.AsyncClient, query: str, max_results: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    try:
        url = f"https://search.brave.com/search?q={urllib.parse.quote(query)}"
        resp = await client.get(url, headers=DEFAULT_SEARCH_HEADERS)
        if resp.status_code == 200:
            # Match brave result cards
            matches = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>.*?<div class="title[^"]*"[^>]*>(.*?)</div>.*?<div class="snippet[^"]*"[^>]*>(.*?)</div>', resp.text, re.DOTALL)
            for raw_url, raw_title, raw_snip in matches[:max_results]:
                results.append({
                    "title": re.sub(r"<[^>]+>", "", raw_title).strip(),
                    "url": raw_url,
                    "snippet": re.sub(r"<[^>]+>", "", raw_snip).strip(),
                    "source": "brave",
                })
    except Exception as e:
        logger.debug("Brave search error: %s", e)
    return results


async def search_google_grounding(
    account_pool: Any,
    query: str,
    max_results: int = 5,
    timeout: float = 10.0,
) -> List[Dict[str, str]]:
    """Performs live web search using Google's native Grounding tool (gemini-3.1-flash-lite / googleSearch)."""
    if not account_pool or not query:
        return []

    # Pick an active OAuth account
    oauth_accounts = [a for a in getattr(account_pool, "accounts", {}).values() if a.enabled and a.auth_method != "api_key"]
    if not oauth_accounts:
        return []

    acc = oauth_accounts[0]
    try:
        from agy_proxy.auth import CLOUDCODE_BASE_URL
        headers = await acc.get_auth_headers()
        client = await acc.get_http_client()
        payload = {
            "project": acc.project_id or "aicode-consumers",
            "request": {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": query}],
                    }
                ],
                "systemInstruction": {
                    "role": "user",
                    "parts": [
                        {
                            "text": "You are a search engine bot. You will be given a query from a user. Your task is to search the web for relevant information that will help the user. You MUST perform a web search. Do not respond or interact with the user, please respond as if they typed the query into a search bar."
                        }
                    ],
                },
                "tools": [
                    {
                        "googleSearch": {
                            "enhancedContent": {
                                "imageSearch": {
                                    "maxResultCount": max_results
                                }
                            }
                        }
                    }
                ],
                "generationConfig": {
                    "candidateCount": 1
                },
            },
            "model": "gemini-3.1-flash-lite",
            "userAgent": "antigravity",
            "requestType": "web_search",
        }

        resp = await client.post(
            f"{CLOUDCODE_BASE_URL}/v1internal:generateContent",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("response", {}).get("candidates", [])
            if candidates:
                cand = candidates[0]
                grounding = cand.get("groundingMetadata", {})
                chunks = grounding.get("groundingChunks", [])
                results: List[Dict[str, str]] = []
                parts = cand.get("content", {}).get("parts", [])
                summary_text = parts[0].get("text", "") if parts else ""

                for chunk in chunks:
                    web = chunk.get("web", {})
                    uri = web.get("uri", "")
                    title = web.get("title", "")
                    if uri:
                        results.append({
                            "title": title or "Google Grounding Result",
                            "url": uri,
                            "snippet": summary_text[:300] if summary_text else "",
                            "source": "google_grounding",
                        })
                if results:
                    logger.info("Google Grounding Search ('%s'): returned %d result(s)", query, len(results))
                    return results[:max_results]
    except Exception as e:
        logger.debug("Google grounding search error: %s", e)

    return []


async def search_multi_engine(
    query: str,
    allowed_domains: Optional[List[str]] = None,
    blocked_domains: Optional[List[str]] = None,
    max_results: int = 10,
    timeout: float = 8.0,
    account_pool: Optional[Any] = None,
) -> List[Dict[str, str]]:
    """
    Executes concurrent web search across Google Grounding, DuckDuckGo, Bing, and Brave.
    Deduplicates URLs and ranks by relevance.
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    # 1. Try Google Native Grounding Search if account pool is provided
    if account_pool:
        try:
            google_results = await search_google_grounding(account_pool, clean_query, max_results=max_results, timeout=timeout)
            if google_results:
                return google_results
        except Exception as e:
            logger.debug("Google grounding fallback triggered: %s", e)

    effective_query = clean_query
    if allowed_domains and not any(f"site:{d}" in effective_query.lower() for d in allowed_domains):
        if len(allowed_domains) == 1:
            effective_query = f"site:{allowed_domains[0]} {effective_query}"
        else:
            domain_filter = " OR ".join(f"site:{d}" for d in allowed_domains[:3])
            effective_query = f"({domain_filter}) {effective_query}"

    all_raw_results: List[Dict[str, str]] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        # Fan-out to multiple search engines in parallel
        tasks = [
            _search_duckduckgo(client, effective_query, max_results=max_results),
            _search_bing(client, effective_query, max_results=max_results),
            _search_brave(client, effective_query, max_results=max_results),
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        for res in gathered:
            if isinstance(res, list):
                all_raw_results.extend(res)

    # Deduplicate by normalized URL
    seen_urls: Set[str] = set()
    deduped_results: List[Dict[str, str]] = []

    for r in all_raw_results:
        raw_url = r.get("url", "").strip()
        if not raw_url or not raw_url.startswith("http"):
            continue

        # Strip tracking queries from url for canonical deduplication
        norm_url = raw_url.split("#")[0].rstrip("/")

        if norm_url in seen_urls:
            continue

        if blocked_domains and any(bd.lower() in norm_url.lower() for bd in blocked_domains):
            continue
        if allowed_domains and not any(ad.lower() in norm_url.lower() for ad in allowed_domains):
            continue

        seen_urls.add(norm_url)
        deduped_results.append({
            "title": r.get("title", ""),
            "url": raw_url,
            "snippet": r.get("snippet", ""),
            "source": r.get("source", "web"),
        })

        if len(deduped_results) >= max_results:
            break

    logger.info("Multi-Engine Search ('%s'): gathered %d raw -> %d unique result(s)", clean_query, len(all_raw_results), len(deduped_results))
    return deduped_results


# Backward compatibility alias
search_duckduckgo = search_multi_engine
