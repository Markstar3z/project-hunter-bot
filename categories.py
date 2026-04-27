"""Category helpers for CoinGecko scans."""

from __future__ import annotations

from typing import Dict, List, Optional


CATEGORY_MAP: Dict[str, str] = {
    "defi": "defi",
    "nft": "non-fungible-tokens-nft",
    "gaming": "gaming",
    "layer 1": "layer-1",
    "meme": "meme-token",
    "ai": "artificial-intelligence",
    "rwa": "real-world-assets-rwa",
    "infrastructure": "infrastructure",
    "storage": "storage",
    "privacy": "privacy",
}


def list_categories() -> List[str]:
    return [
        "DeFi",
        "NFT",
        "Gaming",
        "Layer 1",
        "Meme",
        "AI",
        "RWA",
        "Infrastructure",
        "Storage",
        "Privacy",
    ]


def get_category_id(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return CATEGORY_MAP.get(name.strip().lower())


def is_valid_category(name: Optional[str]) -> bool:
    return get_category_id(name) is not None
