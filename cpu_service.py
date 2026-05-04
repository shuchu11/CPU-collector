"""
cpu_service.py — Standalone CPU Monitoring Microservice
Extracted from sideloader service. Run directly:
    python cpu_service.py
"""

import io
import re
import time
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # non-interactive backend, safe inside Docker
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
import pandas as pd

from flask import Flask, jsonify, request, send_file

app = Flask(__name__)
PORT = 5001  # Change as needed


# ============================================================================
# UTILS
# ============================================================================

def run_cmd(cmd: str, timeout: int = 30) -> Optional[str]:
    """Execute shell command, return stdout or None on failure"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:
        return None


def nsenter(cmd: str, timeout: int = 30) -> Optional[str]:
    """Execute command in host namespace via nsenter"""
    full_cmd = f"nsenter -t 1 -m -u -n -i {cmd}"
    return run_cmd(full_cmd, timeout)


# ============================================================================
# BASE COLLECTOR
# ============================================================================

class BaseCollector(ABC):
    """Base class for all monitoring collectors — enforces 1s sampling"""

    def __init__(self):
        self.samples = []
        self.timestamps = []

    @abstractmethod
    def collect_sample(self) -> Dict[str, Any]:
        pass

    def gather(self, duration: int, include_timeseries: bool = False) -> Dict[str, Any]:
        """Gather samples for duration seconds at 1s intervals"""
        self.samples = []
        self.timestamps = []
        for _ in range(duration):
            ts = time.time()
            sample = self.collect_sample()
            if sample is not None:
                self.timestamps.append(ts)
                self.samples.append(sample)
            time.sleep(1)
        return self.aggregate(include_timeseries)

    @abstractmethod
    def aggregate(self, include_timeseries: bool = True) -> Dict[str, Any]:
        pass

    def get_stats(self, values: List[float]) -> Dict[str, float]:
        if not values:
            return {"min": 0, "max": 0, "avg": 0}
        return {
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "avg": round(sum(values) / len(values), 2),
        }


# ============================================================================
# CPU COLLECTORS
# ============================================================================

class CPUCollector(BaseCollector):
    """Monitor per-core CPU usage with detailed breakdown"""

    def __init__(self, include_breakdown: bool = False):
        super().__init__()
        self.prev_stats = None
        self.include_breakdown = include_breakdown

    def _read_cpu_stats(self) -> Dict[str, Dict[str, int]]:
        out = nsenter("cat /proc/stat")
        if not out:
            return {}

        cpu_stats = {}
        for line in out.split("\n"):
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            cpu_name = parts[0]
            if len(parts) < 8:
                continue
            values = [int(x) for x in parts[1:8]]
            cpu_stats[cpu_name] = {
                "user":    values[0],
                "nice":    values[1],
                "system":  values[2],
                "idle":    values[3],
                "iowait":  values[4],
                "irq":     values[5],
                "softirq": values[6],
                "total":   sum(values),
            }
        return cpu_stats

    def collect_sample(self) -> Optional[Dict[str, Any]]:
        current_stats = self._read_cpu_stats()
        if not current_stats:
            return None

        sample = {}
        if self.prev_stats:
            for cpu_name, current in current_stats.items():
                if cpu_name not in self.prev_stats:
                    continue
                prev = self.prev_stats[cpu_name]
                delta_total = current["total"] - prev["total"]

                if delta_total == 0:
                    sample[cpu_name] = {"usage": 0.0}
                    if self.include_breakdown:
                        sample[cpu_name]["breakdown"] = {k: 0.0 for k in ("user", "system", "iowait", "irq", "softirq")}
                    continue

                delta_idle = current["idle"] - prev["idle"]
                usage = ((delta_total - delta_idle) / delta_total) * 100
                sample[cpu_name] = {"usage": round(usage, 2)}

                if self.include_breakdown:
                    sample[cpu_name]["breakdown"] = {
                        k: round(((current[k] - prev[k]) / delta_total) * 100, 2)
                        for k in ("user", "system", "iowait", "irq", "softirq")
                    }

        self.prev_stats = current_stats
        return sample if sample else None

    def aggregate(self, include_timeseries: bool = True) -> Dict[str, Any]:
        if not self.samples:
            return {"error": "no samples collected"}

        cpu_data = {}
        for sample in self.samples:
            for cpu_name, data in sample.items():
                if cpu_name not in cpu_data:
                    cpu_data[cpu_name] = {"usage": []}
                    if self.include_breakdown:
                        cpu_data[cpu_name]["breakdown"] = {k: [] for k in ("user", "system", "iowait", "irq", "softirq")}
                cpu_data[cpu_name]["usage"].append(data["usage"])
                if self.include_breakdown and "breakdown" in data:
                    for k, v in data["breakdown"].items():
                        cpu_data[cpu_name]["breakdown"][k].append(v)

        timestamps_rounded = [round(ts, 2) for ts in self.timestamps]
        result = {"duration": len(self.timestamps), "samples": len(self.samples), "cpus": {}}

        for cpu_name, data in cpu_data.items():
            usage_stats = self.get_stats(data["usage"])
            if include_timeseries:
                usage_stats["timeseries"] = {"timestamps": timestamps_rounded, "percent": data["usage"]}
            result["cpus"][cpu_name] = {"usage": usage_stats}

            if self.include_breakdown:
                result["cpus"][cpu_name]["breakdown"] = {}
                for k, values in data["breakdown"].items():
                    stats = self.get_stats(values)
                    if include_timeseries:
                        stats["timeseries"] = {"timestamps": timestamps_rounded, "percent": values}
                    result["cpus"][cpu_name]["breakdown"][k] = stats

        online_cpus = run_cmd("cat /sys/devices/system/cpu/online")
        numbers = re.findall(r"\d+", online_cpus) if online_cpus else []
        system_max_cpu = max(int(n) for n in numbers) if numbers else 0

        offline_cpus = [
            cpu_id for cpu_id in range(system_max_cpu + 1)
            if run_cmd(f"cat /sys/devices/system/cpu/cpu{cpu_id}/online 2>/dev/null") == "0"
        ]

        result["system_max_cpu"] = system_max_cpu
        result["offline_cpus"] = offline_cpus
        result["isolated_cpus"] = run_cmd("cat /sys/devices/system/cpu/isolated")
        return result


class ContextSwitchCollector(BaseCollector):
    """Monitor context switches per CPU"""

    def __init__(self, cpu_filter: Optional[List[int]] = None):
        super().__init__()
        self.cpu_filter = cpu_filter
        self.prev_ctxt = {}

    def collect_sample(self) -> Optional[Dict[str, Any]]:
        out = nsenter("cat /proc/stat")
        if not out:
            return None

        sample = {}
        for line in out.split("\n"):
            if line.startswith("ctxt"):
                sample["total"] = int(line.split()[1])

        if self.cpu_filter:
            for cpu_id in self.cpu_filter:
                schedstat = nsenter(f"cat /proc/schedstat | grep 'cpu{cpu_id} ' 2>/dev/null")
                if schedstat:
                    parts = schedstat.split()
                    if len(parts) >= 8:
                        sample[f"cpu{cpu_id}"] = int(parts[7])

        if self.prev_ctxt:
            for key, value in sample.items():
                if key in self.prev_ctxt:
                    sample[f"{key}_rate"] = value - self.prev_ctxt[key]

        self.prev_ctxt = dict(sample)
        return sample

    def aggregate(self, include_timeseries: bool = True) -> Dict[str, Any]:
        if not self.samples:
            return {"error": "no samples collected"}

        ctxt_data: Dict[str, list] = {}
        for sample in self.samples:
            for key, value in sample.items():
                ctxt_data.setdefault(key, []).append(value)

        timestamps_rounded = [round(ts, 2) for ts in self.timestamps]
        result = {"duration": len(self.timestamps), "samples": len(self.samples), "context_switches": {}}

        for key, values in ctxt_data.items():
            stats = self.get_stats(values)
            if include_timeseries:
                stats["timeseries"] = {"timestamps": timestamps_rounded, "count": values}
            result["context_switches"][key] = stats

        return result


class CPUGovernorCollector:
    """Check CPU governor settings (static, one-shot)"""

    @staticmethod
    def get_governor() -> Dict[str, Any]:
        out = run_cmd(
            "nsenter -t 1 -m -u -n -i bash -c "
            "'cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | sort -u'"
        )
        return {"governors": out.split("\n") if out else None}


class CPUIdleCollector:
    """Check CPU idle states (static, one-shot)"""

    @staticmethod
    def get_idle_states() -> Dict[str, Any]:
        out = run_cmd(
            "nsenter -t 1 -m -u -n -i bash -c "
            "'cat /sys/devices/system/cpu/cpu0/cpuidle/state*/disable'"
        )
        return {"idle_states_disabled": out.split("\n") if out else None}


class IRQAffinityCollector:
    """Check IRQ affinity (static, one-shot)"""

    @staticmethod
    def get_affinity(pattern: str = "ens") -> Dict[str, Any]:
        cmd = (
            f"nsenter -t 1 -m -u -n -i bash -c "
            f"'grep -H . /proc/irq/*/smp_affinity_list | grep {pattern}'"
        )
        out = run_cmd(cmd)
        if not out:
            return {"irq_affinity": {}}

        affinities = {}
        for line in out.split("\n"):
            if ":" in line:
                parts = line.split(":")
                irq_num = parts[0].split("/")[3]
                affinities[irq_num] = parts[1]
        return {"irq_affinity": affinities}


# ============================================================================
# ROUTES
# ============================================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/cpu/monitor", methods=["POST"])
def cpu_monitor():
    """Monitor per-core CPU usage with optional breakdown"""
    data = request.json or {}
    collector = CPUCollector(include_breakdown=data.get("breakdown", False))
    result = collector.gather(
        data.get("duration", 10),
        include_timeseries=data.get("include_timeseries", True),
    )
    return jsonify(result)


@app.route("/cpu/governor", methods=["GET"])
def cpu_governor():
    """Get CPU frequency governor"""
    return jsonify(CPUGovernorCollector.get_governor())


@app.route("/cpu/idle_states", methods=["GET"])
def cpu_idle_states():
    """Get CPU idle state configuration"""
    return jsonify(CPUIdleCollector.get_idle_states())


@app.route("/cpu/context_switches", methods=["POST"])
def context_switches_monitor():
    """Monitor context switches over time"""
    data = request.json or {}
    collector = ContextSwitchCollector(cpu_filter=data.get("cpu_filter"))
    result = collector.gather(data.get("duration", 10))
    return jsonify(result)


@app.route("/irq/affinity", methods=["GET"])
def irq_affinity():
    """Get IRQ affinity for network interfaces"""
    return jsonify(IRQAffinityCollector.get_affinity(request.args.get("pattern", "ens")))


# ============================================================================
# PLOT HELPERS
# ============================================================================

def _sort_cpu_keys(keys: List[str]) -> List[str]:
    """Sort cpu0, cpu1 ... cpu47 numerically, 'cpu' (aggregate) last"""
    def _key(k):
        m = re.match(r"^cpu(\d+)$", k)
        return (0, int(m.group(1))) if m else (1, k)
    return sorted(keys, key=_key)


def _build_heatmap_png(cpu_data: Dict[str, Any], title: str) -> io.BytesIO:
    """
    Per-core CPU usage heatmap (min / avg / max).
    Expects cpu_data = { "cpu0": {"usage": {"min":…, "avg":…, "max":…}}, … }
    """
    sorted_keys = _sort_cpu_keys([k for k in cpu_data if k != "cpu"])

    rows, labels = [], []
    for cpu_name in sorted_keys:
        u = cpu_data[cpu_name]["usage"]
        rows.append([u.get("min", 0), u.get("avg", 0), u.get("max", 0)])
        labels.append(cpu_name)

    df = pd.DataFrame(rows, index=labels, columns=["min %", "avg %", "max %"])

    fig_h = max(6, len(labels) * 0.35)
    fig, ax = plt.subplots(figsize=(8, fig_h))
    sns.heatmap(df, annot=True, fmt=".1f", cmap="YlOrRd",
                cbar_kws={"label": "CPU %"}, ax=ax)
    ax.set_title(title)
    ax.set_xlabel("Metric")
    ax.set_ylabel("CPU Core")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def _build_timeseries_png(cpu_data: Dict[str, Any], title: str) -> io.BytesIO:
    """
    Per-core CPU usage timeseries line chart.
    Expects timeseries key inside each cpu's usage dict.
    """
    sorted_keys = _sort_cpu_keys([k for k in cpu_data if k != "cpu"])

    # Collect series that actually have timeseries data
    series = {}
    timestamps = None
    for cpu_name in sorted_keys:
        ts_data = cpu_data[cpu_name]["usage"].get("timeseries")
        if ts_data:
            if timestamps is None:
                raw_ts = ts_data["timestamps"]
                # Normalise to seconds-since-start
                t0 = raw_ts[0]
                timestamps = [round(t - t0, 2) for t in raw_ts]
            series[cpu_name] = ts_data["percent"]

    if not series or timestamps is None:
        raise ValueError("No timeseries data found in JSON")

    fig, ax = plt.subplots(figsize=(14, 6))
    cmap = plt.get_cmap("tab20")
    for i, (cpu_name, values) in enumerate(series.items()):
        ax.plot(timestamps, values, label=cpu_name,
                color=cmap(i % 20), linewidth=1)

    ax.set_title(title)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("CPU Usage (%)")
    ax.set_ylim(0, 100)
    ax.legend(loc="upper right", fontsize=7, ncol=4)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def _build_combined_png(cpu_data: Dict[str, Any], label: str) -> io.BytesIO:
    """Combine heatmap + timeseries side-by-side into one PNG"""
    sorted_keys = _sort_cpu_keys([k for k in cpu_data if k != "cpu"])

    # --- heatmap data ---
    rows, hlabels = [], []
    for cpu_name in sorted_keys:
        u = cpu_data[cpu_name]["usage"]
        rows.append([u.get("min", 0), u.get("avg", 0), u.get("max", 0)])
        hlabels.append(cpu_name)
    df = pd.DataFrame(rows, index=hlabels, columns=["min %", "avg %", "max %"])

    # --- timeseries data ---
    series, timestamps = {}, None
    for cpu_name in sorted_keys:
        ts_data = cpu_data[cpu_name]["usage"].get("timeseries")
        if ts_data:
            if timestamps is None:
                t0 = ts_data["timestamps"][0]
                timestamps = [round(t - t0, 2) for t in ts_data["timestamps"]]
            series[cpu_name] = ts_data["percent"]

    has_ts = bool(series and timestamps)
    fig_h = max(8, len(sorted_keys) * 0.35)
    fig, axes = plt.subplots(1, 2 if has_ts else 1,
                             figsize=(20 if has_ts else 9, fig_h))
    if not has_ts:
        axes = [axes]

    # heatmap
    sns.heatmap(df, annot=True, fmt=".1f", cmap="YlOrRd",
                cbar_kws={"label": "CPU %"}, ax=axes[0])
    axes[0].set_title(f"CPU Usage Heatmap — {label}")
    axes[0].set_xlabel("Metric")
    axes[0].set_ylabel("CPU Core")

    # timeseries
    if has_ts:
        cmap = plt.get_cmap("tab20")
        for i, (cpu_name, values) in enumerate(series.items()):
            axes[1].plot(timestamps, values, label=cpu_name,
                         color=cmap(i % 20), linewidth=1)
        axes[1].set_title(f"CPU Usage Timeseries — {label}")
        axes[1].set_xlabel("Time (s)")
        axes[1].set_ylabel("CPU Usage (%)")
        axes[1].set_ylim(0, 100)
        axes[1].legend(loc="upper right", fontsize=7, ncol=4)
        axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


# ============================================================================
# PLOT ROUTE
# ============================================================================

@app.route("/cpu/plot", methods=["POST"])
def cpu_plot():
    """
    Generate CPU usage plots from existing JSON data.

    POST body (JSON):
    {
        "data":  { ...output from /cpu/monitor... },   # required
        "type":  "heatmap" | "timeseries" | "both",   # default: "both"
        "label": "optional title suffix"               # default: current timestamp
    }

    Returns: image/png
    """
    body = request.json or {}

    cpu_json = body.get("data")
    if not cpu_json:
        return jsonify({"error": "missing 'data' field"}), 400

    cpu_data = cpu_json.get("cpus")
    if not cpu_data:
        return jsonify({"error": "'data' must contain a 'cpus' key (output of /cpu/monitor)"}), 400

    plot_type = body.get("type", "both")
    label = body.get("label") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        if plot_type == "heatmap":
            buf = _build_heatmap_png(cpu_data, f"CPU Usage Heatmap — {label}")
        elif plot_type == "timeseries":
            buf = _build_timeseries_png(cpu_data, f"CPU Usage Timeseries — {label}")
        else:  # "both"
            buf = _build_combined_png(cpu_data, label)
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        return jsonify({"error": f"plot generation failed: {e}"}), 500

    return send_file(buf, mimetype="image/png")


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
