# ⚡ llama.cpp Monitor — AMD ROCm Edition

A single-file, real-time monitoring dashboard for [llama.cpp](https://github.com/ggerganov/llama.cpp) server with full AMD ROCm GPU support.

Based on [abhiFSD/llama.cpp-Monitor-Dashboard](https://github.com/abhiFSD/llama.cpp-Monitor-Dashboard), modified for AMD APU systems and llama.cpp router mode.

## Features

- **Real-time tok/s** — per-slot and aggregate generation speed
- **Slot monitoring** — see which slots are active, idle, or queued
- **Live sparkline charts** — generation speed, active slots over time
- **Completed request log** — history of finished generations with timing
- **Model info display** — name, size, context, parameters, architecture
- **Server stats** — cumulative tokens, decode calls, prompt/generation time
- **AMD ROCm GPU metrics** — util%, temp, power, clock, VRAM (via sysfs GTT)
- **CPU, RAM, disk monitoring** — via lightweight sidecar script
- **Auto-detects** model name and slot count from llama.cpp metrics
- **Router mode compatible** — auto-detects loaded models without triggering reloads
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

### 3. (Optional) Hardware monitoring sidecar

For GPU, CPU, RAM, and disk metrics, run the included `hw-metrics.py` sidecar:

```bash
pip install flask psutil
python3 hw-metrics.py --port 8083
```

Then open the dashboard with the `hw` parameter:
```
http://localhost:8080/monitor.html?hw=http://localhost:8083
```

## URL Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `server` | Same origin | llama.cpp server URL (e.g., `http://host:8080`) |
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

## Router Mode

If your llama.cpp server runs in router mode (`--models-preset`), this dashboard automatically discovers the loaded model via `/v1/models` and only queries metrics for it. It **does not** trigger loading of unloaded models.

This fixes a bug in the original dashboard where passing `?model=...` to `/metrics` would cause the router to load the model on every poll.

## Hardware Metrics Sidecar

The optional `hw-metrics.py` script provides system metrics via a simple HTTP endpoint.

### Setup

```bash
# Install dependencies
pip install flask psutil

# Run
python3 hw-metrics.py --port 8083
```

### GPU Support

- **NVIDIA**: Uses `nvidia-smi` (original version)
- **AMD ROCm**: Uses `rocm-smi` + sysfs (`/sys/class/drm/card*/device/mem_info_gtt_*`) for APU shared memory

On AMD APUs with unified memory, VRAM is reported via the GTT (Graphics Translation Table), which represents the actual shared system memory used by the GPU — not just the small dedicated VRAM portion.

### API

**GET `/hw`** — Returns JSON:

```json
{
  "gpu": {
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

- **`/v1/models`** — Discovers which model is loaded (read-only, doesn't trigger loads)
- **`/metrics?model=...`** — Prometheus-format metrics (tok/s, token counts, timing)
- **`/slots?model=...`** — Real-time slot status (active/idle, context size, progress)

Everything runs client-side in the browser. No backend, no build step, no dependencies.

## Dashboard Layout

1. **Model info bar** — Model name, size, context, parameters, architecture
2. **Top bar** — Aggregate gen tok/s, prompt tok/s, active slots, total tokens
3. **Hardware cards** — GPU util, VRAM, CPU, RAM with live sparklines (if sidecar running)
4. **Slot grid** — Per-slot tok/s with mini charts, progress bars for active generation
5. **Charts** — Generation speed and active slots over time
6. **Request log** — Completed requests with timing breakdown
7. **Server stats** — Cumulative statistics

## Changes from Original

| Change | Description |
|--------|-------------|
| AMD ROCm GPU support | Replaced nvidia-smi with `rocm-smi` + sysfs for APU VRAM |
| Router mode fix | Auto-discovers loaded model via `/v1/models` instead of requiring `?model=` param |
| No auto-reload | Dashboard only polls metrics for already-loaded models |
| Model info display | Shows model name, size, context, parameters, and architecture |
| Removed gputil dependency | No longer needed — uses rocm-smi instead |

## License

MIT

Original work: [abhiFSD/llama.cpp-Monitor-Dashboard](https://github.com/abhiFSD/llama.cpp-Monitor-Dashboard)
