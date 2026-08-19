# Grand Californian cancellation watcher

Polls Disneyland's own room-availability API and alerts you the moment a room
opens up at a Disneyland Resort hotel for the stay you configure.

Your dates, party size, and phone-alert topic live in `config.env`, which is
gitignored and never committed. Copy `config.env.example` to `config.env` and
fill it in. A bare `python3 gch_watch.py` then watches exactly that stay.

## Run it

```bash
python3 "gch_watch.py"
```

It checks about every 5 minutes with random jitter, then automatically speeds up
to about every 90 seconds once you're within 10 days of check-in — that's when
free-cancellation deadlines pass, cancellations spike, and rooms get taken fastest.

When something opens up it will:
- pop a macOS notification (3 times) and play a sound
- speak "Grand Californian availability found"
- open the booking page in your browser
- print every available room type with nightly and total-with-tax pricing

While it's sold out it just logs a quiet line. Everything is appended to `watch.log`.

## Options

```
--check-in / --check-out   dates as YYYY-MM-DD (required; usually from config.env)
--hotel                    slug: grand-californian-hotel, disneyland-hotel, pixar-place-hotel
--adults / --children      party size
--child-ages 7,4           ages at time of stay; count must match --children
--interval 300             seconds between checks (far out)
--tight-interval 90        seconds between checks near the trip
--tighten-within 10        days before check-in to switch to the tight rate
--once                     single check, then exit (exit 0 = available)
--no-open / --no-voice     quieter alerts
```

Check a different stay without stopping the main watcher:

```bash
python3 gch_watch.py --once --check-in 2027-03-14 --check-out 2027-03-16
```

## Background service (installed)

This is already installed and running as a LaunchAgent. It started the moment it
was loaded — no reboot needed — and will start again automatically at every login,
restarting itself if it ever crashes.

Check it's alive:

```bash
launchctl list | grep gchwatch
```

Watch it work:

```bash
tail -f launchd.out.log
```

Stop it:

```bash
launchctl unload ~/Library/LaunchAgents/com.<you>.gchwatch.plist
```

## How it works

`POST /dlr-resort-details-api/api/v1/availability-and-prices/grand-californian-hotel?storeId=dlr`

with the check-in/check-out dates and party mix. This is the exact call the
"Rooms & Rates" page makes. Two outcomes that matter:

- **HTTP 200** with a `roomPriceLookup` → rooms are bookable
- **HTTP 404** `systemErrorCode: 220141` ("No Available Rooms were found.") → sold out

Room ID → name comes from `/categories-and-room-types/`, fetched once at startup.

Anything else is treated as an error, logged, and retried with exponential
backoff (capped at 1 hour). After 5 consecutive errors you get a notification —
that's the signal Disney changed the API and this needs a look.

## Caveats

- The watcher tells you a room exists; it does **not** hold or book it. Grand
  Californian cancellations get taken fast, so be ready to book by hand.
- A stay outside Disney's booking window returns the same 404 as a sold-out
  stay. Oct 2026 is inside the window, so that ambiguity doesn't affect you.
- Prices shown are room-only, tax included in the "total" column.

## Phone alerts

Set `NTFY_TOPIC` in `config.env` to a long random string, install the **ntfy**
app, and subscribe to that same topic. Alerts arrive as urgent push with the
room list and a tap-to-book link. See `DEPLOY.md` to run it 24/7 in the cloud.
