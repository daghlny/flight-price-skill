"""Probe tw.trip.com flight search XHR/Fetch endpoints.

Trip.com (international Ctrip) appears to return the full flight list and a
multi-date price trend without requiring login — unlike flights.ctrip.com.
This probe captures every XHR / Fetch hitting *.trip.com so we can identify
the real endpoints and pivot the CLI to them.
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


INTERESTING_HOSTS = ("trip.com", "ctrip.com")


def is_interesting(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(host.endswith(h) for h in INTERESTING_HOSTS)


async def run(target_url: str, out_path: Path, idle_seconds: int) -> None:
    print(f"[probe] target: {target_url}")
    records: list[dict] = []
    pending: dict[str, int] = {}

    def req_key(r: Request) -> str:
        return f"{r.method} {r.url} #{id(r)}"

    async def on_request(request: Request) -> None:
        if not is_interesting(request.url):
            return
        if request.resource_type not in ("xhr", "fetch"):
            return
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
            body_text = f"<error: {e}>"
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
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            locale="zh-TW",
            timezone_id="Asia/Taipei",
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
            await page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            print(f"[probe] navigation error (continuing): {e}")

        print(f"[probe] idling {idle_seconds}s...")
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
        "count": len(records),
        "records": records,
    }
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[probe] wrote {len(records)} records to {out_path}")

    by_path: dict[str, list[int]] = {}
    for r in records:
        path = urlparse(r["url"]).path
        size = (r.get("response") or {}).get("body_len") or 0
        by_path.setdefault(path, []).append(size)
    print("\n[probe] endpoints by size (sorted by max size desc):")
    rows = sorted(
        by_path.items(), key=lambda x: -max(x[1]) if x[1] else 0
    )[:40]
    for path, sizes in rows:
        n = len(sizes)
        sz = max(sizes) if sizes else 0
        print(f"  {n:>2}× max={sz:>7}B  {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="URL to probe")
    ap.add_argument("--out", required=True)
    ap.add_argument("--idle", type=int, default=30)
    args = ap.parse_args()
    asyncio.run(run(args.url, Path(args.out), args.idle))


if __name__ == "__main__":
    sys.exit(main())
