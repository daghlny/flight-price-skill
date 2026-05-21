"""
Ctrip XHR/Fetch probe.

Usage:
    python probes/probe_ctrip.py oneway BJS SHA 2026-06-15 --out data/probe_dom.json
    python probes/probe_ctrip.py oneway BJS TYO 2026-06-15 --out data/probe_intl.json

It opens flights.ctrip.com's search results page in a real (headed) Chromium,
listens to every request and response, then writes a JSON dump of all
*.ctrip.com XHR/Fetch traffic for offline analysis.

Headed mode is intentional — Ctrip's anti-bot stack is more lenient when
the browser looks ordinary. We also stay on the page for ~25s after load so
the calendar / low-price / list endpoints all have time to fire.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Request, Response


INTERESTING_HOSTS = ("ctrip.com", "trip.com")


def is_interesting(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(host.endswith(h) for h in INTERESTING_HOSTS)


def build_search_url(origin: str, dest: str, depdate: str) -> str:
    # Ctrip uses lowercase 3-letter city codes in URL.
    o, d = origin.lower(), dest.lower()
    return (
        f"https://flights.ctrip.com/online/list/oneway-{o}-{d}"
        f"?depdate={depdate}&cabin=y_s&adult=1&child=0&infant=0"
    )


async def run(origin: str, dest: str, depdate: str, out_path: Path, idle_seconds: int) -> None:
    target_url = build_search_url(origin, dest, depdate)
    print(f"[probe] target: {target_url}")

    records: list[dict] = []
    # Map request URL+method+timestamp to record index for response correlation.
    pending: dict[str, int] = {}

    def req_key(r: Request) -> str:
        return f"{r.method} {r.url} #{id(r)}"

    async def on_request(request: Request) -> None:
        if not is_interesting(request.url):
            return
        if request.resource_type not in ("xhr", "fetch"):
            return
        post = None
        try:
            post = request.post_data
        except Exception:
            post = None
        rec = {
            "ts": time.time(),
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "headers": dict(request.headers),
            "post_data": post,
            "response": None,
        }
        records.append(rec)
        pending[req_key(request)] = len(records) - 1

    async def on_response(response: Response) -> None:
        if not is_interesting(response.url):
            return
        req = response.request
        if req.resource_type not in ("xhr", "fetch"):
            return
        idx = pending.get(req_key(req))
        if idx is None:
            return
        try:
            body_text = await response.text()
        except Exception as e:
            body_text = f"<error reading body: {e}>"
        # Try to parse JSON; if it works, keep parsed form for readability.
        parsed = None
        if body_text and body_text[:1] in "{[":
            try:
                parsed = json.loads(body_text)
            except Exception:
                parsed = None
        records[idx]["response"] = {
            "status": response.status,
            "headers": dict(response.headers),
            "body_text": body_text if parsed is None else None,
            "body_json": parsed,
            "body_len": len(body_text) if body_text else 0,
        }

    async with async_playwright() as p:
        # Use full Chromium (not headless-shell) by setting channel=None and headless=False.
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()
        page.on("request", lambda r: asyncio.create_task(on_request(r)))
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        print("[probe] navigating...")
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"[probe] navigation error (continuing): {e}")

        print(f"[probe] idling {idle_seconds}s to capture late XHRs...")
        await asyncio.sleep(idle_seconds)

        title = await page.title()
        final_url = page.url
        print(f"[probe] final URL: {final_url}")
        print(f"[probe] title: {title}")

        await context.close()
        await browser.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "target": target_url,
        "final_url": final_url,
        "title": title,
        "origin": origin,
        "dest": dest,
        "depdate": depdate,
        "count": len(records),
        "records": records,
    }
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[probe] wrote {len(records)} ctrip XHR/Fetch records to {out_path}")

    # Quick console summary: group by url path.
    by_path: dict[str, int] = {}
    for r in records:
        path = urlparse(r["url"]).path
        by_path[path] = by_path.get(path, 0) + 1
    print("\n[probe] endpoint frequency (top 30):")
    for path, n in sorted(by_path.items(), key=lambda x: -x[1])[:30]:
        print(f"  {n:>3}  {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("trip_type", choices=["oneway"], help="trip type (only oneway for now)")
    ap.add_argument("origin", help="origin city code, e.g. BJS")
    ap.add_argument("dest", help="dest city code, e.g. SHA or TYO")
    ap.add_argument("depdate", help="departure date YYYY-MM-DD")
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--idle", type=int, default=25, help="seconds to idle after page load")
    args = ap.parse_args()

    asyncio.run(run(args.origin, args.dest, args.depdate, Path(args.out), args.idle))


if __name__ == "__main__":
    sys.exit(main())
