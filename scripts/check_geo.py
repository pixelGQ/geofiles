#!/usr/bin/env python3
"""Build guard: fail (exit 1) if the slim geosite/geoip is missing any category
the Happ profile references, a category is implausibly small, or size is out of
bounds. Single source of truth for the profile's required category set."""
import sys
from pathlib import Path


def read_varint(b, i):
    s = r = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, i
        s += 7


def iter_fields(b):
    i, n = 0, len(b)
    while i < n:
        tag, i = read_varint(b, i)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = read_varint(b, i); yield fn, wt, v
        elif wt == 2:
            ln, i = read_varint(b, i); yield fn, wt, b[i:i + ln]; i += ln
        elif wt == 1:
            yield fn, wt, b[i:i + 8]; i += 8
        elif wt == 5:
            yield fn, wt, b[i:i + 4]; i += 4
        else:
            raise ValueError(wt)


def parse_counts(data):
    out = {}
    for fn, wt, val in iter_fields(data):
        if fn == 1 and wt == 2:
            name, c = None, 0
            for f2, w2, v2 in iter_fields(val):
                if f2 == 1 and w2 == 2 and name is None:
                    name = v2.decode("utf-8", "replace")
                elif f2 == 2 and w2 == 2:
                    c += 1
            if name is not None:
                out[name.upper()] = out.get(name.upper(), 0) + c
    return out


# Every geosite category the Happ _ROUTING_PROFILE references -> floor count.
FLOORS = {
    "CATEGORY-ADS": 30,  # slim curated ad list — kept small so the iOS NE stays under its ~50MB memory ceiling
    "WHITELIST": 300, "CATEGORY-GEOBLOCK-RU": 500, "CATEGORY-RU": 500,
    "CATEGORY-BANK-RU": 100, "CATEGORY-GOV-RU": 50, "CATEGORY-MEDIA-RU": 50,
    "CATEGORY-ECOMMERCE-RU": 50, "CATEGORY-RETAIL-RU": 30, "YANDEX": 50, "MAILRU-GROUP": 100,
    "WIN-SPY": 50, "TORRENT": 100,
    "PRIVATE": 10, "MICROSOFT": 10, "APPLE": 10, "GOOGLE-PLAY": 5, "GITHUB": 5,
    "YOUTUBE": 20, "TELEGRAM": 5, "STEAM": 10, "EPICGAMES": 5, "RIOT": 5,
    "TWITCH": 5, "PINTEREST": 5, "FACEIT": 1, "ESCAPEFROMTARKOV": 1, "TWITCH-ADS": 1,
}


# The full profile (geosite-full.dat, desktop/Android only) carries the whole
# Loyalsoldier category-ads-all: ~189k domains on 2026-09-06, ~5 MB. Floors are
# the same for every other category — it is the same build with one source swapped.
FULL_ADS_FLOOR = 100_000
SLIM_SIZE = (60_000, 700_000)        # keep small for iOS NE ~50MB memory ceiling
FULL_SIZE = (1_000_000, 12_000_000)  # never served to iOS; sanity bounds only


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--full" in sys.argv
    geosite = Path(args[0]).read_bytes()
    geoip = Path(args[1]).read_bytes()
    gs = parse_counts(geosite)
    gi = parse_counts(geoip)

    floors = dict(FLOORS)
    if full:
        floors["CATEGORY-ADS"] = FULL_ADS_FLOOR
    errs = []
    for cat, floor in floors.items():
        n = gs.get(cat, 0)
        if n < floor:
            errs.append(f"geosite {cat}: {n} < floor {floor}")
    for ip in ("PRIVATE", "DIRECT"):
        if gi.get(ip, 0) < 1:
            errs.append(f"geoip {ip}: missing/empty")
    size = len(geosite)
    lo, hi = FULL_SIZE if full else SLIM_SIZE
    if not (lo < size < hi):
        errs.append(f"geosite size {size} out of [{lo}, {hi}] ({'full' if full else 'slim'})")

    print(f"geosite={size}B  {len(gs)} categories   |   geoip {len(gi)} categories",
          file=sys.stderr)
    for c in sorted(floors):
        print(f"  {gs.get(c, 0):8d}  {c}", file=sys.stderr)
    print(f"  geoip: PRIVATE={gi.get('PRIVATE', 0)}  DIRECT={gi.get('DIRECT', 0)}",
          file=sys.stderr)

    if errs:
        print("GUARD FAILED:", file=sys.stderr)
        for e in errs:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)
    print("GUARD OK", file=sys.stderr)


if __name__ == "__main__":
    main()
