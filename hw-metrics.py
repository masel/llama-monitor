#!/usr/bin/env python3
"""Hardware metrics sidecar for llama.cpp Monitor.

AMD prefers amdgpu sysfs and falls back to rocm-smi JSON. NVIDIA uses one
unitless nvidia-smi CSV query per poll. Both backends expose a common API.
"""

import argparse
import csv
import json
import re
import subprocess
from pathlib import Path

import psutil
from flask import Flask, jsonify

app = Flask(__name__)
SYS_DRM = Path('/sys/class/drm')
GPU_BACKEND = 'auto'
GPU_INDEX = 0
GPU_COMMAND_TIMEOUT = 3
_active_gpu_backend = None


def _read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _amd_devices():
    """Return amdgpu device directories, without counting connector entries."""
    devices = []
    cards = [path for path in SYS_DRM.glob('card*') if re.fullmatch(r'card\d+', path.name)]
    for card in sorted(cards, key=lambda path: int(path.name[4:])):
        device = card / 'device'
        if _read(device / 'vendor') == '0x1002':
            devices.append(device)
    return devices


def _hwmon_value(device, filename, scale=1.0):
    for path in sorted((device / 'hwmon').glob(f'hwmon*/{filename}')):
        value = _float(_read(path))
        if value is not None:
            return value / scale
    return None


def _current_clock(device, filename):
    """Read the active clock from an amdgpu pp_dpm_* file, in MHz."""
    text = _read(device / filename)
    if not text:
        return None
    active = next((line for line in text.splitlines() if '*' in line), text)
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:Mhz|MHz)', active, re.I)
    return _float(match.group(1)) if match else None


def _memory_bytes(device):
    """Choose dedicated VRAM, or GTT shared memory for an APU.

    APUs commonly expose a small firmware VRAM allocation plus a much larger
    GTT pool used by ROCm.  Discrete cards normally have at least 1 GiB of
    dedicated VRAM, which is the useful quantity to display for them.
    """
    vram_total = int(_float(_read(device / 'mem_info_vram_total')) or 0)
    vram_used = int(_float(_read(device / 'mem_info_vram_used')) or 0)
    gtt_total = int(_float(_read(device / 'mem_info_gtt_total')) or 0)
    gtt_used = int(_float(_read(device / 'mem_info_gtt_used')) or 0)

    if gtt_total > vram_total and vram_total < 1024 ** 3:
        total, used, memory_type = gtt_total, gtt_used, 'GTT shared memory'
    elif vram_total:
        total, used, memory_type = vram_total, vram_used, 'VRAM'
    else:
        total, used, memory_type = gtt_total, gtt_used, 'GTT shared memory'
    used = min(max(used, 0), total) if total else 0
    return total, used, memory_type


def _sysfs_gpu_info(index=0):
    devices = _amd_devices()
    if index < 0 or index >= len(devices):
        return None
    device = devices[index]
    util = _float(_read(device / 'gpu_busy_percent'))
    temp = _hwmon_value(device, 'temp1_input', 1000)
    power = _hwmon_value(device, 'power1_average', 1_000_000)
    if power is None:
        power = _hwmon_value(device, 'power1_input', 1_000_000)
    power_limit = _hwmon_value(device, 'power1_cap', 1_000_000)

    fan = None
    pwm = _hwmon_value(device, 'pwm1')
    pwm_max = _hwmon_value(device, 'pwm1_max') or 255
    if pwm is not None and pwm_max:
        fan = 100 * pwm / pwm_max

    total, used, memory_type = _memory_bytes(device)
    mib = 1024 * 1024
    return {
        'backend': 'amd',
        'index': index,
        'name': _read(device / 'product_name') or f'AMD GPU {index}',
        'memory_type': memory_type,
        'util': util,
        'temp': temp,
        'power': power,
        'power_limit': power_limit,
        'fan': fan,
        'clock_gpu': _current_clock(device, 'pp_dpm_sclk'),
        'clock_mem': _current_clock(device, 'pp_dpm_mclk'),
        'vram_used': used / mib if total else None,
        'vram_total': total / mib if total else None,
        'vram_free': (total - used) / mib if total else None,
    }


def _find_json_metric(data, key_patterns):
    """Find a numeric value in rocm-smi JSON despite version-specific keys."""
    if isinstance(data, dict):
        for key, value in data.items():
            normalized = key.lower()
            if any(re.search(pattern, normalized) for pattern in key_patterns):
                number = re.search(r'-?\d+(?:\.\d+)?', str(value))
                if number:
                    return float(number.group())
        for value in data.values():
            found = _find_json_metric(value, key_patterns)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_json_metric(value, key_patterns)
            if found is not None:
                return found
    return None


def _rocm_smi_gpu_info(index=0):
    """Fallback for systems exposing metrics only through rocm-smi."""
    try:
        result = subprocess.run(
            ['rocm-smi', '--showuse', '--showtemp', '--showpower', '--showfan',
             '--showclocks', '--showmeminfo', 'vram', '--json'],
            capture_output=True, text=True, timeout=GPU_COMMAND_TIMEOUT, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    if isinstance(data, dict):
        cards = [(key, value) for key, value in data.items()
                 if isinstance(value, dict) and re.search(r'(?:card|gpu)\D*\d+', key, re.I)]
        cards.sort(key=lambda item: int(re.search(r'\d+', item[0]).group()))
        if cards:
            if index < 0 or index >= len(cards):
                return None
            data = cards[index][1]
        elif index != 0:
            return None

    total = _find_json_metric(data, [r'vram total.*\(b\)', r'total vram.*\(b\)'])
    used = _find_json_metric(data, [r'vram total used.*\(b\)', r'used vram.*\(b\)'])
    mib = 1024 * 1024
    return {
        'backend': 'amd',
        'index': index,
        'name': f'AMD GPU {index}',
        'memory_type': 'VRAM',
        'util': _find_json_metric(data, [r'^gpu use', r'gpu.*use.*%']),
        'temp': _find_json_metric(data, [r'temperature.*\(c\)', r'temp.*edge']),
        'power': _find_json_metric(data, [r'power.*\(w\)']),
        'power_limit': _find_json_metric(data, [r'power cap.*\(w\)', r'power limit']),
        'fan': _find_json_metric(data, [r'fan.*%']),
        'clock_gpu': _find_json_metric(data, [r'sclk.*clock']),
        'clock_mem': _find_json_metric(data, [r'mclk.*clock']),
        'vram_used': used / mib if used is not None else None,
        'vram_total': total / mib if total is not None else None,
        'vram_free': (total - used) / mib if total is not None and used is not None else None,
    }


NVIDIA_QUERY_FIELDS = (
    'index', 'name', 'utilization.gpu', 'temperature.gpu', 'power.draw',
    'power.limit', 'fan.speed', 'clocks.current.graphics',
    'clocks.current.memory', 'memory.used', 'memory.total', 'memory.free',
)


def _nvidia_number(value):
    """Parse nvidia-smi values while preserving unsupported fields as None."""
    value = value.strip()
    if not value or value.lower() in {'n/a', 'na', 'not supported'} or value.startswith('['):
        return None
    match = re.fullmatch(r'-?\d+(?:\.\d+)?', value)
    return float(value) if match else None


def _nvidia_smi_gpu_info(index=0):
    """Read one physical NVIDIA GPU using stable, unitless CSV output."""
    if index < 0:
        return None
    command = [
        'nvidia-smi', f'--id={index}',
        '--query-gpu=' + ','.join(NVIDIA_QUERY_FIELDS),
        '--format=csv,noheader,nounits',
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=GPU_COMMAND_TIMEOUT, check=False,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None

    try:
        rows = [row for row in csv.reader(result.stdout.splitlines()) if row]
    except csv.Error:
        return None
    if len(rows) != 1 or len(rows[0]) != len(NVIDIA_QUERY_FIELDS):
        return None
    values = [value.strip() for value in rows[0]]
    reported_index = _nvidia_number(values[0])
    return {
        'backend': 'nvidia',
        'index': int(reported_index) if reported_index is not None else index,
        'name': values[1] or f'NVIDIA GPU {index}',
        'memory_type': 'VRAM',
        'util': _nvidia_number(values[2]),
        'temp': _nvidia_number(values[3]),
        'power': _nvidia_number(values[4]),
        'power_limit': _nvidia_number(values[5]),
        'fan': _nvidia_number(values[6]),
        'clock_gpu': _nvidia_number(values[7]),
        'clock_mem': _nvidia_number(values[8]),
        'vram_used': _nvidia_number(values[9]),
        'vram_total': _nvidia_number(values[10]),
        'vram_free': _nvidia_number(values[11]),
    }


def _amd_gpu_info(index=0):
    values = _sysfs_gpu_info(index)
    essential = ('util', 'temp', 'vram_total')
    if not values or not any(values.get(key) is not None for key in essential):
        values = _rocm_smi_gpu_info(index)
    return values


def _display(value, digits=0):
    if value is None:
        return 'N/A'
    return str(round(value, digits)) if digits else str(round(value))


def get_gpu_info():
    """Get one selected AMD or NVIDIA GPU in the dashboard's common schema."""
    global _active_gpu_backend
    if GPU_BACKEND == 'auto':
        preferred = ['nvidia', 'amd']
        backends = ([_active_gpu_backend] + [b for b in preferred if b != _active_gpu_backend]
                    if _active_gpu_backend else preferred)
    else:
        backends = [GPU_BACKEND]

    values = None
    for backend in backends:
        if backend == 'nvidia':
            values = _nvidia_smi_gpu_info(GPU_INDEX)
        elif backend == 'amd':
            values = _amd_gpu_info(GPU_INDEX)
        if values:
            _active_gpu_backend = backend
            break
    if not values:
        # Retry detection if a previously selected auto backend disappeared.
        if GPU_BACKEND == 'auto' and _active_gpu_backend:
            _active_gpu_backend = None
        return None

    return {
        'backend': values.get('backend', 'unknown'),
        'index': values.get('index', GPU_INDEX),
        'name': values.get('name', 'GPU'),
        'memory_type': values.get('memory_type', 'VRAM'),
        'util': _display(values.get('util')),
        'temp': _display(values.get('temp'), 1),
        'power': _display(values.get('power'), 1),
        'power_limit': _display(values.get('power_limit'), 1),
        'fan': _display(values.get('fan')),
        'clock_gpu': _display(values.get('clock_gpu')),
        'clock_mem': _display(values.get('clock_mem')),
        'vram_used': _display(values.get('vram_used')),
        'vram_total': _display(values.get('vram_total')),
        'vram_free': _display(values.get('vram_free')),
    }


def get_cpu_info():
    """Get CPU metrics."""
    freq = psutil.cpu_freq()
    return {
        'percent': psutil.cpu_percent(interval=0),
        'freq': str(int(freq.current)) if freq else '--',
        'cores': str(psutil.cpu_count(logical=False) or '--'),
        'threads': str(psutil.cpu_count(logical=True) or '--')
    }


def get_ram_info():
    """Get RAM metrics."""
    mem = psutil.virtual_memory()
    return {
        'used': f'{mem.used / (1024**3):.1f}',
        'total': f'{mem.total / (1024**3):.1f}',
        'percent': mem.percent
    }


def get_disk_info():
    """Get disk usage for the root partition."""
    try:
        return {'percent': psutil.disk_usage('/').percent}
    except OSError:
        try:
            return {'percent': psutil.disk_usage('C:\\').percent}
        except OSError:
            return {'percent': 0}


@app.route('/hw')
def hw():
    """Return all hardware metrics as JSON."""
    data = {
        'cpu': get_cpu_info(),
        'ram': get_ram_info(),
        'disk': get_disk_info()
    }
    gpu = get_gpu_info()
    if gpu:
        data['gpu'] = gpu
    response = jsonify(data)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.route('/health')
def health():
    response = jsonify({'status': 'ok'})
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Hardware metrics sidecar for llama.cpp Monitor')
    parser.add_argument('--port', type=int, default=8083, help='Port to listen on (default: 8083)')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument(
        '--gpu-backend', choices=('auto', 'amd', 'nvidia'), default='auto',
        help='GPU backend (default: auto; NVIDIA is preferred when both are present)',
    )
    parser.add_argument(
        '--gpu-index', type=int, default=0,
        help='zero-based GPU index within the selected backend (default: 0)',
    )
    args = parser.parse_args()
    if args.gpu_index < 0:
        parser.error('--gpu-index must be zero or greater')

    GPU_BACKEND = args.gpu_backend
    GPU_INDEX = args.gpu_index
    gpu = get_gpu_info()
    print(f'⚡ hw-metrics sidecar starting on {args.host}:{args.port}')
    if gpu:
        print(f'   GPU: {gpu["name"]} ({gpu["backend"]}, index {gpu["index"]})')
    else:
        print(f'   GPU: not available (backend={GPU_BACKEND}, index={GPU_INDEX})')
    print(f'   Endpoint: http://{args.host}:{args.port}/hw')
    app.run(host=args.host, port=args.port, debug=False)
