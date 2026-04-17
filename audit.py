"""
Audit Trail CLI — query the Anchovies audit log from the command line.

Usage:
    python -m anchovies.audit                     # Last 20 events
    python -m anchovies.audit --last 24h          # Last 24 hours
    python -m anchovies.audit --last 1h           # Last 1 hour
    python -m anchovies.audit --member sofia       # Sofia's events
    python -m anchovies.audit --type session_started
    python -m anchovies.audit --limit 50
    python -m anchovies.audit --member sofia --last 24h --type session_completed
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone


def parse_duration(s: str) -> float:
    """
    Parse a duration string like '24h', '30m', '7d' into seconds.

    Supported units: s (seconds), m (minutes), h (hours), d (days)
    """
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([smhd])$", s.strip().lower())
    if not match:
        raise ValueError(f"Invalid duration: '{s}'. Use format like 24h, 30m, 7d")
    value = float(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


def format_timestamp(ts: float) -> str:
    """Format a Unix timestamp as a human-readable local time."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def format_entry(entry) -> str:
    """Format a single audit entry for display."""
    ts = format_timestamp(entry.timestamp)
    member = entry.member or "-"
    details = ""
    if entry.details:
        # Show compact key=value pairs
        pairs = []
        for k, v in entry.details.items():
            if isinstance(v, str) and len(v) > 60:
                v = v[:57] + "..."
            pairs.append(f"{k}={v}")
        details = " | " + ", ".join(pairs)
    return f"  {ts}  {entry.event_type:<22}  {member:<10}{details}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Query the Anchovies audit trail",
        prog="python -m anchovies.audit",
    )
    parser.add_argument(
        "--last", metavar="DURATION",
        help="Show events from the last N hours/minutes/days (e.g., 24h, 30m, 7d)",
    )
    parser.add_argument(
        "--member", "-m", metavar="NAME",
        help="Filter by team member name",
    )
    parser.add_argument(
        "--type", "-t", metavar="EVENT_TYPE",
        help="Filter by event type (e.g., session_started, session_completed)",
    )
    parser.add_argument(
        "--limit", "-n", type=int, default=20,
        help="Max events to show (default: 20)",
    )

    args = parser.parse_args(argv)

    # Parse the --last duration
    since = None
    if args.last:
        try:
            seconds = parse_duration(args.last)
            since = time.time() - seconds
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Query the audit log
    try:
        from anchovies.storage import get_storage
        storage = get_storage()
        entries = storage.query_audit(
            since=since,
            member=args.member,
            event_type=args.type,
            limit=args.limit,
        )
    except Exception as e:
        print(f"Error reading audit log: {e}", file=sys.stderr)
        return 1

    if not entries:
        print("No audit events found matching your filters.")
        return 0

    # Header
    filters = []
    if args.last:
        filters.append(f"last {args.last}")
    if args.member:
        filters.append(f"member={args.member}")
    if args.type:
        filters.append(f"type={args.type}")
    filter_str = f" ({', '.join(filters)})" if filters else ""
    print(f"Audit Trail — {len(entries)} event(s){filter_str}:")
    print(f"  {'TIMESTAMP':<21}  {'EVENT TYPE':<22}  {'MEMBER':<10}  DETAILS")
    print(f"  {'-' * 80}")

    # Events (newest first from the query, display in same order)
    for entry in entries:
        print(format_entry(entry))

    return 0


if __name__ == "__main__":
    sys.exit(main())
