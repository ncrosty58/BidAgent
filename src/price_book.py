"""Price book — fetches live pricing from Twenty CRM with YAML bracket pricing as source of truth."""

import logging
import re

import httpx

from src.config import settings

logger = logging.getLogger("bidagent.price")

TWENTY_BASE_URL = settings.twenty_base_url
TWENTY_TOKEN = settings.twenty_token


def _parse_base_price(base_price_str: str) -> float | None:
    if not base_price_str:
        return None
    match = re.search(r'\d+', base_price_str.replace(',', ''))
    if match:
        return float(match.group(0))
    return None


async def load_or_fetch_price_book(skill_def: dict) -> list[dict]:
    yaml_services = skill_def.get("services", {})
    book = None

    if TWENTY_BASE_URL and TWENTY_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{TWENTY_BASE_URL}/rest/services",
                    headers={"Authorization": f"Bearer {TWENTY_TOKEN}", "User-Agent": "bidagent/1.0"},
                    params={"limit": 100},
                )
                resp.raise_for_status()
                data = resp.json()
            crm_services = data.get("data", {}).get("services", [])
            if crm_services:
                book = _merge_crm_with_yaml(crm_services, yaml_services)
                logger.info("Price book: %d services from Twenty CRM", len(book))
            else:
                logger.warning("Twenty CRM returned no services — using YAML")
        except Exception as e:
            logger.warning("Twenty CRM pricebook fetch failed (%s) — using YAML", e)

    if book is None:
        book = _yaml_to_book(yaml_services)

    return book


def _merge_crm_with_yaml(crm_services: list[dict], yaml_services: dict) -> list[dict]:
    """Merge CRM service records with YAML pricing brackets/flat rates."""
    book = []
    for svc in crm_services:
        key = svc.get("bidagentServiceKey") or ""
        yml = yaml_services.get(key, {})
        entry = {
            "name": key or svc.get("name", "unknown"),
            "display": svc.get("name") or yml.get("display", key),
            "description": svc.get("description", ""),
            "basePrice": svc.get("basePrice", ""),
            "category": svc.get("category", yml.get("category", "")),
        }
        if "flat_rate" in yml:
            entry["flat_rate"] = yml["flat_rate"]
        elif "brackets" in yml:
            entry["brackets"] = yml["brackets"]
        else:
            parsed = _parse_base_price(svc.get("basePrice", ""))
            if parsed is not None:
                entry["flat_rate"] = {"low": parsed, "high": parsed}
        book.append(entry)
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
