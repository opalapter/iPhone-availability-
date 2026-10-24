#!/usr/bin/env python3
"""iPhone Duo (Unlocked) pickup availability checker for Apple Stores near Miami.

Runs from GitHub Actions. Calls Apple's pickup-availability endpoint, compares with
the previous run (state.json, kept in the Actions cache) and pushes ntfy alerts only
when a store/part combination becomes available. Failures are reported at most once
per hour so a block by Apple shows up as "check failed", never as "no stock".

Stdlib only - no pip install needed.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

# Part numbers from a third-party reseller listing (SHI), NOT verified against Apple.
PARTS = {
    "MK1E4LL/A": "256GB Star White",
    "MK1F4LL/A": "256GB Night Sky",
}
LOCATION = os.environ.get("LOCATION", "33139")      # Miami Beach ZIP
MAX_MILES = float(os.environ.get("MAX_MILES", "60"))
STATE_FILE = "state.json"
FAIL_ALERT_EVERY = 3600  # seconds
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
BUY_URL = "https://www.apple.com/shop/buy-iphone/iphone-duo"

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"available": [], "last_fail_alert": 0}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def notify(title, message, click=BUY_URL, priority="high"):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set; would have sent:", title, "|", message)
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Click": click, "Tags": "iphone"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        print("ntfy:", r.status)


def fetch():
    params = {"pl": "true", "mts.0": "regular", "location": LOCATION}
    for i, part in enumerate(PARTS):
        params[f"parts.{i}"] = part
    url = "https://www.apple.com/shop/fulfillment-messages?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": BUY_URL,
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
        status = r.status
    try:
        data = json.loads(raw)
    except ValueError:
        raise RuntimeError(f"HTTP {status}, response is not JSON: {raw[:200]!r}")
    stores = (data.get("body", {}).get("content", {})
                  .get("pickupMessage", {}).get("stores"))
    if stores is None:
        raise RuntimeError(f"Unexpected JSON shape: {raw[:300]!r}")
    return stores


def miles(store):
    try:
        return float(str(store.get("storeDistanceWithUnit", "")).split()[0])
    except (ValueError, IndexError):
        return 0.0


def main():
    state = load_state()
    try:
        stores = fetch()
    except Exception as e:  # noqa: BLE001 - any failure must be reported, not hidden
        print("CHECK FAILED:", e)
        now = time.time()
        if now - state.get("last_fail_alert", 0) >= FAIL_ALERT_EVERY:
            notify("Duo check FAILED", f"Could not read Apple stock (not 'no stock'): {e}"[:400],
                   priority="default")
            state["last_fail_alert"] = now
        save_state(state)
        sys.exit(1)

    found = []
    print(f"{len(stores)} stores returned")
    for s in stores:
        if miles(s) > MAX_MILES:
            continue
        name = f"{s.get('storeName', '?')} ({s.get('city', '?')})"
        for part, info in (s.get("partsAvailability") or {}).items():
            status = info.get("pickupDisplay")
            print(f"  {name:45} {PARTS.get(part, part):20} {status}")
            if status == "available":
                found.append(f"{part}|{name}")

    new = [k for k in found if k not in state.get("available", [])]
    for key in new:
        part, store = key.split("|", 1)
        notify(f"iPhone Duo in stock: {PARTS.get(part, part)}",
               f"Available for pickup at {store}. Reserve now on apple.com.")
    if not new:
        print("No new availability.")

    state["available"] = found
    state["last_fail_alert"] = 0  # reset so the next failure alerts immediately
    save_state(state)


if __name__ == "__main__":
    main()
