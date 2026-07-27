# ⚡ llama.cpp Monitor — AMD ROCm + NVIDIA Edition

A single-file, real-time monitoring dashboard for [llama.cpp](https://github.com/ggerganov/llama.cpp) with AMD ROCm/APU and NVIDIA GPU support.

Based on [abhiFSD/llama.cpp-Monitor-Dashboard](https://github.com/abhiFSD/llama.cpp-Monitor-Dashboard), with hardened NVIDIA telemetry plus AMD APU and llama.cpp router support.

## Features

- **Real-time tok/s** — per-slot prompt and generation speed plus server aggregates
- **Slot phase monitoring** — distinguishes prompt processing, generation, idle, and queued work
- **Live charts** — prompt speed, generation speed, and active slots over time
- **Completed request log** — prompt/generated token counts, cache usage, speed, and timing
- **Model info display** — name, size, context, parameters, architecture
- **Server stats** — cumulative tokens, decode calls, prompt/generation time
- **AMD and NVIDIA GPU metrics** — utilization, temperature, power, fan, clocks, and memory
- **CPU, RAM, disk monitoring** — via lightweight sidecar script
- **Auto-detects** model name and slot count from llama.cpp metrics
- **Router mode compatible** — reads router status and disables request-time autoload
- **Model unloading** — click a loaded router model and confirm to release its RAM/VRAM
- **Responsive** — works on desktop and mobile
- **Dark theme** — GitHub-inspired dark UI

## Quick Start

### 1. Start llama.cpp with metrics enabled

```bash
llama-server \
  --models-dir /path/to/models \
  --host 0.0.0.0 \
  --port 8080 \
  --metrics \
  --slots
```

> **Required flags:** `--metrics` and `--slots` must be enabled for the dashboard to work.

### 2. Serve the dashboard

**Option A: Use a static file server**

```bash
python3 -m http.server 3001
# Then open http://localhost:3001/monitor.html?server=http://localhost:8080
```

**Option B: Open directly in browser**

```bash
# If llama.cpp is on localhost:8080 (default)
xdg-open monitor.html

# If llama.cpp is on a different host/port, use URL params
xdg-open "monitor.html?server=http://192.168.1.100:8080"
```

Opening the local file only makes the dashboard send API requests to port 8080. It does not bind that port, replace llama.cpp's integrated web UI, or proxy its API. The integrated UI remains available at `http://localhost:8080`.

### 3. (Optional) Hardware monitoring sidecar

For GPU, CPU, RAM, and disk metrics, run the included `hw-metrics.py` sidecar:

```bash
pip install -r requirements.txt
python3 hw-metrics.py --port 8083
```

Then open the dashboard with the `hw` parameter:
```
http://localhost:3001/monitor.html?server=http://localhost:8080&hw=http://localhost:8083
```

## URL Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `server` | Same origin; `http://localhost:8080` for `file://` | llama.cpp API URL (e.g., `http://host:8080`) |
| `hw` | *(none)* | Hardware metrics sidecar URL (e.g., `http://host:8083`) |
| `slots` | Auto-detect | Number of slots (auto-detected from `/slots` endpoint) |
| `poll` | `1000` | Polling interval in milliseconds |

### Examples

```
# Local server, default port
monitor.html

# Remote server
monitor.html?server=http://192.168.1.50:8080

# With hardware metrics
monitor.html?server=http://192.168.1.50:8080&hw=http://192.168.1.50:8083

# Slower polling (every 2 seconds)
monitor.html?poll=2000
```

## Router Mode and model-loading safety

If llama.cpp runs in router mode (`--models-dir` or `--models-preset`), the dashboard discovers model state through `GET /models`. This is the router's read-only catalog/status endpoint; the dashboard never adds its mutating `reload` parameter.

The dashboard automatically makes these llama.cpp requests:

```text
GET /models
GET /metrics?model=<loaded-id>&autoload=false
GET /slots?model=<loaded-id>&autoload=false
```

When the user clicks a loaded model and confirms the warning, it additionally sends the documented router management request:

```text
POST /models/unload
Content-Type: application/json

{"model":"<loaded-id>"}
```

This unload action releases model memory but does not delete model files. It may terminate active requests, so the dashboard shows an extra warning for a model currently generating. It never calls `POST /models/load` and never sends inference requests. The dashboard filters the catalog to models whose status is exactly `loaded`; `unloaded`, `loading`, and `sleeping` models are not polled. Both model-specific monitoring requests explicitly use `autoload=false`, independently of the router's global `--models-autoload` setting.

In upstream llama.cpp, an ordinary routed request selecting an unloaded model loads it when autoload is enabled (the default). For GET routes the model comes from `?model=...`; for POST inference routes it normally comes from the JSON `model` field. `POST /models/load` always requests an explicit load. The dashboard uses none of these loading forms.

For defense in depth, the router itself can be started with `--no-models-autoload`. This changes behavior for every API client, not only this dashboard.

> **Sleep-mode limitation:** upstream only guarantees that `GET /health`, `GET /props`, and `GET /models` do not wake a model sleeping because of `--sleep-idle-seconds`. `/metrics` and `/slots` are not sleep-exempt. The dashboard checks for exact `loaded` status before requesting them, but llama.cpp has no atomic “only if still awake” monitoring request. Therefore monitoring may delay sleep, and a status change in the small interval between `/models` and a metrics request could wake a model. If zero possibility of waking a sleeping model is required, do not use this dashboard while `--sleep-idle-seconds` is enabled; only poll `/models`, `/props`, or `/health`.

### Prompt progress accuracy

Current llama.cpp exposes `n_prompt_tokens_processed` and `n_prompt_tokens_cache` through `/slots`, but it does not expose the request's final total prompt length there while prefill is still running. The dashboard therefore shows an animated indeterminate prompt bar with exact evaluated and cached counters instead of presenting a misleading percentage.

An exact prompt percentage is only available to the client that owns a streaming completion request made with `return_progress`; a separate monitoring page cannot access those stream events. Prompt speed in the completed-request table is marked with `~` because it is sampled at the dashboard polling interval. Very short prefills may complete between polls and show no per-request speed, while the aggregate server prompt metrics remain available.

The dashboard accepts both the current object form and older array form of the `/slots.next_token` field.

## Hardware Metrics Sidecar

The optional `hw-metrics.py` script provides system metrics via a simple HTTP endpoint.

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run
python3 hw-metrics.py --port 8083
```

### GPU Support

The sidecar normalizes AMD and NVIDIA metrics into the same API, so all GPU cards and history charts work with either backend.

- **NVIDIA:** invokes `nvidia-smi` once per hardware poll using its unitless CSV query interface. It reports utilization, temperature, power draw/limit, fan, graphics and memory clocks, and used/total/free VRAM. Unsupported values such as fan speed on passively cooled or datacenter cards are returned as `N/A` rather than breaking the dashboard.
- **AMD:** uses amdgpu sysfs first, with `rocm-smi` JSON output as a fallback. Basic APU monitoring can therefore work without `rocm-smi`.

On AMD APUs with unified memory, memory is reported via the GTT shared-memory pool rather than only the small firmware-reserved VRAM allocation. Discrete AMD and NVIDIA GPUs report dedicated VRAM.

Backend and device selection:

```bash
# Auto-detect; NVIDIA is preferred if both backends are available
python3 hw-metrics.py

# Force AMD GPU 0
python3 hw-metrics.py --gpu-backend amd --gpu-index 0

# Select the second NVIDIA GPU
python3 hw-metrics.py --gpu-backend nvidia --gpu-index 1
```

`--gpu-index` is zero-based within the selected vendor backend. NVIDIA support requires a working driver and `nvidia-smi` in `PATH`; it does not require GPUtil, NVML, or another Python package.

### API

**GET `/hw`** — Returns JSON:

```json
{
  "gpu": {
    "backend": "nvidia",
    "index": 0,
    "name": "NVIDIA GeForce RTX 4090",
    "memory_type": "VRAM",
    "util": "45",
    "temp": "62",
    "power": "150.5",
    "power_limit": "N/A",
    "fan": "35",
    "clock_gpu": "1800",
    "clock_mem": "N/A",
    "vram_used": "12288",
    "vram_total": "16384",
    "vram_free": "4096"
  },
  "cpu": {
    "percent": 25.3,
    "freq": "3500",
    "cores": "8",
    "threads": "16"
  },
  "ram": {
    "used": "12.4",
    "total": "32.0",
    "percent": 38.8
  },
  "disk": {
    "percent": 55.2
  }
}
```

### Without GPU

If no GPU is detected, the GPU section is automatically hidden. CPU, RAM, and disk metrics still work.

## Requirements

- **llama.cpp** server with `--metrics` and `--slots` flags
- A modern web browser
- *(Optional)* Python 3 with Flask + psutil for hardware metrics

## How It Works

The dashboard polls three endpoints:

- **`/models`** — Reads router model status without loading models
- **`/metrics?model=...&autoload=false`** — Prometheus-format metrics
- **`/slots?model=...&autoload=false`** — Real-time slot status

Everything runs client-side in the browser. No backend, no build step, no dependencies.

## Dashboard Layout

1. **Model info bar** — Model name, size, context, parameters, architecture
2. **Top bar** — Aggregate gen tok/s, prompt tok/s, active slots, total tokens
3. **Hardware cards** — GPU util, VRAM, CPU, RAM with live sparklines (if sidecar running)
4. **Slot grid** — Per-slot phase, prompt/generation tok/s, cache counters, and progress indicators
5. **Charts** — Prompt speed, generation speed, and active slots over time
6. **Request log** — Prompt/generated counts, cache use, sampled speeds, and total time
7. **Server stats** — Cumulative statistics

## Changes from Original

| Change | Description |
|--------|-------------|
| AMD ROCm GPU support | Uses amdgpu sysfs, with `rocm-smi` fallback and GTT memory for APUs |
| NVIDIA GPU support | Hardened `nvidia-smi` CSV integration with `N/A` handling and GPU selection |
| Router mode fix | Discovers loaded models through the read-only `/models` status endpoint |
| No request-time autoload | Polls only `loaded` models and sends `autoload=false` |
| Model info display | Shows model name, size, context, parameters, and architecture |
| No GPUtil dependency | Uses amdgpu sysfs, `rocm-smi`, or `nvidia-smi` directly |

## License

MIT

Original work: [abhiFSD/llama.cpp-Monitor-Dashboard](https://github.com/abhiFSD/llama.cpp-Monitor-Dashboard)
