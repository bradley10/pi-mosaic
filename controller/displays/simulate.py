from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Tuple

import numpy as np

from controller.data import PixelDisplay, dimensions, validate_pixels
from controller.displays import DisplayProtocol
from controller.settings import settings
from controller.timing import spawn_daemon

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8765
SCALE = 15
GALLERY_SCALE = 4
# Fallbacks for the `display.web_fps` / `display.gallery_fps` settings, which
# cap how often the web mirror is re-encoded and pushed. The physical matrix
# still gets every frame the main loop produces; these only limit the browser
# copy, which nobody can see updating faster than this anyway.
GALLERY_INTERVAL = 0.15  # seconds between gallery broadcasts
FRAME_INTERVAL = 1 / 30
# A browser that can't keep up should skip frames, not accumulate a backlog
# that grows until the process runs out of memory.
CLIENT_QUEUE_DEPTH = 1
# Cap on a request body. The server listens on 0.0.0.0:80 on the Pi, so it
# shouldn't buffer whatever anyone on the network decides to send.
MAX_BODY_BYTES = 64 * 1024

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#18181b"/>'
    '<rect x="5" y="5" width="9" height="9" rx="2" fill="#ef4444"/>'
    '<rect x="18" y="5" width="9" height="9" rx="2" fill="#10b981"/>'
    '<rect x="5" y="18" width="9" height="9" rx="2" fill="#3b82f6"/>'
    '<rect x="18" y="18" width="9" height="9" rx="2" fill="#f59e0b"/>'
    "</svg>"
)
_FAVICON_BYTES = _FAVICON_SVG.encode("utf-8")

# Raw, so the JavaScript below can carry regex escapes (\s, \d, \[) without
# Python trying to interpret them.
_PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Pi Mosaic</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="alternate icon" href="/favicon.ico">
<style>
  html, body { min-height: 100%; margin: 0; background: #1a1a1a; }
  body {
    padding-top: calc(64px + env(safe-area-inset-top, 0px));
    box-sizing: border-box;
    -webkit-text-size-adjust: 100%;
  }
  /* The board canvas is 975px of backing store; on a phone it has to be
     allowed to shrink. `height: auto` keeps the 2:1 aspect, and `pixelated`
     means scaling down still looks like an LED matrix rather than a blur. */
  canvas {
    image-rendering: pixelated;
    display: block;
    background: #000;
    max-width: 100%;
    height: auto;
  }

  #mode-bar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: calc(64px + env(safe-area-inset-top, 0px));
    padding-top: env(safe-area-inset-top, 0px);
    box-sizing: border-box;
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

  #modes {
    display: flex;
    gap: 4px;
    padding: 4px;
    background: #1e1e1e;
    border-radius: 999px;
  }

  .mode {
    padding: 7px 18px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: #bbb;
    font: 600 13px sans-serif;
    cursor: pointer;
  }

  .mode:hover { color: #fff; }
  .mode[aria-selected="true"] { background: #4c8bf5; color: #fff; }

  #button-view {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: calc(100vh - 64px);
    padding: 0 12px;
    box-sizing: border-box;
  }

  .frame {
    display: inline-block;
    max-width: 100%;
    box-sizing: border-box;
    padding: 32px;
    border-radius: 3px;
    background: #8b5a2b;
    box-shadow:
      inset 0 0 4px rgba(0, 0, 0, 0.6),
      0 3px 10px rgba(0, 0, 0, 0.6);
  }

  .mat {
    max-width: 100%;
    box-sizing: border-box;
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
    max-width: 100%;
    height: auto;
  }

  .gallery-cell:hover canvas {
    outline: 2px solid #4c8bf5;
  }

  .gallery-label {
    color: #ccc;
    font: 11px sans-serif;
    margin-top: 4px;
  }

  /* --- Settings --- */

  #settings-view {
    display: none;
    max-width: 880px;
    margin: 0 auto;
    padding: 24px 16px 64px;
    box-sizing: border-box;
    color: #ddd;
    font: 14px sans-serif;
  }

  #settings-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 4px;
  }

  #settings-header h1 { font-size: 19px; margin: 0; }

  #settings-actions { display: flex; gap: 8px; }

  #settings-status {
    color: #8a8a8a;
    font-size: 12px;
    min-height: 16px;
  }

  #settings-actions button {
    padding: 7px 14px;
    border: 1px solid #555;
    border-radius: 8px;
    background: #222;
    color: #ddd;
    font: 600 13px sans-serif;
    cursor: pointer;
  }

  #settings-actions button:hover { border-color: #4c8bf5; color: #fff; }

  #settings-json {
    display: none;
    width: 100%;
    height: 180px;
    margin-top: 12px;
    box-sizing: border-box;
    background: #1a1a1a;
    color: #ddd;
    border: 1px solid #555;
    border-radius: 8px;
    padding: 10px;
    font: 12px ui-monospace, Menlo, Consolas, monospace;
    resize: vertical;
  }

  .section {
    background: #232323;
    border-radius: 10px;
    padding: 2px 14px 8px;
    margin-top: 10px;
  }

  .section h2 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #8fb4f7;
    margin: 10px 0 2px;
  }

  .field {
    display: grid;
    grid-template-columns: minmax(120px, 1fr) minmax(0, 2fr) 64px;
    align-items: center;
    gap: 12px;
    padding: 5px 0;
    border-top: 1px solid #2c2c2c;
  }

  .field:first-of-type { border-top: 0; }

  .field-label { display: flex; flex-direction: column; }
  .field-label label { line-height: 1.25; }
  .field-label .help { color: #7d7d7d; font-size: 11px; line-height: 1.3; }

  .field input[type="range"] { width: 100%; accent-color: #4c8bf5; }
  .field input[type="color"] {
    width: 54px; height: 30px; padding: 0;
    background: none; border: 1px solid #555; border-radius: 6px; cursor: pointer;
  }
  .field select, .field input[type="number"] {
    background: #1a1a1a;
    color: #ddd;
    border: 1px solid #555;
    border-radius: 6px;
    padding: 6px 8px;
    font: 13px sans-serif;
    width: 100%;
    box-sizing: border-box;
  }
  .field .readout {
    color: #aaa;
    font: 12px ui-monospace, Menlo, Consolas, monospace;
    text-align: right;
  }
  .field input[type="checkbox"] { width: 18px; height: 18px; accent-color: #4c8bf5; }

  /* Phones. Until the viewport meta above was added this never applied:
     a phone reported ~980px wide regardless of the hardware. */
  @media (max-width: 620px) {
    /* Label above the control, value under it, rather than three columns
       squeezed into 390px. */
    .field { grid-template-columns: 1fr; gap: 6px; }
    .field .readout { text-align: left; }

    #mode-bar { height: calc(52px + env(safe-area-inset-top, 0px)); }
    body { padding-top: calc(52px + env(safe-area-inset-top, 0px)); }
    .mode { padding: 7px 12px; font-size: 12px; }

    /* Centring in the viewport fights the fixed button bar on a short
       screen; sit the board at the top and reserve room underneath. */
    #button-view {
      min-height: 0;
      justify-content: flex-start;
      padding: 12px 10px calc(84px + env(safe-area-inset-bottom, 0px));
    }
    .frame { padding: 10px; width: 100%; }

    /* Full-width thumb targets, clear of the home indicator. */
    #buttons {
      left: 10px;
      right: 10px;
      bottom: calc(10px + env(safe-area-inset-bottom, 0px));
      gap: 10px;
    }
    .button { flex: 1; min-width: 0; }

    #gallery-view { padding: 10px; }
    #gallery-grid {
      grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
      gap: 10px;
    }

    #settings-view { padding: 16px 10px 48px; }
    #settings-header h1 { font-size: 17px; }
    #settings-actions { width: 100%; }
    #settings-actions button { flex: 1; padding: 10px 12px; }
    .section { padding: 2px 10px 8px; }
    /* 16px keeps iOS from zooming the page when a field takes focus. */
    .field select,
    .field input[type="number"] { font-size: 16px; }
  }
</style>
</head>
<body>
<div id="mode-bar">
  <div id="modes" role="tablist">
    <button class="mode" role="tab" data-view="board"
            aria-selected="true">Board</button>
    <button class="mode" role="tab" data-view="gallery"
            aria-selected="false">All pages</button>
    <button class="mode" role="tab" data-view="settings"
            aria-selected="false">Settings</button>
  </div>
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

<div id="settings-view">
  <div id="settings-header">
    <h1>Settings</h1>
    <div id="settings-actions">
      <button id="copy-json">Copy as JSON</button>
      <button id="reset">Reset all</button>
    </div>
  </div>
  <div id="settings-status"></div>
  <textarea id="settings-json" readonly></textarea>
  <div id="settings-sections"></div>
</div>

<script>
  const scale = __SCALE__;
  const radius = scale / 2;
  const canvas = document.getElementById("screen");
  const ctx = canvas.getContext("2d");

  // Frames arrive as base64 of raw RGB bytes rather than a nested JSON
  // array. Building that nested array cost the Pi ~85x more CPU per frame
  // than this does, on the same thread that drives the physical matrix, and
  // it made the payload four times bigger for the browser to parse too.
  function decodeFrame(encoded) {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  function draw(rgb, width, height) {
    ctx.fillStyle = "black";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    let i = 0;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const r = rgb[i++], g = rgb[i++], b = rgb[i++];
        ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
        ctx.beginPath();
        ctx.arc(x * scale + radius, y * scale + radius, radius, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  function connect() {
    const source = new EventSource("/stream");
    source.onmessage = (event) => {
      const frame = JSON.parse(event.data);
      draw(decodeFrame(frame.pixels), frame.width, frame.height);
    };
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

  function drawScaled(ctx2d, rgb, width, height, scale) {
    let i = 0;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const r = rgb[i++], g = rgb[i++], b = rgb[i++];
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
    showView("board");
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
        const cell = galleryCellFor(frame.name, index, frame.height, frame.width);
        drawScaled(
          cell.ctx, decodeFrame(frame.pixels), frame.width, frame.height, galleryScale
        );
      });
    };
    source.onerror = () => {
      source.close();
      setTimeout(connectGallery, 1000);
    };
  }
  connectGallery();

  // --- View switching ---

  const VIEWS = {
    board: [document.getElementById("button-view"), "flex"],
    gallery: [document.getElementById("gallery-view"), "block"],
    settings: [document.getElementById("settings-view"), "block"],
  };

  function showView(name) {
    for (const [key, [element, display]] of Object.entries(VIEWS)) {
      element.style.display = key === name ? display : "none";
    }
    document.querySelectorAll(".mode").forEach((button) => {
      button.setAttribute("aria-selected", button.dataset.view === name);
    });
  }

  document.querySelectorAll(".mode").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  showView("board");

  // --- Settings ---

  const sectionsHost = document.getElementById("settings-sections");
  const statusLine = document.getElementById("settings-status");
  const controls = {};   // key -> {set(value), readout}
  let schema = null;
  let statusTimer = null;

  function setStatus(text) {
    statusLine.textContent = text;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { statusLine.textContent = ""; }, 1800);
  }

  function toHex([r, g, b]) {
    return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
  }

  function fromHex(hex) {
    return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  }

  // Changes are sent as the control moves, so the board reacts while you
  // drag. Coalesced into one request per frame or so: the server debounces
  // the file write, but there's no point flooding it either.
  let queued = {};
  let flushTimer = null;

  function send(key, value) {
    queued[key] = value;
    if (flushTimer !== null) return;
    flushTimer = setTimeout(() => {
      const body = queued;
      queued = {};
      flushTimer = null;
      fetch("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then((response) => response.json())
        .then((result) => {
          if (result.rejected && result.rejected.length) {
            setStatus("Ignored: " + result.rejected.join(", "));
          } else {
            setStatus("Saved");
          }
        })
        .catch(() => setStatus("Could not reach the board"));
    }, 80);
  }

  function formatNumber(setting, value) {
    if (setting.kind === "int") return String(value);
    const decimals = setting.step && setting.step < 0.01 ? 6 : 2;
    // Round for display, then back through Number to drop trailing zeros.
    return String(Number(Number(value).toFixed(decimals)));
  }

  function buildField(setting) {
    const field = document.createElement("div");
    field.className = "field";

    const labelBox = document.createElement("div");
    labelBox.className = "field-label";
    const label = document.createElement("label");
    label.textContent = setting.label;
    labelBox.appendChild(label);
    if (setting.help) {
      const help = document.createElement("span");
      help.className = "help";
      help.textContent = setting.help;
      labelBox.appendChild(help);
    }
    field.appendChild(labelBox);

    const readout = document.createElement("div");
    readout.className = "readout";

    let input;
    if (setting.kind === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.addEventListener("change", () => send(setting.key, input.checked));
      controls[setting.key] = {
        input,
        set: (value) => { input.checked = Boolean(value); },
      };
    } else if (setting.kind === "choice") {
      input = document.createElement("select");
      setting.choices.forEach((choice) => {
        const option = document.createElement("option");
        option.value = choice;
        option.textContent = choice;
        input.appendChild(option);
      });
      input.addEventListener("change", () => send(setting.key, input.value));
      controls[setting.key] = { input, set: (value) => { input.value = value; } };
    } else if (setting.kind === "color") {
      input = document.createElement("input");
      input.type = "color";
      input.addEventListener("input", () => send(setting.key, fromHex(input.value)));
      controls[setting.key] = {
        input,
        set: (value) => { input.value = toHex(value); },
      };
    } else if (setting.step && setting.step < 0.01) {
      // Latitude/longitude: a slider can't resolve six decimal places.
      input = document.createElement("input");
      input.type = "number";
      input.min = setting.min;
      input.max = setting.max;
      input.step = setting.step;
      input.addEventListener("change", () => send(setting.key, input.value));
      controls[setting.key] = {
        input,
        set: (value) => { input.value = value; },
      };
    } else {
      input = document.createElement("input");
      input.type = "range";
      input.min = setting.min;
      input.max = setting.max;
      input.step = setting.step || (setting.kind === "int" ? 1 : 0.01);
      input.addEventListener("input", () => {
        readout.textContent = formatNumber(setting, input.value);
        send(setting.key, Number(input.value));
      });
      controls[setting.key] = {
        input,
        set: (value) => {
          input.value = value;
          readout.textContent = formatNumber(setting, value);
        },
      };
    }

    field.appendChild(input);
    field.appendChild(readout);
    return field;
  }

  function buildSettings(payload) {
    schema = payload.schema;
    sectionsHost.textContent = "";
    schema.sections.forEach((name) => {
      const section = document.createElement("div");
      section.className = "section";
      const heading = document.createElement("h2");
      heading.textContent = name;
      section.appendChild(heading);
      schema.settings
        .filter((setting) => setting.section === name)
        .forEach((setting) => section.appendChild(buildField(setting)));
      sectionsHost.appendChild(section);
    });
    applyValues(payload.values);
  }

  function applyValues(values) {
    for (const [key, value] of Object.entries(values)) {
      const control = controls[key];
      // Don't yank a control out from under someone mid-drag when an update
      // arrives from another tab.
      if (!control || control.input === document.activeElement) continue;
      control.set(value);
    }
  }

  fetch("/settings")
    .then((response) => response.json())
    .then(buildSettings)
    .catch(() => setStatus("Could not load settings"));

  // The board is served over plain HTTP, so `navigator.clipboard` is
  // undefined here (it needs a secure context) - hence the execCommand path.
  // Either way the JSON is also dropped into a textarea, so a browser that
  // blocks both still lets you select it by hand.
  function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    const scratch = document.createElement("textarea");
    scratch.value = text;
    scratch.setAttribute("readonly", "");
    scratch.style.position = "fixed";
    scratch.style.opacity = "0";
    document.body.appendChild(scratch);
    scratch.select();
    scratch.setSelectionRange(0, text.length);
    try {
      return document.execCommand("copy")
        ? Promise.resolve()
        : Promise.reject(new Error("copy rejected"));
    } finally {
      document.body.removeChild(scratch);
    }
  }

  document.getElementById("copy-json").addEventListener("click", () => {
    fetch("/settings")
      .then((response) => response.json())
      .then((payload) => {
        const values = payload.values;
        // Sorted, so two exports of the same board diff cleanly. Colour
        // triples are pulled back onto one line - indent 2 puts every
        // channel on its own row, which buries the rest of the settings.
        const text = JSON.stringify(values, Object.keys(values).sort(), 2)
          .replace(/\[\s*([\d\s,.-]+?)\s*\]/g,
                   (all, inner) => "[" + inner.split(/\s*,\s*/).join(", ") + "]");
        const box = document.getElementById("settings-json");
        box.value = text;
        box.style.display = "block";
        const count = Object.keys(values).length;
        copyToClipboard(text)
          .then(() => setStatus("Copied " + count + " settings to the clipboard"))
          .catch(() => { box.select(); setStatus("Copy blocked - select and copy"); });
      })
      .catch(() => setStatus("Could not reach the board"));
  });

  document.getElementById("reset").addEventListener("click", () => {
    fetch("/settings/reset", { method: "POST" })
      .then((response) => response.json())
      .then((result) => { applyValues(result.values); setStatus("Reset to defaults"); })
      .catch(() => setStatus("Could not reach the board"));
  });

  // Keep every open tab in sync.
  function connectSettings() {
    const source = new EventSource("/settings-stream");
    source.onmessage = (event) => applyValues(JSON.parse(event.data));
    source.onerror = () => {
      source.close();
      setTimeout(connectSettings, 1000);
    };
  }
  connectSettings();
</script>
</body>
</html>
"""


def _encode(pixels: PixelDisplay) -> dict:
    """Encode a frame for the wire as base64 of raw RGB bytes.

    The obvious encoding - `json.dumps(pixels.tolist())` - builds ~2k small
    Python lists and 6k boxed ints per frame. That benchmarks ~85x slower
    than this and produces a payload four times larger, and it ran on the
    same thread that drives the physical matrix, so on the Pi it was a
    leading cause of the visible stutter. `tobytes()` is a single C-level
    copy.
    """
    brightness = settings.get("display.brightness")
    if brightness >= 100:
        raw = pixels.astype(np.uint8)
    else:
        # Match what the matrix driver does to the physical panel, so the
        # mirror on the page shows the brightness you actually set.
        raw = (pixels * (brightness / 100.0)).clip(0, 255).astype(np.uint8)
    return {
        "width": pixels.shape[1],
        "height": pixels.shape[0],
        "pixels": base64.b64encode(raw.tobytes()).decode("ascii"),
    }


def _put_latest(client_queue: queue.Queue, payload: str) -> None:
    """Hand `payload` to a client, discarding any frame it hasn't picked up
    yet - a stale frame is worthless once a newer one exists."""
    while True:
        try:
            client_queue.put_nowait(payload)
            return
        except queue.Full:
            try:
                client_queue.get_nowait()
            except queue.Empty:
                pass


class Simulate(DisplayProtocol):
    def __init__(self, port: int = PORT, open_browser: bool = True, host: str = HOST):
        self._clients: list[queue.Queue] = []
        self._clients_lock = threading.Lock()
        self._latest: str | None = None

        self._gallery_clients: list[queue.Queue] = []
        self._gallery_clients_lock = threading.Lock()
        self._latest_gallery: str | None = None
        self._last_gallery_send = 0.0

        # Frames are handed off to `_broadcast_loop` rather than encoded
        # inline. `display_matrix` is called from the same thread that pushes
        # to the physical matrix, and that thread must never block on
        # encoding or on a slow browser socket. The slots hold only the most
        # recent frame, so a fast producer coalesces instead of queueing.
        self._pending_lock = threading.Lock()
        self._pending_ready = threading.Condition(self._pending_lock)
        self._pending_frame: Optional[PixelDisplay] = None
        self._pending_gallery: Optional[List[Tuple[str, PixelDisplay]]] = None

        # Settings changes are pushed to every open tab, so two browsers (or
        # a phone and a laptop) don't drift out of sync.
        self._settings_clients: List[queue.Queue] = []
        self._settings_clients_lock = threading.Lock()
        settings.subscribe(self._broadcast_settings)

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
        self._broadcaster = spawn_daemon(self._broadcast_loop)

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

            def _send_json(self, payload: dict, status: int = 200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self):
                """Read a JSON object body, or None if it isn't one.

                Length-capped: this server is reachable by anything on the
                network, so it must not try to buffer an arbitrarily large
                body into memory.
                """
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return None
                if length <= 0 or length > MAX_BODY_BYTES:
                    return None
                try:
                    parsed = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return None
                return parsed if isinstance(parsed, dict) else None

            def _serve_sse(self, clients: list, clients_lock, latest_getter):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                client_queue: queue.Queue = queue.Queue(maxsize=CLIENT_QUEUE_DEPTH)
                with clients_lock:
                    clients.append(client_queue)
                    latest = latest_getter()
                    if latest is not None:
                        _put_latest(client_queue, latest)

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
                elif self.path == "/settings":
                    self._send_json(
                        {
                            "schema": settings.schema_json(),
                            "values": settings.as_dict(),
                        }
                    )
                elif self.path == "/settings-stream":
                    self._serve_sse(
                        outer._settings_clients,
                        outer._settings_clients_lock,
                        lambda: json.dumps(settings.as_dict()),
                    )
                elif self.path in ("/favicon.ico", "/favicon.svg"):
                    self.send_response(200)
                    self.send_header("Content-Type", "image/svg+xml")
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_header("Content-Length", str(len(_FAVICON_BYTES)))
                    self.end_headers()
                    self.wfile.write(_FAVICON_BYTES)
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
                elif self.path == "/settings":
                    incoming = self._read_json()
                    if incoming is None:
                        self._send_json({"error": "expected a JSON object"}, 400)
                        return
                    applied, rejected = settings.update(incoming)
                    self._send_json(
                        {
                            "applied": {
                                key: list(value) if isinstance(value, tuple) else value
                                for key, value in applied.items()
                            },
                            "rejected": rejected,
                            "values": settings.as_dict(),
                        }
                    )
                elif self.path == "/settings/reset":
                    settings.reset()
                    self._send_json({"values": settings.as_dict()})
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

    @property
    def has_viewers(self) -> bool:
        """Whether any browser tab is watching the live frame."""
        with self._clients_lock:
            return bool(self._clients)

    @property
    def has_gallery_viewers(self) -> bool:
        """Whether any browser tab has the gallery view open. The caller uses
        this to skip collecting every program's frame when nobody's looking."""
        with self._gallery_clients_lock:
            return bool(self._gallery_clients)

    @validate_pixels
    def display_matrix(self, pixels: PixelDisplay) -> None:
        """Hand the current frame to the broadcaster for any connected tabs.

        Deliberately does no encoding and no socket I/O: on the Pi this is
        called from the same thread that drives the physical matrix, so any
        work here shows up directly as stutter on the board. With no tabs
        connected it does nothing at all.
        """
        if not self.has_viewers:
            return
        with self._pending_ready:
            self._pending_frame = pixels
            self._pending_ready.notify()

    def display_gallery(self, frames: List[Tuple[str, PixelDisplay]]) -> None:
        """Hand every program's current frame to the broadcaster, for the
        scrolling gallery view. Like `display_matrix`, this only stashes -
        encoding N frames is far too expensive to do on the caller's thread."""
        if not self.has_gallery_viewers:
            return
        with self._pending_ready:
            self._pending_gallery = frames
            self._pending_ready.notify()

    def _broadcast_loop(self) -> None:
        """Encode and fan out whatever frame is pending, at most every
        `FRAME_INTERVAL`.

        Running on its own thread is the point: encoding and writing to
        browser sockets can't be allowed to hold up the matrix. Because the
        pending slots hold only the newest frame, a producer running faster
        than this loop simply has its intermediate frames dropped.
        """
        while True:
            with self._pending_ready:
                while self._pending_frame is None and self._pending_gallery is None:
                    self._pending_ready.wait()
                frame = self._pending_frame
                gallery = self._pending_gallery
                self._pending_frame = None
                self._pending_gallery = None

            if frame is not None:
                payload = json.dumps(_encode(frame))
                self._latest = payload
                self._send(payload, self._clients, self._clients_lock)

            if gallery is not None:
                now = time.monotonic()
                if now - self._last_gallery_send >= self._gallery_interval():
                    self._last_gallery_send = now
                    payload = json.dumps(
                        [dict(_encode(pixels), name=name) for name, pixels in gallery]
                    )
                    self._latest_gallery = payload
                    self._send(
                        payload, self._gallery_clients, self._gallery_clients_lock
                    )

            time.sleep(self._frame_interval())

    def _broadcast_settings(self, _changed: dict) -> None:
        """Push the full value set to every open tab whenever anything
        changes. Runs on the thread that made the change, so it must only
        touch the (bounded, non-blocking) client queues."""
        self._send(
            json.dumps(settings.as_dict()),
            self._settings_clients,
            self._settings_clients_lock,
        )

    @staticmethod
    def _frame_interval() -> float:
        fps = settings.get("display.web_fps")
        return 1.0 / fps if fps > 0 else FRAME_INTERVAL

    @staticmethod
    def _gallery_interval() -> float:
        fps = settings.get("display.gallery_fps")
        return 1.0 / fps if fps > 0 else GALLERY_INTERVAL

    @staticmethod
    def _send(payload: str, clients: List[queue.Queue], clients_lock) -> None:
        with clients_lock:
            targets = list(clients)
        for client_queue in targets:
            _put_latest(client_queue, payload)
