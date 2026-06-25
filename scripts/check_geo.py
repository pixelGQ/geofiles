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
    "CATEGORY-ADS": 40000,  # real ad list (Hagezi light), same name the profile already uses
    "WHITELIST": 300, "CATEGORY-GEOBLOCK-RU": 500, "CATEGORY-RU": 500,
    "CATEGORY-BANK-RU": 100, "CATEGORY-GOV-RU": 50, "CATEGORY-MEDIA-RU": 50,
    "CATEGORY-ECOMMERCE-RU": 50, "CATEGORY-RETAIL-RU": 30, "YANDEX": 50, "MAILRU-GROUP": 100,
    "WIN-SPY": 50, "TORRENT": 100,
    "PRIVATE": 10, "MICROSOFT": 10, "APPLE": 10, "GOOGLE-PLAY": 5, "GITHUB": 5,
    "YOUTUBE": 20, "TELEGRAM": 5, "STEAM": 10, "EPICGAMES": 5, "RIOT": 5,
    "TWITCH": 5, "PINTEREST": 5, "FACEIT": 1, "ESCAPEFROMTARKOV": 1, "TWITCH-ADS": 1,
}


def main():
    geosite = Path(sys.argv[1]).read_bytes()
    geoip = Path(sys.argv[2]).read_bytes()
    gs = parse_counts(geosite)
    gi = parse_counts(geoip)

    errs = []
    for cat, floor in FLOORS.items():
        n = gs.get(cat, 0)
        if n < floor:
            errs.append(f"geosite {cat}: {n} < floor {floor}")
    for ip in ("PRIVATE", "DIRECT"):
        if gi.get(ip, 0) < 1:
            errs.append(f"geoip {ip}: missing/empty")
    size = len(geosite)
    if not (1_000_000 < size < 18_000_000):
        errs.append(f"geosite size {size} out of [1MB, 18MB]")

    print(f"geosite={size}B  {len(gs)} categories   |   geoip {len(gi)} categories",
          file=sys.stderr)
    for c in sorted(FLOORS):
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
