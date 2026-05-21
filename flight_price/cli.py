"""Command-line entry point.

Default mode is one-way (single-leg) scan. Pass --rt --stay N (or
--return-date) to scan round-trip itineraries.

Data is sourced from Trip.com's FlightListSearch endpoint — the same
backend that powers the visible flight list on tw.trip.com. Prices, airlines,
and direct/transfer info reflect what's actually bookable. (We deliberately
do NOT use the GetLowPriceInCalender "from" prices; those don't correspond
to any specific flight.)

This means each date in your range triggers a separate search, so a 30-day
scan takes ~60-120 seconds (3 concurrent searches by default).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import date, time, timedelta

from . import __version__
from .help_text import MAN_PAGE, HELP_HINTS
from .trip import (
    DayResult,
    FlightOption,
    query_oneway_range,
    query_pairs,
    query_roundtrip_range,
)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _parse_hhmm(s: str) -> time:
    """HH:MM → time. Used by --depart-after / --depart-before."""
    h, _, m = s.partition(":")
    return time(hour=int(h), minute=int(m or 0))


def _parse_limit(s: str) -> int:
    """--limit accepts a positive int or 'all' (== 0, meaning unlimited)."""
    if s.lower() in ("all", "0"):
        return 0
    n = int(s)
    if n < 0:
        raise argparse.ArgumentTypeError("--limit must be >= 0 or 'all'")
    return n


def _parse_csv_upper(s: str) -> list[str]:
    return [tok.strip().upper() for tok in s.split(",") if tok.strip()]


def _parse_pairs(s: str) -> list[tuple[str, str | None]]:
    """Parse OUT[:RET][,OUT[:RET]...] into a list of date pairs.

    Examples:
        "2026-06-19"                          -> [("2026-06-19", None)]
        "2026-06-19:2026-06-21"               -> [("2026-06-19", "2026-06-21")]
        "2026-06-18:2026-06-20,2026-06-19"    -> mixed RT + OW
    """
    pairs: list[tuple[str, str | None]] = []
    for item in s.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            out, ret = item.split(":", 1)
            out, ret = out.strip(), ret.strip()
            try:
                date.fromisoformat(out)
                date.fromisoformat(ret)
            except ValueError as e:
                raise argparse.ArgumentTypeError(
                    f"bad pair '{item}': {e}"
                ) from None
            pairs.append((out, ret))
        else:
            try:
                date.fromisoformat(item)
            except ValueError as e:
                raise argparse.ArgumentTypeError(
                    f"bad date '{item}': {e}"
                ) from None
            pairs.append((item, None))
    if not pairs:
        raise argparse.ArgumentTypeError("--pairs is empty")
    return pairs


def _stay_nights(out_iso: str, ret_iso: str | None) -> int | None:
    if not ret_iso:
        return None
    return (date.fromisoformat(ret_iso) - date.fromisoformat(out_iso)).days


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flight-price",
        description=(
            "Query Trip.com for bookable flight prices across a date range. "
            "Defaults to one-way + cheapest-per-date; use --limit, --airline, "
            "--depart-after etc. to surface alternatives, or --rt for round-trip."
        ),
        epilog=(
            "Examples:\n"
            "  flight-price BJS SHA --from 2026-06-01 --to 2026-06-07\n"
            "  flight-price BJS TYO --from 2026-06-06 --to 2026-06-06 --rt --stay 2 --limit 5\n"
            "  flight-price BJS NRT --rt --return-date 2026-06-15 --airline CA,MU\n"
            "  flight-price BJS SHA --depart-after 18:00 --max-stops 0\n"
            "\n"
            "Run `flight-price man` for the full manual.\n"
            "Run `flight-price --version` for version info."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--version", action="version", version=f"flight-price {__version__}",
    )
    p.add_argument("origin", help="origin city code, e.g. BJS, SHA, PEK")
    p.add_argument("dest", help="dest city code, e.g. SHA, TYO, NRT")
    p.add_argument(
        "--from", dest="date_from", type=_parse_date, default=None,
        help="start of outbound date range YYYY-MM-DD (default: today)",
    )
    p.add_argument(
        "--to", dest="date_to", type=_parse_date, default=None,
        help="end of outbound date range YYYY-MM-DD (default: from + 7 days)",
    )
    p.add_argument(
        "--rt", action="store_true",
        help="round-trip mode (requires --stay or --return-date)",
    )
    p.add_argument(
        "--stay", type=int, default=None,
        help="round-trip stay in nights (return = outbound + N days)",
    )
    p.add_argument(
        "--return-date", type=_parse_date, default=None,
        help="fix the return date instead of using --stay",
    )
    p.add_argument(
        "--pairs", type=_parse_pairs, default=None,
        help=(
            "explicit (outbound[:return]) date pairs, comma-separated; "
            "e.g. 2026-06-18:2026-06-20,2026-06-19:2026-06-22. "
            "Mutually exclusive with --from/--to/--rt/--stay/--return-date."
        ),
    )

    # Filters
    p.add_argument(
        "--direct", action="store_true",
        help="shortcut for --max-stops 0 (outbound direct only)",
    )
    p.add_argument(
        "--max-stops", type=int, default=None,
        help="max outbound stops (0 = direct only, 1 = up to one transfer, ...)",
    )
    p.add_argument(
        "--airline", type=_parse_csv_upper, default=None,
        help="comma-separated airline allow-list, e.g. CA,MU,HU",
    )
    p.add_argument(
        "--exclude-airline", type=_parse_csv_upper, default=None,
        help="comma-separated airline deny-list, e.g. IJ,9C",
    )
    p.add_argument(
        "--depart-after", type=_parse_hhmm, default=None,
        help="show only flights departing at or after this time (HH:MM, local)",
    )
    p.add_argument(
        "--depart-before", type=_parse_hhmm, default=None,
        help="show only flights departing at or before this time (HH:MM, local)",
    )

    # Ranking / display
    p.add_argument(
        "--limit", type=_parse_limit, default=None,
        help=(
            "how many itineraries to show per date "
            "(default: 1 for table, 5 for --json; 'all' for no cap)"
        ),
    )
    p.add_argument(
        "--sort", choices=("price", "duration", "depart"), default="price",
        help="rank itineraries by this key (default: price)",
    )

    # Perf / IO
    p.add_argument(
        "--concurrency", type=int, default=3,
        help="how many dates to scan in parallel (default: 3)",
    )
    p.add_argument(
        "--json", action="store_true",
        help="output JSON instead of a table",
    )
    p.add_argument(
        "--headed", action="store_true",
        help="show the underlying browser window (for debugging)",
    )
    return p


def _fmt_duration(minutes: int) -> str:
    if minutes <= 0:
        return ""
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h{m}m"
    return f"{h}h" if h else f"{m}m"


def _fmt_outbound(o: FlightOption) -> str:
    if not o.flight_nos:
        return o.airline or "-"
    if o.direct or not o.layovers:
        return "+".join(o.flight_nos)
    via = ",".join(
        f"{lv.airport} {_fmt_duration(lv.duration_min)}"
        + ("*" if lv.change_terminal else "")
        for lv in o.layovers
    )
    return f"{'+'.join(o.flight_nos)} via {via}"


def _fmt_hhmm(iso_dt: str) -> str:
    # "2026-06-06 09:20:00" → "09:20"
    if not iso_dt or len(iso_dt) < 16:
        return ""
    return iso_dt[11:16]


def _fmt_return(o: FlightOption) -> str:
    if not o.return_flights:
        return ""
    parts = []
    for rf in o.return_flights:
        parts.append(f"{rf.flight_no}@{_fmt_hhmm(rf.depart_time)}")
    return "+".join(parts)


def _flight_passes_filters(o: FlightOption, args: argparse.Namespace) -> bool:
    # Stops filter (effective from --max-stops or --direct)
    max_stops = args.max_stops
    if args.direct and max_stops is None:
        max_stops = 0
    if max_stops is not None:
        out_stops = max(0, len(o.flight_nos) - 1)
        if out_stops > max_stops:
            return False

    if args.airline and o.airline.upper() not in args.airline:
        return False
    if args.exclude_airline and o.airline.upper() in args.exclude_airline:
        return False

    if args.depart_after or args.depart_before:
        hhmm = _fmt_hhmm(o.depart_time)
        if not hhmm:
            return False
        h, m = int(hhmm[:2]), int(hhmm[3:5])
        t = time(hour=h, minute=m)
        if args.depart_after and t < args.depart_after:
            return False
        if args.depart_before and t > args.depart_before:
            return False
    return True


_SORT_KEYS = {
    "price": lambda o: (o.price, o.depart_time),
    "duration": lambda o: (o.total_duration_min or 10**9, o.price),
    "depart": lambda o: (o.depart_time or "~", o.price),
}


def _apply_filters_sort_limit(
    options: list[FlightOption], args: argparse.Namespace,
) -> list[FlightOption]:
    filtered = [o for o in options if _flight_passes_filters(o, args)]
    filtered.sort(key=_SORT_KEYS[args.sort])
    if args.limit and args.limit > 0:
        filtered = filtered[: args.limit]
    return filtered


def _render_table(
    origin: str, dest: str, df: date, dt_: date,
    rows: list[DayResult], *, mode_label: str, args: argparse.Namespace,
) -> str:
    has_rt = any(r.return_date for r in rows)

    chosen: list[tuple[DayResult, list[FlightOption]]] = []
    for r in rows:
        picks = _apply_filters_sort_limit(r.options, args)
        if picks:
            chosen.append((r, picks))

    if not chosen:
        kind = "matching " if (args.direct or args.max_stops is not None or args.airline or args.exclude_airline or args.depart_after or args.depart_before) else ""
        return (
            f"{origin.upper()} → {dest.upper()}  {df}~{dt_}  {mode_label}  "
            f"(no {kind}flights found)\n"
        )

    currency = chosen[0][1][0].currency
    flat_price_min = min(p.price for _, opts in chosen for p in opts)
    total_rows = sum(len(opts) for _, opts in chosen)

    # Column widths
    cols = [
        ("date", 12),
    ]
    if has_rt:
        cols.append(("return", 12))
    cols += [
        (currency, 6),
        ("type", 6),
        ("dep", 5),
        ("dur", 6),
        ("airline", 7),
        ("outbound", 32),
    ]
    if has_rt:
        cols.append(("return-leg", 32))

    def _row(cells: list[str]) -> str:
        out = []
        for (name, w), c in zip(cols, cells):
            if name == currency:
                out.append(f"{c:>{w}}")
            else:
                out.append(f"{c:<{w}}")
        return "  ".join(out)

    header = _row([name for name, _ in cols])

    lines = [
        f"{origin.upper()} → {dest.upper()}  {df}~{dt_}  {mode_label}  "
        f"({len(chosen)} day{'s' if len(chosen) != 1 else ''}, "
        f"{total_rows} option{'s' if total_rows != 1 else ''}, "
        f"min={flat_price_min:.0f} {currency})",
        header,
        "-" * len(header),
    ]

    multi = (args.limit or 0) != 1
    has_terminal_note = False
    for di, (r, picks) in enumerate(chosen):
        if multi and di > 0:
            lines.append("")
        for pi, p in enumerate(picks):
            stops = max(0, len(p.flight_nos) - 1)
            type_str = "direct" if stops == 0 else f"{stops}-stop"
            cells = [r.date if pi == 0 or not multi else ""]
            if has_rt:
                cells.append(r.return_date or "" if (pi == 0 or not multi) else "")
            cells += [
                f"{p.price:.0f}",
                type_str,
                _fmt_hhmm(p.depart_time),
                _fmt_duration(p.total_duration_min),
                p.airline or "-",
                _fmt_outbound(p),
            ]
            if has_rt:
                cells.append(_fmt_return(p) or "-")
            mark = "  *" if p.price == flat_price_min else ""
            lines.append(_row(cells) + mark)
            if any(lv.change_terminal for lv in p.layovers):
                has_terminal_note = True

    note = ""
    if has_terminal_note:
        note = "\n  (* after a layover airport = requires terminal change)\n"
    return "\n".join(lines) + "\n" + note


def _to_jsonable(r: DayResult, picks: list[FlightOption]) -> dict:
    return {
        "date": r.date,
        "return_date": r.return_date,
        "stay_nights": _stay_nights(r.date, r.return_date),
        "status": r.status,
        "n_options_total": r.n_options,
        "n_options_returned": len(picks),
        "options": [asdict(o) for o in picks],
    }


def _flatten_flights(
    per_day: list[tuple[DayResult, list[FlightOption]]],
    sort_key: str,
) -> list[dict]:
    """Flat array of every returned itinerary, ranked by the user's --sort.

    Each entry is a FlightOption + the query context it came from (date,
    return_date, stay_nights). This is the "give me the best across ALL my
    queries" view that agents commonly want — saves them an O(N*M) traversal.
    """
    out: list[dict] = []
    for r, picks in per_day:
        stay = _stay_nights(r.date, r.return_date)
        for o in picks:
            d = asdict(o)
            d["date"] = r.date
            d["return_date"] = r.return_date
            d["stay_nights"] = stay
            out.append(d)
    out.sort(key=lambda d: (
        d["price"] if sort_key == "price"
        else (d["total_duration_min"] or 10**9) if sort_key == "duration"
        else (d["depart_time"] or "~")
    ))
    return out


def _try_subcommand(argv: list[str]) -> int | None:
    """Handle `help` / `man` / no-args sub-commands before argparse runs.

    Returns an exit code if a sub-command handled the call; otherwise None
    (meaning: fall through to the normal argparse flow).
    """
    if not argv:
        _build_parser().print_help()
        print(HELP_HINTS)
        return 0
    cmd = argv[0]
    if cmd in ("help",):
        _build_parser().print_help()
        print(HELP_HINTS)
        return 0
    if cmd in ("man",):
        if sys.stdout.isatty():
            import os
            pager = os.environ.get("PAGER", "less -R")
            try:
                import subprocess
                proc = subprocess.Popen(pager.split(), stdin=subprocess.PIPE)
                proc.communicate(MAN_PAGE.encode())
                return proc.returncode or 0
            except FileNotFoundError:
                pass
        sys.stdout.write(MAN_PAGE)
        return 0
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    sub_rc = _try_subcommand(argv)
    if sub_rc is not None:
        return sub_rc
    args = _build_parser().parse_args(argv)

    # Resolve --limit default based on output mode (agents typically want
    # several options to choose from, humans want the one-line answer).
    if args.limit is None:
        args.limit = 5 if args.json else 1

    # --pairs is mutually exclusive with the date-range / RT flags.
    if args.pairs is not None:
        conflicting = [
            n for n, v in (
                ("--from", args.date_from), ("--to", args.date_to),
                ("--rt", args.rt or None),
                ("--stay", args.stay), ("--return-date", args.return_date),
            ) if v
        ]
        if conflicting:
            print(
                f"error: --pairs cannot be combined with: {', '.join(conflicting)}",
                file=sys.stderr,
            )
            return 2
        pairs = args.pairs
        # df/dt_ are still used in the table header; pick min/max across pairs.
        all_dates = [date.fromisoformat(o) for o, _ in pairs] + \
                    [date.fromisoformat(r) for _, r in pairs if r]
        df = min(all_dates)
        dt_ = max(all_dates)
        n_rt = sum(1 for _, r in pairs if r)
        if n_rt == len(pairs):
            mode_label = f"PAIRS ({len(pairs)} RT)"
        elif n_rt == 0:
            mode_label = f"PAIRS ({len(pairs)} OW)"
        else:
            mode_label = f"PAIRS ({n_rt} RT + {len(pairs) - n_rt} OW)"
        print(
            f"scanning {len(pairs)} explicit pair(s) "
            f"(concurrency={args.concurrency}, ~{int(len(pairs) / args.concurrency * 10)}s)...",
            file=sys.stderr, flush=True,
        )
        rows = asyncio.run(
            query_pairs(
                args.origin, args.dest, pairs,
                headless=not args.headed,
                concurrency=args.concurrency,
            )
        )
    else:
        df = args.date_from or date.today()
        dt_ = args.date_to or (df + timedelta(days=7))
        if dt_ < df:
            print("error: --to must be on or after --from", file=sys.stderr)
            return 2

        if args.rt and not args.stay and not args.return_date:
            print(
                "error: --rt requires either --stay N (nights) or --return-date YYYY-MM-DD",
                file=sys.stderr,
            )
            return 2

        n_days = (dt_ - df).days + 1
        dates = [(df + timedelta(days=i)).isoformat() for i in range(n_days)]

        print(
            f"scanning {n_days} {'date pairs' if args.rt else 'dates'} "
            f"(concurrency={args.concurrency}, ~{int(n_days / args.concurrency * 10)}s)...",
            file=sys.stderr,
            flush=True,
        )

        if args.rt:
            if args.return_date:
                mode_label = f"RT (return={args.return_date})"
                rows = asyncio.run(
                    query_roundtrip_range(
                        args.origin, args.dest, dates,
                        return_date=args.return_date.isoformat(),
                        headless=not args.headed,
                        concurrency=args.concurrency,
                    )
                )
            else:
                mode_label = f"RT (stay={args.stay}n)"
                rows = asyncio.run(
                    query_roundtrip_range(
                        args.origin, args.dest, dates,
                        stay_nights=args.stay,
                        headless=not args.headed,
                        concurrency=args.concurrency,
                    )
                )
        else:
            mode_label = "OW"
            rows = asyncio.run(
                query_oneway_range(
                    args.origin, args.dest, dates,
                    headless=not args.headed,
                    concurrency=args.concurrency,
                )
            )

    if args.json:
        per_day = [
            (r, _apply_filters_sort_limit(r.options, args)) for r in rows
        ]
        if args.pairs is not None:
            mode = "PAIRS"
        elif args.rt:
            mode = "RT"
        else:
            mode = "OW"
        print(json.dumps(
            {
                "origin": args.origin.upper(),
                "dest": args.dest.upper(),
                "mode": mode,
                "date_from": df.isoformat(),
                "date_to": dt_.isoformat(),
                "stay_nights": args.stay if args.rt else None,
                "return_date": args.return_date.isoformat() if args.return_date else None,
                "filters": {
                    "direct": args.direct,
                    "max_stops": args.max_stops,
                    "airline": args.airline,
                    "exclude_airline": args.exclude_airline,
                    "depart_after": args.depart_after.isoformat() if args.depart_after else None,
                    "depart_before": args.depart_before.isoformat() if args.depart_before else None,
                },
                "sort": args.sort,
                "limit": args.limit or "all",
                "results": [_to_jsonable(r, picks) for r, picks in per_day],
                "flights": _flatten_flights(per_day, args.sort),
            },
            ensure_ascii=False, indent=2, default=str,
        ))
    else:
        print(_render_table(
            args.origin, args.dest, df, dt_, rows,
            mode_label=mode_label, args=args,
        ), end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())
