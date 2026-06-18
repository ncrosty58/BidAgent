"""
Price book — fetches live pricing from Twenty CRM, matches to YAML bracket/flat-rate data.

BidAgent combines CRM service records (names, descriptions, basePrice hints)
with the structural pricing (brackets, flat_rates) defined in the YAML skill file.
CRM is the canonical list of services; YAML provides the pricing structure.
"""

import logging
import re
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger("bidagent.price")


def _parse_base_price(base_price_str: str) -> float | None:
    if not base_price_str:
        return None
    # Find all digits in the string
    match = re.search(r'\d+', base_price_str.replace(',', ''))
    if match:
        return float(match.group(0))
    return None


async def load_or_fetch_price_book(skill_def: dict) -> list[dict]:
    """
    Returns a combined price book. CRM is tried first for names/descriptions,
    then enriched with YAML bracket/flat_rate pricing. Falls back to YAML-only
    if CRM is unavailable or returns empty.
    """
    yaml_services = skill_def.get("services", {})
    if not settings.twenty_crm_api_url or not settings.twenty_crm_bearer_token:
        logger.info("CRM not configured — using YAML-only price book")
        return _yaml_to_book(yaml_services)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                settings.twenty_crm_api_url.rstrip("/") + "/rest/services",
                headers={"Authorization": f"Bearer {settings.twenty_crm_bearer_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

        crm_services = data.get("data", {}).get("services", [])
        if not crm_services:
            logger.warning("CRM returned empty—using YAML price book")
            return _yaml_to_book(yaml_services)

    except Exception as e:
        logger.warning("CRM fetch failed (%s) — using YAML price book", e)
        return _yaml_to_book(yaml_services)

    # Merge CRM + YAML: match by service name
    book = []
    for svc in crm_services:
        name = svc.get("name", "unknown")
        yml = yaml_services.get(name, {})
        entry = {
            "name": name,
            "display": yml.get("display", name),
            "category": yml.get("category", svc.get("category", "")),
            "description": svc.get("description", ""),
            "basePrice": svc.get("basePrice", ""),
        }
        if "flat_rate" in yml:
            entry["flat_rate"] = yml["flat_rate"]
        elif "brackets" in yml:
            entry["brackets"] = yml["brackets"]
        else:
            # Fallback to CRM basePrice parsed as flat rate
            parsed = _parse_base_price(svc.get("basePrice", ""))
            if parsed is not None:
                entry["flat_rate"] = {"low": parsed, "high": parsed}

        book.append(entry)

    logger.info("Price book: %d services (CRM base + YAML brackets)", len(book))
    return book


def _yaml_to_book(yaml_services: dict) -> list[dict]:
    """Pure YAML price book (no CRM connection)."""
    book = []
    for name, svc in yaml_services.items():
        entry = {"name": name, "display": svc.get("display", name)}
        if "flat_rate" in svc:
            entry["flat_rate"] = svc["flat_rate"]
        elif "brackets" in svc:
            entry["brackets"] = svc["brackets"]
        book.append(entry)
    return book

