from __future__ import annotations

import json
import logging
import queue
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from controller.data import PixelDisplay, dimensions, validate_pixels
from controller.displays import DisplayProtocol

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8765
SCALE = 15
GALLERY_SCALE = 4
GALLERY_INTERVAL = 0.15  # seconds between gallery broadcasts

_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Display Simulator</title>
<style>
  html, body { min-height: 100%; margin: 0; background: #1a1a1a; }
  body { padding-top: 64px; box-sizing: border-box; }
  canvas { image-rendering: pixelated; display: block; background: #000; }

  #mode-bar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: 64px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    background: #111;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.5);
    z-index: 10;
    font: 14px sans-serif;
    color: #ccc;
  }

  .switch {
    position: relative;
    display: inline-block;
    width: 44px;
    height: 24px;
  }

  .switch input { opacity: 0; width: 0; height: 0; }

  .slider {
    position: absolute;
    cursor: pointer;
    inset: 0;
    background: #555;
    border-radius: 24px;
    transition: background 0.15s;
  }

  .slider::before {
    content: "";
    position: absolute;
    width: 18px;
    height: 18px;
    left: 3px;
    bottom: 3px;
    background: white;
    border-radius: 50%;
    transition: transform 0.15s;
  }

  input:checked + .slider { background: #4c8bf5; }
  input:checked + .slider::before { transform: translateX(20px); }

  #button-view {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: calc(100vh - 64px);
  }

  .frame {
    display: inline-block;
    padding: 32px;
    border-radius: 3px;
    background: #8b5a2b;
    box-shadow:
      inset 0 0 4px rgba(0, 0, 0, 0.6),
      0 3px 10px rgba(0, 0, 0, 0.6);
  }

  .mat {
    padding: 8px;
    background: #000;
  }

  #buttons {
    position: fixed;
    left: 16px;
    bottom: 16px;
    display: flex;
    gap: 12px;
  }

  .button {
    min-width: 96px;
    height: 48px;
    padding: 0 20px;
    border-radius: 24px;
    border: 2px solid #555;
    background: #222;
    color: #ddd;
    font: bold 15px sans-serif;
    letter-spacing: 0.03em;
    cursor: pointer;
  }

  .button:active {
    background: #444;
  }

  #gallery-view {
    display: none;
    padding: 16px;
    max-width: 1400px;
    margin: 0 auto;
    box-sizing: border-box;
  }

  #gallery-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 16px;
  }

  .gallery-cell {
    background: #000;
    border-radius: 4px;
    padding: 6px;
    text-align: center;
  }

  .gallery-cell canvas {
    margin: 0 auto;
    cursor: pointer;
  }

  .gallery-cell:hover canvas {
    outline: 2px solid #4c8bf5;
  }

  .gallery-label {
    color: #ccc;
    font: 11px sans-serif;
    margin-top: 4px;
  }
</style>
</head>
<body>
<div id="mode-bar">
  <span id="mode-label">Button Simulation</span>
  <label class="switch">
    <input type="checkbox" id="mode-toggle">
    <span class="slider"></span>
  </label>
  <span>Scroll All Pages</span>
</div>

<div id="button-view">
  <div class="frame">
    <div class="mat">
      <canvas id="screen" width="__CANVAS_WIDTH__" height="__CANVAS_HEIGHT__"></canvas>
    </div>
  </div>
  <div id="buttons">
    <button class="button" id="button-b">◀ BACK</button>
    <button class="button" id="button-a">NEXT ▶</button>
  </div>
</div>

<div id="gallery-view">
  <div id="gallery-grid"></div>
</div>

<script>
  const scale = __SCALE__;
  const radius = scale / 2;
  const canvas = document.getElementById("screen");
  const ctx = canvas.getContext("2d");

  function draw(pixels) {
    ctx.fillStyle = "black";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (let y = 0; y < pixels.length; y++) {
      const row = pixels[y];
      for (let x = 0; x < row.length; x++) {
        const [r, g, b] = row[x];
        ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
        ctx.beginPath();
        ctx.arc(x * scale + radius, y * scale + radius, radius, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  function connect() {
    const source = new EventSource("/stream");
    source.onmessage = (event) => draw(JSON.parse(event.data));
    source.onerror = () => {
      source.close();
      setTimeout(connect, 1000);
    };
  }
  connect();

  document.getElementById("button-a").addEventListener("click", () => {
    fetch("/button/a", { method: "POST" });
  });
  document.getElementById("button-b").addEventListener("click", () => {
    fetch("/button/b", { method: "POST" });
  });

  // --- Gallery mode: every program's frame at once, in a scrolling grid ---

  const galleryScale = __GALLERY_SCALE__;
  const galleryGrid = document.getElementById("gallery-grid");
  const galleryCells = {};

  function drawScaled(ctx2d, pixels, scale) {
    for (let y = 0; y < pixels.length; y++) {
      const row = pixels[y];
      for (let x = 0; x < row.length; x++) {
        const [r, g, b] = row[x];
        ctx2d.fillStyle = `rgb(${r}, ${g}, ${b})`;
        ctx2d.fillRect(x * scale, y * scale, scale, scale);
      }
    }
  }

  // --- Click a gallery cell to show that page on the board ---

  function selectProgram(index) {
    fetch(`/select/${index}`, { method: "POST" });
    // Switch back to the single-screen view so the selection is visible
    // immediately, both here and on the physical board.
    modeToggle.checked = false;
    applyMode();
  }

  function galleryCellFor(name, index, height, width) {
    let cell = galleryCells[name];
    if (cell) return cell;

    const wrapper = document.createElement("div");
    wrapper.className = "gallery-cell";

    const cellCanvas = document.createElement("canvas");
    cellCanvas.width = width * galleryScale;
    cellCanvas.height = height * galleryScale;
    cellCanvas.addEventListener("click", () => selectProgram(index));

    const label = document.createElement("div");
    label.className = "gallery-label";
    label.textContent = name;

    wrapper.appendChild(cellCanvas);
    wrapper.appendChild(label);
    galleryGrid.appendChild(wrapper);

    cell = { ctx: cellCanvas.getContext("2d") };
    galleryCells[name] = cell;
    return cell;
  }

  function connectGallery() {
    const source = new EventSource("/gallery-stream");
    source.onmessage = (event) => {
      const frames = JSON.parse(event.data);
      frames.forEach((frame, index) => {
        const height = frame.pixels.length;
        const width = height > 0 ? frame.pixels[0].length : 0;
        const cell = galleryCellFor(frame.name, index, height, width);
        drawScaled(cell.ctx, frame.pixels, galleryScale);
      });
    };
    source.onerror = () => {
      source.close();
      setTimeout(connectGallery, 1000);
    };
  }
  connectGallery();

  // --- Mode toggle ---

  const modeToggle = document.getElementById("mode-toggle");
  const modeLabel = document.getElementById("mode-label");
  const buttonView = document.getElementById("button-view");
  const galleryView = document.getElementById("gallery-view");

  function applyMode() {
    const scrollMode = modeToggle.checked;
    buttonView.style.display = scrollMode ? "none" : "flex";
    galleryView.style.display = scrollMode ? "block" : "none";
    modeLabel.style.opacity = scrollMode ? "0.5" : "1";
  }
  modeToggle.addEventListener("change", applyMode);
  applyMode();
</script>
</body>
</html>
"""


class Simulate(DisplayProtocol):
    def __init__(self, port: int = PORT, open_browser: bool = True, host: str = HOST):
        self._clients: list[queue.Queue] = []
        self._clients_lock = threading.Lock()
        self._latest: str | None = None

        self._gallery_clients: list[queue.Queue] = []
        self._gallery_clients_lock = threading.Lock()
        self._latest_gallery: str | None = None
        self._last_gallery_send = 0.0

        self._button_lock = threading.Lock()
        self.button_a_index = 0
        self.button_b_index = 0
        self._pending_select: int | None = None

        page = (
            _PAGE.replace("__CANVAS_WIDTH__", str(dimensions.width * SCALE + SCALE))
            .replace("__CANVAS_HEIGHT__", str(dimensions.height * SCALE + SCALE))
            .replace("__SCALE__", str(SCALE))
            .replace("__GALLERY_SCALE__", str(GALLERY_SCALE))
        )
        self._page = page.encode("utf-8")

        self._server = ThreadingHTTPServer((host, port), self._make_handler())
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        display_host = host if host != "0.0.0.0" else "<this device's IP>"
        url = f"http://{display_host}:{self.port}"
        logger.info(f"Simulation display running at {url}")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                logger.info("Could not open a browser automatically")

    def close(self) -> None:
        """Stop serving and release the port."""
        self._server.shutdown()
        self._server.server_close()

    def take_pending_select(self) -> int | None:
        """Pop and return the program index most recently clicked in the
        gallery view, if any - consumed once so it's only applied once."""
        with self._button_lock:
            value = self._pending_select
            self._pending_select = None
        return value

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                logger.debug(format % args)

            def _serve_sse(self, clients: list, clients_lock, latest_getter):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                client_queue: queue.Queue = queue.Queue()
                with clients_lock:
                    clients.append(client_queue)
                    latest = latest_getter()
                    if latest is not None:
                        client_queue.put(latest)

                try:
                    while True:
                        payload = client_queue.get()
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with clients_lock:
                        if client_queue in clients:
                            clients.remove(client_queue)

            def do_GET(self):
                if self.path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(outer._page)))
                    self.end_headers()
                    self.wfile.write(outer._page)
                elif self.path == "/stream":
                    self._serve_sse(
                        outer._clients,
                        outer._clients_lock,
                        lambda: outer._latest,
                    )
                elif self.path == "/gallery-stream":
                    self._serve_sse(
                        outer._gallery_clients,
                        outer._gallery_clients_lock,
                        lambda: outer._latest_gallery,
                    )
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path == "/button/a":
                    with outer._button_lock:
                        outer.button_a_index += 1
                    logger.info(f"Button A pressed {outer.button_a_index} times")
                    self.send_response(204)
                    self.end_headers()
                elif self.path == "/button/b":
                    with outer._button_lock:
                        outer.button_b_index += 1
                    logger.info(f"Button B pressed {outer.button_b_index} times")
                    self.send_response(204)
                    self.end_headers()
                elif self.path.startswith("/select/"):
                    try:
                        index = int(self.path.rsplit("/", 1)[-1])
                    except ValueError:
                        self.send_response(400)
                        self.end_headers()
                        return
                    with outer._button_lock:
                        outer._pending_select = index
                    logger.info(f"Selected program index {index} from gallery")
                    self.send_response(204)
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

        return Handler

    @validate_pixels
    def display_matrix(self, pixels: PixelDisplay) -> None:
        """Given an led matrix, broadcast it to any connected browser tabs."""
        payload = json.dumps(pixels.tolist())
        self._latest = payload
        with self._clients_lock:
            clients = list(self._clients)
        for client_queue in clients:
            client_queue.put(payload)

    def display_gallery(self, frames: list[tuple[str, PixelDisplay]]) -> None:
        """Broadcast every program's current frame at once, for the scrolling
        gallery view. Throttled to `GALLERY_INTERVAL`, since this fans out
        N frames per broadcast instead of just one."""
        now = time.monotonic()
        if now - self._last_gallery_send < GALLERY_INTERVAL:
            return
        self._last_gallery_send = now

        payload = json.dumps(
            [{"name": name, "pixels": pixels.tolist()} for name, pixels in frames]
        )
        self._latest_gallery = payload
        with self._gallery_clients_lock:
            clients = list(self._gallery_clients)
        for client_queue in clients:
            client_queue.put(payload)
