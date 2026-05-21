---
name: flight-price
description: This skill should be used when the user asks to "find cheap flights", "compare flight prices across dates", "find the cheapest weekend trip", "scan flight prices for a holiday", "比较机票价格", "查机票", "找便宜的机票", or any request that involves discovering flight prices across multiple dates or itinerary combinations. The skill drives the `flight-price` CLI, which queries Trip.com for bookable inventory (not marketing "from" prices) and is designed to be invoked by AI agents with structured JSON output.
version: 0.4.0
---

# flight-price

A CLI tool that queries **Trip.com** for real, currently-bookable flight prices across a date range or a list of explicit (outbound, return) date pairs. Designed for AI agents — supports JSON output, parallel scans, and an `--pairs` flag that batches arbitrary itinerary combinations into one invocation.

## When to invoke this skill

Use this skill whenever the user's request involves any of:
- Finding the cheapest flight on a specific date or across a date range
- Comparing prices for different stay-length / departure-date combinations (typical "should I travel Friday or Saturday?" style)
- Holiday-planning queries that require enumerating leave-day combinations ("端午 + 请1天假怎么去 X 最划算？")
- Filtering flights by airline, max stops, depart-time window
- Producing a JSON-structured list of candidate itineraries for downstream decision-making

Don't use this for: hotel pricing, ground transportation, visa info, airline schedules without prices.

## Installation check

Before invoking the CLI, verify it's installed:

```bash
which flight-price && flight-price --version
```

If missing, the user must install it. Point them at: https://github.com/daghlny/flight-price-skill (one-line installer in the README).

## Recommended invocation pattern for agents

**Always pass `--json`.** The default human-table output is harder to parse; JSON is structured and stable.

**Always read `flights[]`** (the top-level flat array) rather than walking `results[].options[]`. It's pre-ranked across all queries.

**Always check `results[i].status`** for each query — distinguishes `"ok"` / `"no_results"` / `"timeout"`. Retry only `"timeout"` ones.

### Single-date or contiguous-range scan

```bash
flight-price BJS SHA --from 2026-06-01 --to 2026-06-07 --json
flight-price BJS TYO --rt --stay 2 --from 2026-06-19 --to 2026-06-19 --json
flight-price BJS NRT --rt --return-date 2026-06-15 --from 2026-06-10 --to 2026-06-14 --json
```

### Multi-combination scan (the agent-native form)

When the user's question implies several itinerary variants — typical of holiday planning, leave-day optimization, or "compare these specific date pairs" — compute the date pairs yourself and pass them in one call:

```bash
flight-price BJS HGH --pairs \
  2026-06-18:2026-06-20,2026-06-18:2026-06-21,\
  2026-06-19:2026-06-21,2026-06-19:2026-06-22,\
  2026-06-20:2026-06-22 \
  --json --max-stops 0
```

Each pair is `OUT[:RET]` (omit `:RET` for one-way). Mutually exclusive with `--from/--to/--rt/--stay/--return-date`.

## CLI surface (cheat sheet)

```
flight-price ORIGIN DEST [date selection] [filters] [ranking] [output]
```

| Group | Flag | Notes |
|---|---|---|
| Date range | `--from YYYY-MM-DD` / `--to YYYY-MM-DD` | default: today / from + 7 |
| Round-trip | `--rt --stay N` or `--rt --return-date YYYY-MM-DD` | RT mode |
| Explicit pairs | `--pairs OUT[:RET][,OUT[:RET]...]` | mutually exclusive with above |
| Filters | `--direct`, `--max-stops N`, `--airline CA,MU`, `--exclude-airline IJ`, `--depart-after HH:MM`, `--depart-before HH:MM` | apply to OUTBOUND leg |
| Ranking | `--sort {price,duration,depart}`, `--limit N\|all` | default sort=price, limit=1 (table) / 5 (--json) |
| Perf | `--concurrency N` | default 3, raise to 5-8 for big scans |
| Output | `--json` | structured, agent-friendly |
| Auxiliary | `flight-price help \| man \| --version` | docs |

ORIGIN / DEST accept either 3-letter IATA city codes (BJS = any Beijing airport, SHA = any Shanghai, TYO = any Tokyo) or specific airport codes (PEK, PVG, NRT, HND).

## JSON output schema

Top-level:

```jsonc
{
  "origin": "BJS",
  "dest": "HGH",
  "mode": "OW" | "RT" | "PAIRS",
  "date_from": "2026-06-18",
  "date_to": "2026-06-22",
  "filters": { "direct": false, "max_stops": null, "airline": null, ... },
  "sort": "price",
  "limit": 5,
  "results": [ /* per-query, see below */ ],
  "flights": [ /* FLAT array, ranked across all queries — READ THIS FIRST */ ]
}
```

Each `results[i]`:

```jsonc
{
  "date": "2026-06-19",
  "return_date": "2026-06-22",
  "stay_nights": 3,          // null for one-way
  "status": "ok" | "no_results" | "timeout",
  "n_options_total": 39,     // before filter+limit
  "n_options_returned": 5,
  "options": [ /* FlightOption list */ ]
}
```

Each `flights[i]` (and `options[j]`) is a `FlightOption`:

```jsonc
{
  "price": 1460.0,
  "currency": "CNY",
  "direct": true,                // outbound is non-stop
  "airline": "CA",                // outbound first-segment carrier
  "flight_nos": ["CA1732"],       // outbound flight numbers
  "segments": [ /* full outbound detail: airport, terminal, time, duration */ ],
  "layovers": [ /* outbound layovers if any */ ],
  "return_flights": [             // RT only; empty array for OW
    {"flight_no": "CA8367", "airline": "CA", "depart_time": "2026-06-22 07:55:00"}
  ],
  "return_stops": 0,
  "return_direct": true,
  "depart_time": "2026-06-19 21:35:00",
  "total_duration_min": 155,
  // flights[] entries also carry date / return_date / stay_nights for context
  "date": "2026-06-19",
  "return_date": "2026-06-22",
  "stay_nights": 3
}
```

## Status field semantics — important for retry decisions

| status | meaning | agent action |
|---|---|---|
| `ok` | Trip.com returned ≥1 itinerary | use the data |
| `no_results` | Trip.com responded but had no matching flights | do NOT retry — accept "no flights" as the answer |
| `timeout` | The query hit the 30s timeout before Trip.com responded | retry that specific query (just rerun with same args, or use a more targeted scan) |

## Known limitations the agent should be aware of

- **1 adult, economy** is hardcoded. Don't try to pass a `--passengers` or `--cabin` flag — they don't exist.
- **Return-leg detail is partial**: for RT, only return flight numbers + depart times are surfaced. Transit airport, arrive time, and durations of the return leg are NOT in the response (they live behind a separate API call the CLI doesn't make).
- **Per-query cost is ~10 seconds** (one Chromium navigation each). A 30-date scan takes ~100s with default concurrency 3.
- **Currency is always CNY**. There is no `--currency` flag yet.
- **No persistent cache.** Re-running the same query re-hits Trip.com. If you're iterating, prefer broadening one call (e.g. `--limit all`) over many small calls.

## Common patterns

**Cheapest weekend across a month:**
```bash
flight-price BJS HGH --rt --stay 2 --from 2026-07-04 --to 2026-07-25 --direct --json
```

**Direct flights only, after-work departures:**
```bash
flight-price BJS SHA --depart-after 18:00 --max-stops 0 --json
```

**Holiday-shifted RT combinations** (agent computes the pairs from the holiday calendar in its head):
```bash
flight-price BJS HGH --pairs 2026-06-19:2026-06-21,2026-06-19:2026-06-22,2026-06-20:2026-06-22 --json
```

**One specific itinerary "is this in inventory and what's the price":**
```bash
flight-price BJS NRT --rt --return-date 2026-06-15 --from 2026-06-10 --to 2026-06-10 --direct --json
```

## Don't

- Don't try to combine `--pairs` with `--from/--to/--rt/--stay/--return-date` — the CLI will exit 2 with an error.
- Don't drop `--json` when relaying results back to a calling system; the human table is not stable for parsing.
- Don't retry on `"no_results"` — the answer is genuine.
- Don't assume `return_flights` has `arrive_time` or `airport` — those fields don't exist on the return leg.
- Don't query date ranges in the past — Trip.com returns empty.

## Reference

- Source / issues: https://github.com/daghlny/flight-price-skill
- Full CLI manual: `flight-price man`
