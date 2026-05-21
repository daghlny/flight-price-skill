"""Capture one RT FlightListSearch payload and dump the journey structure
so we can see whether return-leg detail is actually in there.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flight_price.trip import (
    _bootstrap_url,
    _parse_response_body,
    FLS_PATH,
    UA,
)
from playwright.async_api import async_playwright


async def main() -> None:
    origin, dest = "BJS", "TYO"
    ddate, rdate = "2026-06-06", "2026-06-08"
    url = _bootstrap_url(origin, dest, ddate, rdate)
    print(f"URL: {url}")

    captured: dict[str, str] = {}
    done = asyncio.Event()

    async def on_response(response):
        if FLS_PATH not in response.url:
            return
        try:
            body = await response.text()
        except Exception:
            return
        if not body:
            return
        kind = "sse" if "SSE" in response.url else "json"
        captured[kind] = body
        done.set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
        )
        page = await ctx.new_page()
        page.on("response", lambda r: asyncio.create_task(on_response(r)))
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"goto error: {e}")
        try:
            await asyncio.wait_for(done.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
        if captured:
            await asyncio.sleep(2.0)
        await ctx.close()
        await browser.close()

    body = captured.get("sse") or captured.get("json")
    if not body:
        print("NO RESPONSE CAPTURED")
        return

    Path("data").mkdir(exist_ok=True)
    Path("data/rt_raw.txt").write_text(body)
    print(f"raw body len: {len(body)}")

    payload = _parse_response_body(body)
    if not payload:
        print("PAYLOAD UNPARSEABLE")
        return

    Path("data/rt_parsed.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)
    )

    itl = payload.get("itineraryList") or []
    print(f"itineraryList count: {len(itl)}")
    if not itl:
        return

    # Look at first 3 itineraries' shape
    for idx, it in enumerate(itl[:3]):
        print(f"\n--- itinerary #{idx} ---")
        print(f"keys: {sorted(it.keys())}")
        journeys = it.get("journeyList") or []
        print(f"  journeyList count: {len(journeys)}")
        for ji, j in enumerate(journeys):
            sections = j.get("transSectionList") or []
            print(f"    journey #{ji}: {len(sections)} sections, keys={sorted(j.keys())}")
            for si, s in enumerate(sections):
                fi = s.get("flightInfo") or {}
                dep = (s.get("departPoint") or {}).get("airportCode")
                arr = (s.get("arrivePoint") or {}).get("airportCode")
                dt_ = s.get("departDateTime")
                at_ = s.get("arriveDateTime")
                print(
                    f"      seg #{si}: {fi.get('flightNo')} "
                    f"{dep} {dt_} -> {arr} {at_}"
                )
        policies = it.get("policies") or []
        if policies:
            price = policies[0].get("price", {}).get("adult", {}).get("totalPrice")
            print(f"  price: {price}")


if __name__ == "__main__":
    asyncio.run(main())
