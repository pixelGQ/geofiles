#!/usr/bin/env python3
"""Build BoltBoost's slim geosite.dat (+ geoip passthrough).

Extract-merge: parse upstream prebuilt v2ray geosite .dat files (keeping each
Domain submessage's raw bytes — type + value + attributes), union the categories
we need (cherry-picking per source, renaming on extract so the Happ profile's
names stay stable), fold in a trimmed ad-domain list, and re-serialize one small
geosite.dat. GeoIP is passed through verbatim from upstream.

Deterministic output: domains sorted within a category, categories sorted by name
— so an unchanged upstream produces a byte-identical file (no spurious commits).

Pure stdlib. Protobuf shapes:
  GeoSiteList { repeated GeoSite entry = 1 }
  GeoSite     { string country_code = 1; repeated Domain domain = 2 }
  Domain      { Type type = 1; string value = 2; repeated Attribute attribute = 3 }
  Domain.Type { Plain=0; Regex=1; RootDomain=2; Full=3 }
"""
import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

# ---------- protobuf primitives ----------

def read_varint(b, i):
    shift = result = 0
    while True:
        x = b[i]; i += 1
        result |= (x & 0x7F) << shift
        if not x & 0x80:
            return result, i
        shift += 7


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
            raise ValueError(f"bad wire type {wt}")


def enc_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def ld(fn, payload):
    return enc_varint((fn << 3) | 2) + enc_varint(len(payload)) + payload


# ---------- geosite parse / domain helpers ----------

def parse_geosite(data):
    """-> dict UPPERCASE_name -> list[raw Domain submessage bytes]"""
    out = {}
    for fn, wt, val in iter_fields(data):
        if fn == 1 and wt == 2:  # GeoSite entry
            name, domains = None, []
            for f2, w2, v2 in iter_fields(val):
                if f2 == 1 and w2 == 2 and name is None:
                    name = v2.decode("utf-8", "replace")
                elif f2 == 2 and w2 == 2:
                    domains.append(v2)
            if name is not None:
                out.setdefault(name.upper(), []).extend(domains)
    return out


def domain_key(raw):
    """(type, value) — dedup key; ignores attributes."""
    t, v = 0, b""
    for fn, wt, val in iter_fields(raw):
        if fn == 1 and wt == 0:
            t = val
        elif fn == 2 and wt == 2:
            v = val
    return (t, v)


ROOTDOMAIN = 2  # matches the domain and all subdomains ("domain:")


def enc_domain(value, dtype=ROOTDOMAIN):
    return enc_varint((1 << 3) | 0) + enc_varint(dtype) + ld(2, value.encode("utf-8"))


def build_category(name, raw_domains):
    body = bytearray(ld(1, name.upper().encode()))
    for d in sorted(raw_domains, key=domain_key):
        body += ld(2, d)
    return ld(1, bytes(body))


# ---------- ad / hosts list parsing ----------

_DOMAIN_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789.-_")


def parse_adlist(text):
    domains = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#!":
            continue
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("0.0.0.0", "127.0.0.1", "::", "::1"):
            line = parts[1]
        elif len(parts) == 1:
            line = parts[0]
        else:
            continue
        if line.startswith("||"):
            line = line[2:]
        line = line.strip("^").lstrip("*.").rstrip(".").lower()
        if not line or "." not in line:
            continue
        if set(line) <= _DOMAIN_CHARS:
            domains.add(line)
    return domains


# ---------- fetch (http or local path), retry + optional cache ----------

def fetch_bytes(url, cache_dir=None):
    if not url.startswith(("http://", "https://")):
        return Path(url).read_bytes()
    cache = None
    if cache_dir:
        key = hashlib.md5(url.encode()).hexdigest()[:16]
        cache = Path(cache_dir) / key
        if cache.exists():
            return cache.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "boltvpn-geo-builder"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(data)
            return data
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"failed to fetch {url}: {last}")


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sources.json")
    ap.add_argument("--out-geosite", default="geosite.dat.new")
    ap.add_argument("--out-geoip", default="geoip.dat.new")
    ap.add_argument("--cache", default=None, help="download cache dir (offline reuse)")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text())
    base = cfg_path.parent

    parsed = {}
    for sid, s in cfg["sources"].items():
        parsed[sid] = parse_geosite(fetch_bytes(s["url"], args.cache))
        print(f"[src] {sid}: {len(parsed[sid])} categories", file=sys.stderr)

    adlists = {}
    for aid, a in cfg.get("adlists", {}).items():
        txt = fetch_bytes(a["url"], args.cache).decode("utf-8", "replace")
        adlists[aid] = parse_adlist(txt)
        print(f"[ad ] {aid}: {len(adlists[aid])} domains", file=sys.stderr)

    allow = set()
    if cfg.get("ads_allow"):
        af = base / cfg["ads_allow"]
        if af.exists():
            allow = parse_adlist(af.read_text())
            print(f"[allow] {len(allow)} exceptions", file=sys.stderr)

    categories = {}
    for cat, spec in cfg["geosite_categories"].items():
        raws, seen = [], set()
        for ref in spec.get("from", []):
            for d in parsed.get(ref["src"], {}).get(ref["name"].upper(), []):
                k = domain_key(d)
                if k not in seen:
                    seen.add(k); raws.append(d)
        if spec.get("adlist"):
            for dom in adlists.get(spec["adlist"], set()):
                if dom in allow:
                    continue
                raw = enc_domain(dom)
                k = domain_key(raw)
                if k not in seen:
                    seen.add(k); raws.append(raw)
        if spec.get("extra"):  # operator-curated domains added to this category
            ef = base / spec["extra"]
            if ef.exists():
                for dom in parse_adlist(ef.read_text()):
                    raw = enc_domain(dom)
                    k = domain_key(raw)
                    if k not in seen:
                        seen.add(k); raws.append(raw)
        categories[cat.upper()] = raws
        for al in spec.get("alias", []):
            categories[al.upper()] = list(raws)
        extra = f" (+alias {spec['alias']})" if spec.get("alias") else ""
        print(f"[cat] {cat}: {len(raws)}{extra}", file=sys.stderr)

    blob = bytearray()
    for name in sorted(categories):
        blob += build_category(name, categories[name])
    Path(args.out_geosite).write_bytes(blob)
    print(f"[out] geosite: {len(blob)} B, {len(categories)} categories", file=sys.stderr)

    geoip = fetch_bytes(cfg["geoip"]["url"], args.cache)
    Path(args.out_geoip).write_bytes(geoip)
    print(f"[out] geoip: {len(geoip)} B", file=sys.stderr)


if __name__ == "__main__":
    main()
