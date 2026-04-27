"""CoinGecko scanner and filtering logic."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import requests

from categories import get_category_id
from storage import Storage


LOGGER = logging.getLogger(__name__)
COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
MIN_MARKET_CAP = 10_000
MAX_MARKET_CAP = 1_000_000_000
MAX_PAGES_PER_SCAN = 10
PAGE_SIZE = 250


class ScannerError(Exception):
    """Raised when a scan cannot complete."""


@dataclass
class ScanParams:
    target_count: int
    scan_type: str
    category_name: Optional[str] = None
    sort_mode: str = "market_cap_desc"


class CoinGeckoScanner:
    def __init__(self, storage: Storage, api_key: Optional[str] = None, session: Optional[requests.Session] = None) -> None:
        self.storage = storage
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if api_key:
            self.session.headers.update({"x-cg-demo-api-key": api_key})

    def scan(
        self,
        params: ScanParams,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        category_id = get_category_id(params.category_name) if params.scan_type == "specific" else None
        resume = self.storage.resume_state()
        start_page = min(max(resume["last_page_scanned"], 1), MAX_PAGES_PER_SCAN)
        start_index = max(resume["last_coin_index"], 0)

        results: List[Dict[str, Any]] = []
        scanned_count = 0
        no_links_count = 0
        mcap_filtered_count = 0
        duplicate_count = 0
        last_page_scanned = start_page
        last_coin_index = 0

        for page in range(start_page, MAX_PAGES_PER_SCAN + 1):
            if progress_callback and (page == start_page or (page - start_page) % 2 == 0):
                progress_callback(f"Scanning page {page} of {MAX_PAGES_PER_SCAN}...")

            market_page = self._fetch_market_page(page, params.sort_mode, category_id)
            if not market_page:
                last_page_scanned = page
                last_coin_index = 0
                break

            page_start_index = start_index if page == start_page else 0
            for index, coin in enumerate(market_page[page_start_index:], start=page_start_index):
                scanned_count += 1
                last_page_scanned = page
                last_coin_index = index + 1

                market_cap = coin.get("market_cap") or 0
                if market_cap < MIN_MARKET_CAP or market_cap > MAX_MARKET_CAP:
                    mcap_filtered_count += 1
                    continue

                try:
                    details = self._fetch_coin_details(coin["id"])
                except ScannerError:
                    LOGGER.exception("Skipping coin after repeated fetch failure: %s", coin.get("id"))
                    continue

                social = details.get("links", {})
                twitter_handle = (social.get("twitter_screen_name") or "").strip()
                telegram_url = self._extract_telegram_url(social.get("telegram_channel_identifier"))
                if not twitter_handle or not telegram_url:
                    no_links_count += 1
                    continue

                if self.storage.is_duplicate(coin["id"], twitter_handle):
                    duplicate_count += 1
                    continue

                results.append(
                    {
                        "coin_id": coin["id"],
                        "name": coin.get("name", ""),
                        "symbol": str(coin.get("symbol", "")).upper(),
                        "twitter_handle": twitter_handle,
                        "telegram_handle": telegram_url.rsplit("/", 1)[-1],
                        "twitter_url": f"https://twitter.com/{twitter_handle}",
                        "telegram_url": telegram_url,
                        "market_cap": market_cap,
                        "category": params.category_name or "All",
                    }
                )

                if len(results) >= params.target_count:
                    break

            start_index = 0
            if len(results) >= params.target_count:
                break
            time.sleep(0.5)

        save_result = self.storage.add_projects(results, last_page_scanned, last_coin_index)
        return {
            "projects": results,
            "scanned_count": scanned_count,
            "no_links_count": no_links_count,
            "mcap_filtered_count": mcap_filtered_count,
            "duplicate_count": duplicate_count,
            "new_count": save_result["added_count"],
            "total_db_count": save_result["total_projects"],
            "last_page_scanned": last_page_scanned,
            "last_coin_index": last_coin_index,
        }

    def _fetch_market_page(self, page: int, sort_mode: str, category_id: Optional[str]) -> List[Dict[str, Any]]:
        params = {
            "vs_currency": "usd",
            "order": sort_mode,
            "per_page": PAGE_SIZE,
            "page": page,
        }
        if category_id:
            params["category"] = category_id
        return self._request_json("/coins/markets", params)

    def _fetch_coin_details(self, coin_id: str) -> Dict[str, Any]:
        data = self._request_json(
            f"/coins/{coin_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            timeout=20,
            retry_pause=5,
        )
        time.sleep(1.2)
        return data

    def _request_json(
        self,
        path: str,
        params: Dict[str, Any],
        timeout: int = 15,
        retry_pause: int = 60,
    ) -> Any:
        url = f"{COINGECKO_API_BASE}{path}"
        for attempt in range(2):
            try:
                response = self.session.get(url, params=params, timeout=timeout)
                if response.status_code == 429:
                    if attempt == 0:
                        time.sleep(60)
                        continue
                    raise ScannerError("CoinGecko rate limit reached after retry.")
                response.raise_for_status()
                return response.json()
            except requests.Timeout as exc:
                if attempt == 0:
                    time.sleep(retry_pause)
                    continue
                raise ScannerError(f"Timeout calling CoinGecko: {path}") from exc
            except requests.RequestException as exc:
                if attempt == 0:
                    time.sleep(retry_pause)
                    continue
                raise ScannerError(f"Network error calling CoinGecko: {path}") from exc
        raise ScannerError(f"Unexpected request failure: {path}")

    @staticmethod
    def _extract_telegram_url(identifier: Any) -> Optional[str]:
        if not identifier:
            return None
        if isinstance(identifier, list):
            for item in identifier:
                url = CoinGeckoScanner._extract_telegram_url(item)
                if url:
                    return url
            return None
        value = str(identifier).strip()
        if not value:
            return None
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"https://t.me/{value.lstrip('@')}"
