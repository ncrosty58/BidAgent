"""
Quote builder — takes requested services, price book, and vision analysis
from the LLM, then constructs an itemized quote with job descriptions.
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
        return _flat_quote_fallback(services_list, price_book, error_message="No OpenAI API key configured.")

    pricing_json = json.dumps(price_book, indent=2, default=str)
    services_json = json.dumps(services_list)
    system_prompt = _quote_prompt(services_json, pricing_json, skill_def)

    content_parts = [
        {"type": "text", "text": "Analyze these property photos and produce an estimate."},
    ]
    for buf in image_buffers:
        b64 = base64.b64encode(buf["data"]).decode("utf-8")
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "auto"},
        })

    try:
        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        response = client.chat.completions.create(
            model=settings.llm_model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts},
            ],
            max_tokens=2000,
            temperature=0.2,
        )
        raw = response.choices[0].message.content or ""
        clean = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        
        # Ensure single price fields are present and ranges match the single price
        if "itemized_quote" in result:
            for item in result["itemized_quote"]:
                if "price" in item:
                    item["price_low"] = item["price"]
                    item["price_high"] = item["price"]
                elif "price_low" in item:
                    # Fallback if LLM missed 'price' but returned 'price_low'
                    item["price"] = item["price_low"]
                    item["price_high"] = item["price_low"]
            
        if "total" in result:
            result["total_low"] = result["total"]
            result["total_high"] = result["total"]
        elif "total_low" in result:
            result["total"] = result["total_low"]
            result["total_high"] = result["total_low"]

        return result
    except Exception as e:
        logger.error("LLM quote generation failed: %s", e)
        return _flat_quote_fallback(services_list, price_book, error_message=str(e))


def _quote_prompt(services_json: str, pricing_json: str, skill_def: dict) -> str:
    prompts = skill_def.get("prompts", {})
    custom_system = prompts.get("system", "")
    return f"""
{custom_system}

Requested services:
{services_json}

Available pricing data:
{pricing_json}

For each requested service:
1. Analyze the property photos to assess the amount of work required (e.g. estimate size/condition of driveway, height/stories of house, size of landscape beds, extent of wood rot/railing damage).
2. Using the pricing guidelines in the price book (brackets and flat rates) as boundaries and base rates, calculate a specific, single estimated price (not a range) for the work. Be smart: do not just regurgitate the bracket boundaries; adjust the price dynamically within or slightly around the boundaries based on the visual complexity, size, and level of effort observed in the photos.
3. Determine the bracket name that closest matches the workload.
4. Generate a concise (1-2 sentence) job description explaining what needs to be done and how the specific price was calculated from the visual evidence in the photos.

Respond with ONLY a JSON object:
{{
  "itemized_quote": [
    {{
      "service": "concrete_clean", 
      "bracket": "medium_4_car", 
      "label": "Driveway & Concrete Deep Clean", 
      "description": "4-car driveway shows moderate dirt and oil staining near the garage; estimated at $310 based on standard deep clean effort.", 
      "price": 310,
      "price_low": 310,
      "price_high": 310
    }}
  ],
  "description": "Concise 2-3 sentence overall job summary based on the photos, detailing the calculated cost.",
  "total": 1250,
  "total_low": 1250,
  "total_high": 1250,
  "warnings": [],
  "rejection": null
}}

If you cannot estimate any of the requested services, return rejection with an explanation.
"""


def _flat_quote_fallback(services_list: list[str], price_book: list[dict], error_message: str = None) -> dict:
    items = []
    total = 0

    for svc_name in services_list:
        pricing = next((p for p in price_book if p["name"] == svc_name), None)
        if not pricing:
            items.append({"service": svc_name, "error": "No pricing data found"})
            continue

        if "flat_rate" in pricing:
            fr = pricing["flat_rate"]
            mid_val = float((fr["low"] + fr["high"]) // 2)
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
            mid_val = float((mid["low"] + mid["high"]) // 2)
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
    description = f"Auto-estimate for {len(items)} service(s): {svc_names}. Estimated total ${total}."

    warning_msg = "AI analysis unavailable -- used default brackets."
    if error_message:
        warning_msg += f" (Error: {error_message})"

    return {
        "itemized_quote": items,
        "description": description,
        "total": total,
        "total_low": total,
        "total_high": total,
        "warnings": [warning_msg],
        "rejection": None,
    }

