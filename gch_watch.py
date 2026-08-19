#!/usr/bin/env python3
"""
Watch Disneyland hotel room availability and alert on cancellations.

Defaults to Disney's Grand Californian Hotel & Spa, Oct 9-11 2026 (2 nights).

Uses the same endpoint the disneyland.disney.go.com "Rooms & Rates" page calls:
    POST /dlr-resort-details-api/api/v1/availability-and-prices/<resort-slug>
      200 -> rooms are bookable
      404 RESOURCE_NOT_FOUND (220141) -> nothing available for that stay

Stdlib only. Python 3.8+.
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = "https://disneyland.disney.go.com"
API = BASE + "/dlr-resort-details-api/api/v1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

SOLD_OUT_CODE = 220141  # "No Available Rooms were found."

NTFY_HOST = "https://ntfy.sh"


def load_config():
    """Load config.env (KEY=value lines) next to this script into os.environ.

    Real environment variables always win, so GitHub Actions secrets override
    the local file. Keeps personal details out of committed code.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.env")
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def env(key, fallback):
    v = os.environ.get(key, "")
    return v.strip() if v.strip() else fallback


def headers(slug):
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "en-us",
        "X-Disney-Internal-Core-Api-Product-Instance": "true",
        "User-Agent": UA,
        "Origin": BASE,
        "Referer": "%s/hotels/%s/rates-rooms/" % (BASE, slug),
    }


def _request(url, slug, payload=None, timeout=30):
    """Return (status, parsed_json). Raises on transport failure."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers(slug),
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body[:500]}


def room_names(slug):
    """id -> human room name. Best effort; empty dict on failure."""
    url = "%s/categories-and-room-types/%s/?storeId=dlr&accessible=false" % (API, slug)
    try:
        status, body = _request(url, slug)
        if status != 200:
            return {}
        return {k: v.get("name", k) for k, v in body.get("roomLookup", {}).items()}
    except Exception:
        return {}


def check(slug, check_in, check_out, adults, children, child_ages, accessible):
    """Return (state, detail).

    state is one of: 'available', 'sold_out', 'error'
    """
    url = "%s/availability-and-prices/%s?storeId=dlr&accessible=%s" % (
        API, slug, "true" if accessible else "false")
    payload = {
        "checkInDate": check_in,
        "checkOutDate": check_out,
        "partyMix": {
            "adultCount": adults,
            "childCount": children,
            "nonAdultAges": [{"age": a} for a in child_ages],
        },
        "accessible": accessible,
        "affiliations": ["STD_GST"],
    }
    try:
        status, body = _request(url, slug, payload)
    except Exception as e:
        return "error", "request failed: %s" % e

    if status == 200 and body.get("roomPriceLookup"):
        return "available", body
    if status == 404:
        codes = [e.get("systemErrorCode") for e in body.get("errors", [])]
        if SOLD_OUT_CODE in codes:
            return "sold_out", None
    return "error", "HTTP %s %s" % (status, json.dumps(body)[:300])


def summarize(body, names):
    """Build a list of (room name, nightly, total) sorted cheapest first."""
    rooms = []
    for rid, info in body.get("roomPriceLookup", {}).items():
        try:
            total = float(info["totalPrice"]["total"])
            nightly = float(info["displayPrice"]["basePrice"]["subtotal"])
        except (KeyError, TypeError, ValueError):
            continue
        rooms.append((names.get(rid, "Room %s" % rid), nightly, total))
    rooms.sort(key=lambda r: r[2])
    return rooms


def notify(title, message, sound=True, repeat=1):
    """macOS notification + audible alert. Silently no-ops elsewhere."""
    if sys.platform != "darwin":
        return
    safe_t = title.replace('"', "'")
    safe_m = message.replace('"', "'")
    for i in range(repeat):
        try:
            subprocess.run(
                ["osascript", "-e",
                 'display notification "%s" with title "%s"%s'
                 % (safe_m, safe_t, ' sound name "Glass"' if sound else "")],
                check=False, capture_output=True)
            if sound:
                subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"],
                               check=False, capture_output=True)
        except Exception:
            pass
        if i + 1 < repeat:
            time.sleep(1.5)


def push(topic, title, message, click=None, priority="urgent"):
    """Send a phone push via ntfy.sh. Returns True on success."""
    if not topic:
        return False
    hdrs = {
        "Title": title.encode("ascii", "ignore").decode(),
        "Priority": priority,
        "Tags": "hotel,rotating_light",
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click:
        hdrs["Click"] = click
    req = urllib.request.Request("%s/%s" % (NTFY_HOST, topic),
                                 data=message.encode("utf-8"),
                                 headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def pushcut(url, title, message, click=None):
    """Send via a Pushcut webhook URL. The URL embeds a secret - never log it."""
    if not url:
        return False
    body = {"title": title, "text": message}
    if click:
        body["actions"] = [{"name": "Book now", "url": click}]
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def say(text):
    if sys.platform == "darwin":
        subprocess.run(["say", text], check=False, capture_output=True)


def days_until(date_str):
    """Whole days from today to date_str (YYYY-MM-DD). Negative if past."""
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return 10 ** 6
    return (target - datetime.now().date()).days


def log(line, path):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = "[%s] %s" % (stamp, line)
    print(msg, flush=True)
    if path:
        try:
            with open(path, "a") as f:
                f.write(msg + "\n")
        except OSError:
            pass


def main():
    load_config()
    p = argparse.ArgumentParser(
        description="Alert when a Disneyland hotel opens up for your dates.")
    p.add_argument("--check-in", default=env("CHECK_IN", ""))
    p.add_argument("--check-out", default=env("CHECK_OUT", ""))
    p.add_argument("--hotel", default=env("HOTEL", "grand-californian-hotel"),
                   help="URL slug, e.g. disneyland-hotel, pixar-place-hotel")
    p.add_argument("--adults", type=int, default=int(env("ADULTS", "2")))
    p.add_argument("--children", type=int, default=int(env("CHILDREN", "0")))
    p.add_argument("--child-ages", default=env("CHILD_AGES", ""),
                   help="comma-separated ages")
    p.add_argument("--accessible", action="store_true")
    p.add_argument("--interval", type=int, default=300,
                   help="seconds between checks (default 300)")
    p.add_argument("--jitter", type=int, default=45,
                   help="random +/- seconds added to interval")
    p.add_argument("--tight-interval", type=int, default=90,
                   help="seconds between checks once inside --tighten-within days")
    p.add_argument("--tighten-within", type=int, default=10,
                   help="days before check-in to switch to --tight-interval")
    p.add_argument("--once", action="store_true", help="check once and exit")
    p.add_argument("--remind-every", type=int, default=1800,
                   help="re-alert every N seconds while still available")
    p.add_argument("--no-open", action="store_true",
                   help="do not auto-open the booking page on a hit")
    p.add_argument("--no-voice", action="store_true")
    p.add_argument("--ntfy-topic", default=os.environ.get("NTFY_TOPIC", ""),
                   help="ntfy.sh topic for phone push (or set NTFY_TOPIC)")
    p.add_argument("--pushcut-url", default=os.environ.get("PUSHCUT_URL", ""),
                   help="Pushcut webhook URL (or set PUSHCUT_URL). Contains a "
                        "secret, so it is never printed.")
    p.add_argument("--log", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "watch.log"))
    args = p.parse_args()

    if not args.check_in or not args.check_out:
        p.error("check-in/check-out required: set CHECK_IN and CHECK_OUT in "
                "config.env (or as env vars), or pass --check-in/--check-out")
    ages = [int(a) for a in args.child_ages.split(",") if a.strip()]
    if len(ages) != args.children:
        p.error("--children=%d but %d child age(s) given"
                % (args.children, len(ages)))
    stay = "%s -> %s" % (args.check_in, args.check_out)
    booking_url = "%s/hotels/%s/rates-rooms/" % (BASE, args.hotel)

    names = room_names(args.hotel)
    left = days_until(args.check_in)
    log("watching %s for %s (%da/%dc); %d days out, every ~%ds "
        "(tightens to ~%ds inside %d days)"
        % (args.hotel, stay, args.adults, args.children, left,
           args.tight_interval if left <= args.tighten_within else args.interval,
           args.tight_interval, args.tighten_within), args.log)
    log("alerts: ntfy=%s pushcut=%s"
        % (args.ntfy_topic or "off",
           "configured" if args.pushcut_url else "off"), args.log)

    last_state = None
    last_alert = 0.0
    consecutive_errors = 0

    while True:
        state, detail = check(args.hotel, args.check_in, args.check_out,
                              args.adults, args.children, ages, args.accessible)

        if state == "available":
            consecutive_errors = 0
            rooms = summarize(detail, names)
            headline = "%d room type%s open for %s" % (
                len(rooms), "" if len(rooms) == 1 else "s", stay)
            log("*** AVAILABLE *** " + headline, args.log)
            for name, nightly, total in rooms:
                log("      %-34s $%s/night   $%s total w/ tax"
                    % (name, format(nightly, ",.0f"), format(total, ",.2f")), args.log)

            now = time.time()
            if last_state != "available" or now - last_alert >= args.remind_every:
                cheapest = rooms[0] if rooms else None
                body = ("%s from $%s/night. Book now."
                        % (rooms[0][0], format(cheapest[1], ",.0f"))) if cheapest else headline
                notify("Grand Californian is OPEN", body, repeat=3)
                lines = ["%s  $%s/night  ($%s total)"
                         % (n, format(ni, ",.0f"), format(t, ",.2f"))
                         for n, ni, t in rooms[:6]]
                ok = push(args.ntfy_topic,
                          "GCH OPEN %s" % stay,
                          "%s\n\n%s\n\nTap to book." % (headline, "\n".join(lines)),
                          click=booking_url)
                if args.ntfy_topic:
                    log("ntfy push %s" % ("sent" if ok else "FAILED"), args.log)
                if args.pushcut_url:
                    pc = pushcut(args.pushcut_url,
                                 "GCH OPEN %s" % stay,
                                 "%s\n\n%s" % (headline, "\n".join(lines)),
                                 click=booking_url)
                    log("pushcut %s" % ("sent" if pc else "FAILED"), args.log)
                if not args.no_voice:
                    say("Grand Californian availability found. Book now.")
                if (not args.no_open and last_state != "available"
                        and sys.platform == "darwin"):
                    try:
                        subprocess.run(["open", booking_url], check=False)
                    except OSError:
                        pass
                last_alert = now

        elif state == "sold_out":
            consecutive_errors = 0
            if last_state != "sold_out":
                log("sold out for %s (will keep checking)" % stay, args.log)
            else:
                log("still sold out", args.log)

        else:
            consecutive_errors += 1
            log("ERROR (%d in a row): %s" % (consecutive_errors, detail), args.log)
            if consecutive_errors == 5:
                notify("GCH watcher problem",
                       "5 failed checks in a row - the API may have changed.")

        last_state = state
        if args.once:
            return 0 if state == "available" else 1

        left = days_until(args.check_in)
        base = args.tight_interval if left <= args.tighten_within else args.interval
        jitter = min(args.jitter, max(1, base // 4))
        wait = base + random.randint(-jitter, jitter)
        # back off on sustained errors so we don't hammer a broken endpoint
        if consecutive_errors:
            wait = min(wait * (2 ** min(consecutive_errors, 4)), 3600)
        time.sleep(max(30, wait))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped.")
