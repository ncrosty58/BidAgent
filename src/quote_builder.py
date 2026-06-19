"""
Quote builder — takes requested services, price book, and vision analysis
from the LLM, then constructs an itemized quote with job descriptions.
All prompt text comes from the skill YAML file, not hardcoded Python.
"""

import base64
import json
import logging

from openai import OpenAI

from src.config import settings

logger = logging.getLogger("bidagent.quote")


async def build_quote(
    services_list: list[str],
    price_book: list[dict],
    image_buffers: list[dict],
    skill_def: dict,
) -> dict:
    if not settings.openai_api_key:
        return _flat_quote_fallback(services_list, price_book, skill_def, error_message="No OpenAI API key configured.")

    pricing_json = json.dumps(price_book, indent=2, default=str)
    services_json = json.dumps(services_list)

    prompts = skill_def.get("prompts", {})
    system_prompt = prompts.get("system", "")
    quote_prompt = prompts.get("quote", "")

    full_prompt = f"""{quote_prompt}

Requested services:
{services_json}

Available pricing data:
{pricing_json}

Respond with ONLY a JSON object as specified above."""

    content_parts = [
        {"type": "text", "text": "Analyze these property photos and produce an estimate."},
    ]
    for buf in image_buffers:
        b64 = base64.b64encode(buf["data"]).decode("utf-8")
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "auto"},
        })

    raw = ""
    try:
        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        response = client.chat.completions.create(
            model=settings.llm_model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts},
                {"role": "user", "content": [{"type": "text", "text": full_prompt}]},
            ],
            response_format={"type": "json_object"},
            max_tokens=8192,
            temperature=0.2,
        )
        raw = response.choices[0].message.content or ""
        
        # Clean JSON structure
        clean = raw.strip()
        
        # Strip markdown code fences if present
        if clean.startswith("```"):
            first_newline = clean.find("\n")
            if first_newline != -1:
                clean = clean[first_newline:].strip()
            if clean.endswith("```"):
                clean = clean[:-3].strip()
        
        # Extract only the JSON part between first { and last }
        start = clean.find('{')
        end = clean.rfind('}')
        if start >= 0 and end > start:
            clean = clean[start:end+1]
            
        # Sanitize raw newlines/tabs inside string literals
        chars = []
        in_string = False
        escape = False
        for c in clean:
            if c == '"' and not escape:
                in_string = not in_string
                chars.append(c)
            elif c == '\\' and in_string and not escape:
                escape = True
                chars.append(c)
            elif in_string:
                if escape:
                    escape = False
                if c == '\n':
                    chars.append('\\n')
                elif c == '\r':
                    chars.append('\\r')
                elif c == '\t':
                    chars.append('\\t')
                else:
                    chars.append(c)
            else:
                chars.append(c)
        clean = "".join(chars)
        
        try:
            result = json.loads(clean)
        except json.JSONDecodeError as e:
            import re
            # Try fixing missing commas between fields
            fixed = re.sub(r'(true|false|\d+|\]|")\s+\n?\s+"', r'\1,\n      "', clean)
            try:
                result = json.loads(fixed)
            except:
                raise e
        
        # Ensure single price fields are present and ranges match the single price
        if "itemized_quote" in result:
            # 1. Correct any existing items with price <= 0 or missing
            for item in result["itemized_quote"]:
                svc_name = item.get("service")
                pricing = next((p for p in price_book if p["name"] == svc_name or p["display"] == svc_name), None)
                
                min_price = 150.0
                if pricing:
                    if "flat_rate" in pricing:
                        min_price = float(pricing["flat_rate"]["low"])
                    elif "brackets" in pricing and pricing["brackets"]:
                        min_price = float(pricing["brackets"][0]["low"])
                
                price_val = item.get("price")
                if price_val is None or price_val <= 0:
                    item["price"] = min_price
                    item["price_low"] = min_price
                    item["price_high"] = min_price
                    item["description"] = "Requested service quoted at starting rate."
                else:
                    if "price" in item:
                        item["price_low"] = item["price"]
                        item["price_high"] = item["price"]
                    elif "price_low" in item:
                        item["price"] = item["price_low"]
                        item["price_high"] = item["price_low"]
            
            # 2. Add any requested services that the LLM completely omitted
            existing_services = {item.get("service").lower() for item in result["itemized_quote"] if item.get("service")}
            for requested in services_list:
                pricing = next((p for p in price_book if p["name"].lower() == requested.lower() or p["display"].lower() == requested.lower()), None)
                if pricing:
                    key = pricing["name"]
                    if key.lower() not in existing_services:
                        min_price = 150.0
                        if "flat_rate" in pricing:
                            min_price = float(pricing["flat_rate"]["low"])
                        elif "brackets" in pricing and pricing["brackets"]:
                            min_price = float(pricing["brackets"][0]["low"])
                        
                        result["itemized_quote"].append({
                            "service": key,
                            "label": pricing.get("display", key),
                            "bracket": "standard",
                            "price": min_price,
                            "price_low": min_price,
                            "price_high": min_price,
                            "description": "Requested service quoted at starting rate."
                        })
            
            # 3. Recalculate totals
            total_val = sum(item.get("price", 0.0) for item in result["itemized_quote"] if "error" not in item)
            total_low_val = sum(item.get("price_low", 0.0) for item in result["itemized_quote"] if "error" not in item)
            total_high_val = sum(item.get("price_high", 0.0) for item in result["itemized_quote"] if "error" not in item)
            result["total"] = total_val
            result["total_low"] = total_low_val
            result["total_high"] = total_high_val

        return result
    except Exception as e:
        logger.error("LLM quote generation failed: %s | Raw response: %r", e, raw)
        return _flat_quote_fallback(services_list, price_book, skill_def, error_message=str(e))


def _flat_quote_fallback(services_list: list[str], price_book: list[dict], skill_def: dict, error_message: str = None) -> dict:
    items = []
    total = 0

    for svc_name in services_list:
        pricing = next((p for p in price_book if p["name"] == svc_name), None)
        if not pricing:
            items.append({"service": svc_name, "error": "No pricing data found"})
            continue

        if "flat_rate" in pricing:
            fr = pricing["flat_rate"]
            mid_val = float(fr["low"])
            items.append({
                "service": svc_name,
                "label": pricing.get("display", svc_name),
                "flat_rate": True,
                "price": mid_val,
                "price_low": mid_val,
                "price_high": mid_val,
                "description": f'Standard flat-rate pricing for {pricing.get("display", svc_name)}.'
            })
            total += mid_val
        elif "brackets" in pricing:
            brackets = pricing["brackets"]
            mid = brackets[len(brackets) // 2]
            mid_val = float(mid["low"])
            items.append({
                "service": svc_name,
                "label": pricing.get("display", svc_name),
                "bracket": mid.get("name", "standard"),
                "price": mid_val,
                "price_low": mid_val,
                "price_high": mid_val,
                "description": f'Classified at {mid.get("label", "standard")} bracket.'
            })
            total += mid_val
        else:
            items.append({"service": svc_name, "error": "No bracket or flat rate data"})

    # Build overall description for CRM note
    svc_names = ", ".join(i.get("label", i.get("service", "")) for i in items if "error" not in i)
    customer_description = f"Auto-estimate for {len(items)} service(s): {svc_names}. Estimated total ${total}."
    contractor_notes = f"Fallback estimate (AI unavailable). Services: {svc_names}. Total ${total}."

    warning_msg = "AI analysis unavailable -- used default brackets."
    if error_message:
        warning_msg += f" (Error: {error_message})"

    return {
        "itemized_quote": items,
        "description": customer_description,
        "contractor_notes": contractor_notes,
        "total": total,
        "total_low": total,
        "total_high": total,
        "warnings": [warning_msg],
        "rejection": None,
    }
