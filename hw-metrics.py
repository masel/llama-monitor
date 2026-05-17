#!/usr/bin/env python3
"""
Hardware metrics sidecar for llama.cpp Monitor.
Provides GPU, CPU, RAM, and disk stats via a simple HTTP endpoint.

Usage:
  pip install flask psutil
  python3 hw-metrics.py --port 8083
"""

import argparse
import json
import re
import subprocess
import psutil
from flask import Flask, jsonify
from flask_cors import cross_origin

app = Flask(__name__)

def get_gpu_info():
    """Get AMD GPU metrics via rocm-smi."""
    try:
        result = subprocess.run(
            ['rocm-smi'], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None

        lines = result.stdout.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line and line[0].isdigit() and 'Temp' not in line and '=====' not in line:
                parts = line.split()
                # Columns: 0=Device, 1=Node, 2=DID, 3=GUID, 4=Temp, 5=Power,
                # 6-8=Partitions, 9=SCLK, 10=MCLK, 11=Fan, 12=Perf, 13=PwrCap, 14=VRAM%, 15=GPU%
                if len(parts) < 16:
                    return None

                temp_str = parts[4].replace('\x1b[3m', '').replace('\x1b[0m', '').rstrip('°C')
                power_str = parts[5].rstrip('W')
                fan_str = parts[11].rstrip('%')
                vram_pct = parts[14].rstrip('%')
                gpu_pct = parts[15].rstrip('%')

                try:
                    temp = float(temp_str)
                except ValueError:
                    temp = 0

                try:
                    power = float(power_str)
                except ValueError:
                    power = 0

                try:
                    fan = int(fan_str)
                except ValueError:
                    fan = 0

                try:
                    vram_percent = int(vram_pct)
                except ValueError:
                    vram_percent = 0

                try:
                    gpu_percent = int(gpu_pct)
                except ValueError:
                    gpu_percent = 0

                # Get VRAM from sysfs (more reliable than rocm-smi on APUs)
                vram_total = 0
                vram_used = 0
                try:
                    import glob
                    for card in glob.glob('/sys/class/drm/card*/device/mem_info_gtt_total'):
                        with open(card) as f:
                            vram_total = int(f.read().strip())
                        break
                    for card in glob.glob('/sys/class/drm/card*/device/mem_info_gtt_used'):
                        with open(card) as f:
                            vram_used = int(f.read().strip())
                        break
                except Exception:
                    pass

                # Convert bytes to MiB for the dashboard
                vram_total_mib = vram_total / (1024 * 1024)
                vram_used_mib = vram_used / (1024 * 1024)
                vram_free_mib = (vram_total - vram_used) / (1024 * 1024)

                # Get sclk (GPU clock)
                sclk = 'N/A'
                try:
                    clk_result = subprocess.run(
                        ['rocm-smi', '--showallinfo'], capture_output=True, text=True, timeout=5
                    )
                    for cline in clk_result.stdout.split('\n'):
                        if 'sclk clock level:' in cline:
                            match = re.search(r'\((\d+)Mhz\)', cline)
                            if match:
                                sclk = match.group(1)
                            break
                except Exception:
                    pass

                return {
                    'util': str(gpu_percent),
                    'temp': str(temp),
                    'power': str(power),
                    'power_limit': 'N/A',
                    'fan': str(fan),
                    'clock_gpu': sclk,
                    'clock_mem': 'N/A',
                    'vram_used': str(round(vram_used_mib)),
                    'vram_total': str(round(vram_total_mib)),
                    'vram_free': str(round(vram_free_mib))
                }
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

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
        disk = psutil.disk_usage('/')
        return {'percent': disk.percent}
    except Exception:
        try:
            disk = psutil.disk_usage('C:\\')
            return {'percent': disk.percent}
        except Exception:
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
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    args = parser.parse_args()
    
    print(f'⚡ hw-metrics sidecar starting on {args.host}:{args.port}')
    print(f'   GPU: {"rocm-smi found" if get_gpu_info() else "not available (no AMD GPU or rocm-smi)"}')
    print(f'   Endpoint: http://{args.host}:{args.port}/hw')
    
    app.run(host=args.host, port=args.port, debug=False)
