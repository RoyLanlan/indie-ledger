#!/usr/bin/env python3
"""
Liveness classifier for the indie product ledger.

Rules derived from measured failures (see indie-product-liveness-probe skill):
  dead     = NXDOMAIN confirmed twice (~1s apart) OR (DNS resolves, no listener on 80/443)
  alive    = any HTTP < 400 on port 80 or 443
  blocked  = HTTP 403/405/429  (site exists but refuses bots -> NEVER dead)
  degraded = DNS resolves, port open, but request fails (TLS/timeout)
  unknown  = inconclusive

TLS errors are NEVER death. A self-signed or expired cert means the site is alive
and serving. This is the single most important rule in the module.
"""
import socket, ssl, subprocess, time, urllib.request, urllib.error

UA = {'User-Agent': 'Mozilla/5.0 (compatible; indie-ledger/0.1; +research)'}
BLOCK_CODES = {403, 405, 429, 999}


def http_code(url, timeout=15, method="HEAD"):
    """Return (code:int|None, error:str|None)."""
    try:
        req = urllib.request.Request(url, method=method, headers=UA)
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except urllib.error.URLError as e:
        return None, str(e.reason)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def dns_state(host):
    """'RESOLVES' | 'NO_A' | 'FAIL'."""
    try:
        r = subprocess.run(["dig", "+short", host], capture_output=True, text=True, timeout=12)
        out = (r.stdout or "").strip()
        return "RESOLVES" if out else "NO_A"
    except Exception:
        pass
    try:
        socket.getaddrinfo(host, None)
        return "RESOLVES"
    except socket.gaierror as e:
        return "NO_A" if e.errno in (-2, -5, -3) else "FAIL"
    except Exception:
        return "FAIL"


def port_open(host, ports=(443, 80), timeout=8):
    for p in ports:
        try:
            with socket.create_connection((host, p), timeout=timeout):
                return True
        except Exception:
            continue
    return False


def classify(domain, timeout=15, retries=2):
    """
    Classify one domain. Returns a dict with verdict + evidence.
    Evidence is required: a verdict without evidence must never be published.
    """
    ev = {"domain": domain}

    dns = dns_state(domain)
    ev["dns"] = dns
    if dns == "NO_A":
        # confirm — DNS can be flaky
        time.sleep(1.0)
        dns2 = dns_state(domain)
        ev["dns2"] = dns2
        if dns2 == "NO_A":
            ev["verdict"] = "dead"
            ev["reason"] = "NXDOMAIN confirmed twice"
            return ev
        dns = dns2

    codes, errs = {}, {}
    for scheme in ("https", "http"):
        for attempt in range(retries):
            c, e = http_code(f"{scheme}://{domain}/", timeout=timeout)
            codes[scheme], errs[scheme] = c, e
            if c is not None and c < 400:
                break
            time.sleep(0.7)
    ev["http"] = codes
    ev["errors"] = {k: v for k, v in errs.items() if v}

    ok = lambda c: isinstance(c, int) and c < 400
    if ok(codes.get("https")) or ok(codes.get("http")):
        ev["verdict"] = "alive"
        ev["reason"] = f"HTTP {codes.get('https') if ok(codes.get('https')) else codes.get('http')}"
        return ev

    # blocked: site answers but refuses us
    for c in codes.values():
        if isinstance(c, int) and c in BLOCK_CODES:
            ev["verdict"] = "blocked"
            ev["reason"] = f"HTTP {c} (site up, refuses bots)"
            return ev

    # DNS resolved but nothing works: is the host listening at all?
    ev["port_open"] = port_open(domain, timeout=8)
    if ev["port_open"]:
        ev["verdict"] = "degraded"
        ev["reason"] = "host reachable, request failed (TLS/timeout)"
    else:
        ev["verdict"] = "dead"
        ev["reason"] = "DNS resolves but no listener on 80/443"
    return ev


def classify_batch(domains, delay=1.0, progress=None):
    out = []
    for i, d in enumerate(domains):
        if not d:
            continue
        out.append(classify(d))
        if progress and (i + 1) % progress == 0:
            print(f"   ...{i+1}/{len(domains)}", flush=True)
        time.sleep(delay)
    return out


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("usage: liveness.py domain [domain ...]")
        sys.exit(1)
    for d in sys.argv[1:]:
        r = classify(d)
        print(f"{r['domain']:<34} {r['verdict']:<9} {r['reason']}")
