# mbta-tracker

## About the project
Controller software for a Raspberry Pi-driven RGB LED matrix display. It cycles
through a handful of small programs:

- **Clock** — current time, black background
- **Ball** — a bouncing-ball screensaver
- **Snake** — a self-playing snake, using BFS pathfinding to chase its own tail
- **Moon** — current moon phase
- **Metaballs** / **Lava Lamp** — blobby ambient animations
- **Tide** — local tide chart
- **Ticker** — a scrolling DNA/BTC/SPY price ticker
- **Paintings** — a small rotating gallery of famous public-domain artwork,
  one painting per page (see `controller/programs/gallery.py`)

Programs are paged through with the web UI's NEXT/BACK buttons, or by
clicking a page directly in its "Scroll All Pages" gallery view - there are
no physical buttons; the board is controlled entirely from the web page.

On the Pi, the controller drives the physical LED matrix **and** a web mirror
at the same time (see "Viewing and controlling it remotely" below) — the web
page shows exactly what's on the physical display, and its buttons are the
only way to page through programs.

### Installation
Clone this repository and run:
```sh
make develop
```

### Running
```sh
# Simulate mode: opens a browser tab with a virtual LED matrix and its
# NEXT/BACK controls.
make sim

# Real hardware mode: drives an actual RGB LED matrix via rpi-rgb-led-matrix,
# and also mirrors it to a web UI. Only works on a Raspberry Pi set up per
# setup.sh.
make run
```

### Development
```sh
make lint   # ruff check + pyright
make format # ruff format
make test   # pytest
```

## Project layout
- `controller/data.py` — shared pixel/font helpers and the built-in bitmap font
- `controller/programs/` — the individual display programs (`Program` protocol:
  a `start()` method and a `pixels` property)
- `controller/displays/` — the display backends: `Simulate` (web UI),
  `AdaFruit` (real hardware via `rpi-rgb-led-matrix`), and `Dual` (drives both
  at once, used on the Pi)
- `controller/timing.py` — small helpers for running a program's loop in a
  daemon thread at a fixed rate

## Deploying to a Raspberry Pi
`setup.sh` bootstraps a fresh Pi (installs `rpi-rgb-led-matrix` and clones this
repo). The controller itself starts via a systemd service, not `rc.local` (see
below).

### SSH access
```sh
ssh pi@raspberrypi.local   # on the same LAN, via mDNS
ssh pi@raspberrypi         # over Tailscale, from anywhere the laptop can reach it
```
Password is `password` — fine since the Pi is only reachable on the local
network / your private Tailscale network, never the public internet.

To avoid typing the password every time, copy your SSH key up once:
```sh
ssh-copy-id pi@raspberrypi
```

### Connecting the Pi to a new Wi-Fi network
This Pi runs the older networking stack (dhcpcd + wpa_supplicant), not
NetworkManager, so `nmtui`/`nmcli` won't work (`Error: NetworkManager is not
running`). SSH or console in, then either:

- **Easiest**: `sudo raspi-config` → *System Options* → *Wireless LAN* → enter
  the SSID and password.
- **Manual**: edit `/etc/wpa_supplicant/wpa_supplicant.conf` and add a block:
  ```
  network={
      ssid="YOUR_SSID"
      psk="YOUR_PASSWORD"
  }
  ```
  then apply it with `sudo wpa_cli -i wlan0 reconfigure` (or `sudo reboot` if
  that doesn't take).

(If a future Pi image is upgraded to Bookworm's NetworkManager-based
networking, use `sudo nmtui` or `sudo nmcli device wifi connect "SSID"
password "PASSWORD"` instead.)

### Starting on boot (systemd)
The controller runs as a systemd service, `deploy/mbta-tracker.service`,
installed on the Pi at `/etc/systemd/system/mbta-tracker.service`. It runs
`venv/bin/python -m controller` from `/home/pi/mbta-tracker`, as root (the LED
matrix driver needs raw GPIO/memory access), and restarts automatically if it
crashes.

```sh
# Status / start / stop / restart
sudo systemctl status mbta-tracker
sudo systemctl restart mbta-tracker
sudo systemctl stop mbta-tracker      # e.g. to run it manually for testing

# Disable auto-start on boot (without touching the unit file)
sudo systemctl disable mbta-tracker

# Run it manually in the foreground, e.g. after `stop`ping the service
cd /home/pi/mbta-tracker && ./venv/bin/python -m controller
```

`/etc/rc.local` no longer launches the controller (it used to, via a stale
`src.main` reference left over from before the code was reorganized into
`controller/` — that had been silently failing on every boot). It's now a
no-op that just prints the Pi's IP address.

### Logs
Since it's a proper systemd service, logs go to the journal:
```sh
sudo journalctl -u mbta-tracker -f        # follow live
sudo journalctl -u mbta-tracker -b        # this boot
sudo journalctl -u mbta-tracker --since "1 hour ago"
```

### Deploying code changes
```sh
make deploy
# or directly:
./deploy/deploy.sh [pi-host]   # defaults to $PI_HOST or "raspberrypi"
```
This rsyncs the working tree to `/home/pi/mbta-tracker` (deleting anything on
the Pi that's no longer part of the repo), installs
[`requirements-pi.txt`](#pi-python-version) into the Pi's existing virtualenv,
and restarts the systemd service.

### Pi Python version
The Pi's Raspbian Bullseye only ships Python 3.8/3.9, and the compiled
`rgbmatrix` hardware bindings are built against that system Python — so
rather than upgrading it (which would mean recompiling
`rpi-rgb-led-matrix`'s bindings), the code is kept compatible with 3.9 and
the Pi installs a separate, pinned `requirements-pi.txt` (resolved against
[piwheels](https://www.piwheels.org/), which hosts prebuilt ARM wheels) instead
of `uv.lock`, which targets the newer Python used for local dev. The Pi's venv
was created with `--system-site-packages` so it can see the system-wide
`rgbmatrix` install.

### Viewing and controlling it remotely
On the Pi (real hardware mode, i.e. whenever it's *not* run with `simulate`),
the controller uses the `Dual` display: it drives the physical LED matrix and
simultaneously serves the same frames to a web page, bound on all interfaces.
With Tailscale set up on both the Pi and your laptop, just go to:
```
raspberrypi
```
(it's served on port 80, the default for `http://`, so no `:8765` needed).
The page mirrors the physical display live, and its NEXT/BACK buttons (and
its "Scroll All Pages" gallery view, where clicking a page shows it on the
board) are the only way to page through programs — there are no physical
buttons. Since it binds `0.0.0.0`, it's also reachable from anything else on
the same LAN, not just Tailscale — there's no auth on it, matching the
LAN/Tailscale-only trust model used for SSH above.

### Privilege drop order in `Dual`
`AdaFruit`'s `RGBMatrix` drops root privileges by default once the hardware
is initialized (`rpi-rgb-led-matrix`'s own behavior - see
`--led-no-drop-privs` in `controller/displays/adafruit.py`). Anything that
still needs root - binding port 80 - has to be constructed *before*
`AdaFruit`, in `Dual.__init__`. Getting this order wrong previously caused
the web server to crash-loop with `PermissionError: [Errno 13]` trying to
bind port 80, because it was being constructed after privileges had already
been dropped.
