"""Long-form documentation rendered by `flight-price man`."""

from . import __version__


MAN_PAGE = f"""\
FLIGHT-PRICE(1)                  User Commands                  FLIGHT-PRICE(1)

NAME
    flight-price - query Trip.com for actually-bookable flight prices across
    a date range

VERSION
    {__version__}

SYNOPSIS
    flight-price ORIGIN DEST [--from DATE] [--to DATE]
                 [--rt (--stay N | --return-date DATE)]
                 [--pairs OUT[:RET][,OUT[:RET]...]]
                 [--limit N|all] [--sort {{price,duration,depart}}]
                 [--direct] [--max-stops N]
                 [--airline LIST] [--exclude-airline LIST]
                 [--depart-after HH:MM] [--depart-before HH:MM]
                 [--concurrency N] [--json] [--headed]
    flight-price help
    flight-price man
    flight-price --version

DESCRIPTION
    Scan a date range and report bookable flights per departure date.
    By default the cheapest itinerary per date is shown; pass --limit N
    to surface the top-N alternatives (useful when you care about more
    than price — preferred carrier, time-of-day, direct vs. transfer).
    Prices come from Trip.com's per-itinerary search endpoint
    (FlightListSearch), so each quoted price corresponds to a real
    flight inventory item — not a promotional "from" price stitched
    from cached snapshots.

    Both one-way (default) and round-trip are supported. In round-trip
    mode the return-leg flight numbers + depart times are decoded from
    Trip.com's policy ID (see DATA SOURCE NOTES); full per-segment
    detail (transit airport, arrive time, duration) for the return is
    not currently fetched.

POSITIONAL ARGUMENTS
    ORIGIN
        Origin city code. Use either a 3-letter IATA city code (BJS, SHA,
        CAN, CTU, SZX, ...) or, for cities with multiple airports, the
        specific airport code (PEK, PKX, SHA, PVG, ...). City codes
        aggregate all the city's airports.

    DEST
        Destination city or airport code. Same rules as ORIGIN. International
        examples: TYO (any Tokyo airport), NRT (Narita), HND (Haneda), SEL
        (any Seoul airport), ICN (Incheon), SIN, BKK, LAX, JFK, LHR.

DATE OPTIONS
    --from YYYY-MM-DD
        Start of the outbound-date range. Default: today.

    --to YYYY-MM-DD
        End of the outbound-date range. Default: --from + 7 days.

ROUND-TRIP OPTIONS
    --rt
        Enable round-trip mode. Requires either --stay or --return-date.

    --stay N
        Stay length in nights. Return date is computed per outbound as
        outbound + N days. Use this to scan "weekend trip" style queries
        (--stay 2 for 3-day weekends, --stay 7 for a week, etc.).

    --return-date YYYY-MM-DD
        Fix the return date and scan outbound dates against it. Use this
        when the return is already decided and only the outbound is flexible.

EXPLICIT DATE PAIRS
    --pairs OUT[:RET][,OUT[:RET]...]
        Scan an arbitrary list of date combinations in one invocation.
        Each pair is either a single date (one-way) or OUT:RET (round-
        trip). Comma-separates multiple pairs. Mutually exclusive with
        --from/--to/--rt/--stay/--return-date.

        Example — five Duanwu holiday RT variants for a 2-3 night trip
        with up to one day of leave, in a single command:

          --pairs 2026-06-18:2026-06-20,2026-06-18:2026-06-21,\\
                  2026-06-19:2026-06-21,2026-06-19:2026-06-22,\\
                  2026-06-20:2026-06-22

        All pairs scan in parallel under --concurrency. Designed for AI
        agents that compute their own date combinations from a prompt
        and want one round-trip to the tool.

FILTER OPTIONS
    --direct
        Shortcut for `--max-stops 0` — show only itineraries with no
        outbound stops.

    --max-stops N
        Maximum outbound stops. 0 = direct only, 1 = up to one transfer.
        Applies to the OUTBOUND leg only; return-leg stops are not
        filtered (Trip.com's list endpoint doesn't surface them
        cleanly).

    --airline CODE[,CODE,...]
        Comma-separated allow-list of airline IATA codes for the
        outbound first segment, e.g. `--airline CA,MU,HU`. Only
        itineraries whose outbound starts with one of these carriers
        are kept.

    --exclude-airline CODE[,CODE,...]
        Comma-separated deny-list. Useful e.g. `--exclude-airline IJ`
        to skip Spring Japan (low-cost, restrictive baggage).

    --depart-after HH:MM
        Keep only itineraries whose first outbound segment departs at
        or after this local time. e.g. `--depart-after 18:00` for
        post-work flights.

    --depart-before HH:MM
        Keep only itineraries whose first outbound segment departs at
        or before this local time. Combine with --depart-after to
        bound a window: `--depart-after 06:00 --depart-before 09:00`.

RANKING OPTIONS
    --limit N|all
        How many itineraries to show per date. Default depends on output
        mode: 1 for the human table, 5 for --json (agents typically want
        several candidates to choose from). `--limit all` returns every
        matching itinerary the search returned (typically 30-50 per date
        for major routes).

    --sort {{price,duration,depart}}
        How to rank itineraries within a date. Default: price (ascending).
        `duration` ranks by outbound total trip time; `depart` ranks by
        outbound depart time (earliest first).

PERFORMANCE OPTIONS
    --concurrency N
        How many dates to search in parallel. Default: 3. Each query
        launches a Chromium tab and waits for Trip.com to return the
        flight list; concurrency 3 keeps the load gentle. Raising to
        5-8 is usually safe; >10 may trigger Trip.com rate-limits.

OUTPUT OPTIONS
    --json
        Emit machine-readable JSON instead of the human-readable table.
        Includes full per-flight detail: segments (airline, flight number,
        depart/arrive airport+terminal, time, duration) and layovers
        (transit airport, duration in minutes, whether the terminal
        changes).

    --headed
        Show the underlying Chromium window. Useful when diagnosing why
        prices look wrong or when Trip.com returns no results.

EXAMPLES
    # Cheapest one-way Beijing→Shanghai across a week
    flight-price BJS SHA --from 2026-06-01 --to 2026-06-07

    # Top 5 cheapest itineraries per date for a weekend Tokyo trip
    flight-price BJS TYO --from 2026-06-06 --to 2026-06-06 --rt --stay 2 --limit 5

    # All Friday-departure options for a 7-night trip across June
    flight-price BJS SIN --from 2026-06-05 --to 2026-06-26 --rt --stay 7

    # Fix the return at June 15, find cheapest outbound that week
    flight-price BJS NRT --from 2026-06-10 --to 2026-06-14 --rt --return-date 2026-06-15

    # Direct flights only, ranked by depart time
    flight-price BJS TYO --from 2026-06-01 --to 2026-06-15 --direct --sort depart

    # After-work flight on a specific airline
    flight-price BJS SHA --depart-after 18:00 --airline MU,CA --limit 3

    # Avoid budget carriers
    flight-price BJS TYO --rt --stay 2 --exclude-airline IJ,9C

    # JSON for an AI agent to consume (full itineraries + return-leg data)
    flight-price BJS TYO --from 2026-06-06 --to 2026-06-06 --rt --stay 2 --limit all --json

    # Agent-style: 5 hand-picked RT combos in one call, ranked across all
    flight-price BJS HGH --pairs 2026-06-18:2026-06-20,2026-06-19:2026-06-21,2026-06-19:2026-06-22,2026-06-20:2026-06-22 --json

JSON OUTPUT FORMAT (--json)
    Top-level keys:
        origin, dest      query inputs (echoed back)
        mode              "OW" | "RT" | "PAIRS"
        date_from/to      min/max date seen in the query
        filters           the filters that were applied
        sort, limit       ranking/cap that were applied
        results[]         per-query results (see below)
        flights[]         FLAT array of every returned itinerary across
                          all queries, ranked by --sort. Each entry is
                          a full FlightOption with date/return_date/
                          stay_nights added for context. This is the
                          fast path for "what's the best overall?".

    results[] entry:
        date              outbound date
        return_date       return date (RT only)
        stay_nights       return_date - date in nights (RT only)
        status            "ok" | "no_results" | "timeout"
                            ok         flights returned
                            no_results Trip.com responded but no flights
                            timeout    Trip.com didn't respond — agent
                                       should retry this query only
        n_options_total   total itineraries seen before filter+limit
        n_options_returned  itineraries actually included
        options[]         FlightOption list, sorted by --sort

    FlightOption fields:
        price, currency
        direct            outbound is non-stop
        airline           IATA code of outbound first segment
        flight_nos        outbound flight numbers in order
        segments[]        outbound full segment detail
        layovers[]        outbound layovers (airport, duration, change_terminal)
        return_flights[]  return-leg flight nums + depart times (RT only)
        return_stops      return-leg stop count
        return_direct     return-leg is non-stop
        depart_time       outbound first segment depart datetime (CST)
        total_duration_min  outbound first depart -> last arrive

OUTPUT FORMAT
    Table mode (default) prints one row per itinerary (with --limit 1 that's
    one row per date):

        date        [return]       CNY  type    dep    dur     airline  outbound                   [return-leg]
        2026-06-06  2026-06-08    2375  1-stop  09:20  10h45m  HX       HX305+HX630 via HKG 1h5m*  HX607@08:30+HX304@20:20

    Columns:
        date        outbound departure date (blank on continuation rows in --limit > 1)
        return      shown only in --rt mode (return depart date)
        CNY         total bookable price in CNY (round-trip total in --rt)
        type        "direct" or "N-stop" (outbound)
        dep         outbound first-segment depart time (HH:MM, local)
        dur         outbound total duration (first depart → last arrive)
        airline     2-letter IATA code of the outbound first segment
        outbound    flight numbers, "via XXX Hh Mm" for transfers;
                    trailing "*" on a layover means it requires a terminal change
        return-leg  return flight numbers + depart time (HH:MM), e.g.
                    "HX607@08:30+HX304@20:20" (RT only)

    The row(s) with the lowest price across the entire result set are
    marked with " *" at the end.

DATA SOURCE NOTES
    Data comes from tw.trip.com (Trip.com's Taiwan site) with `curr=CNY`,
    which returns prices natively in CNY without conversion. No login is
    required.

    Trip.com's calendar endpoint (GetLowPriceInCalender) is NOT used:
    it returns marketing "from" prices that don't correspond to specific
    bookable flights. We rely instead on FlightListSearch / SSE, which
    returns real inventory.

    Round-trip return-leg detail: Trip.com's list endpoint only returns
    outbound segment detail in `journeyList`. The return-leg flight
    numbers + depart timestamps are encoded inside the response's
    `shortPolicyId` field, which we decode. This gives you the airline,
    flight numbers, and depart times of the return leg without any
    extra request — but the return-leg transit airport, arrive time,
    and segment durations are not surfaced (they live behind a separate
    FlightDetail call that we don't make).

    Airline-code quirks:
        IJ  Spring Japan      (Spring Airlines' Japanese subsidiary,
                              flies BJS-TYO; mainland's 9C does not)
        HX  Hong Kong Airlines (common 1-stop via HKG for BJS-TYO)
        CX  Cathay Pacific
        CA  Air China
        MU  China Eastern
        HU  Hainan Airlines

LIMITATIONS
    - 1 adult passenger, economy class — not yet configurable.
    - Multi-city itineraries not supported.
    - No historical price tracking or price-drop alerts.
    - Round-trip return-leg detail is limited to flight numbers + depart
      time (no transit airport, no arrive time, no terminal info) — see
      DATA SOURCE NOTES.

EXIT STATUS
    0   success
    2   invalid arguments

SEE ALSO
    flight-price help    Short usage and option summary
"""


HELP_HINTS = """\

For more detail and examples:
    flight-price man

For version:
    flight-price --version
"""
