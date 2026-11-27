"""Measures Cuivar Pulse's resource use for a while and prints one JSON line.

    python3 perf_sample.py SECONDS

  plugin_cpu    CPU used by the plugin and everything it runs (ffmpeg, pw-cat,
                yt-dlp...), in % of ONE core (the Legion Go S has 8 cores / 16 threads)
  plugin_ram    memory of the plugin process and its helpers (PSS, MB): average and peak
  ui_cpu        Steam's UI (steamwebhelper, where the panel and Pixel TV draw), % of one core
  system_cpu    the whole device, % of all cores
  power_w       whole-device power draw from the battery (only while unplugged)
  download_mb   network data received by the whole device
"""

import json
import os
import sys
import time

TICK = os.sysconf("SC_CLK_TCK")


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def processes():
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        stat = read(f"/proc/{pid}/stat")
        if not stat:
            continue
        name = stat[stat.index("(") + 1:stat.rindex(")")]
        fields = stat[stat.rindex(")") + 2:].split()
        out[int(pid)] = {
            "name": name, "ppid": int(fields[1]),
            "own": int(fields[11]) + int(fields[12]),              # utime + stime
            "reaped": int(fields[13]) + int(fields[14]),           # cutime + cstime (finished children)
            "cmd": read(f"/proc/{pid}/cmdline").replace("\0", " "),
        }
    return out


def plugin_tree(procs):
    root = next((pid for pid, p in procs.items() if p["cmd"].startswith("Cuivar Pulse (")), None)
    if root is None:
        return None, []
    tree, frontier = [root], [root]
    while frontier:
        frontier = [pid for pid, p in procs.items() if p["ppid"] in frontier]
        tree += frontier
    return root, tree


def pss_mb(pid):
    for line in read(f"/proc/{pid}/smaps_rollup").splitlines():
        if line.startswith("Pss:"):
            return int(line.split()[1]) / 1024
    for line in read(f"/proc/{pid}/status").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def system_ticks():
    fields = [int(x) for x in read("/proc/stat").splitlines()[0].split()[1:]]
    return sum(fields), fields[3] + fields[4]   # total, idle + iowait


def power_w():
    base = "/sys/class/power_supply/BAT0"
    if read(f"{base}/status").strip() != "Discharging":
        return None
    power = read(f"{base}/power_now").strip()
    if power and int(power) > 0:
        return int(power) / 1e6
    current, voltage = read(f"{base}/current_now").strip(), read(f"{base}/voltage_now").strip()
    return int(current) * int(voltage) / 1e12 if current and voltage else None


def rx_bytes():
    total = 0
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, data = line.split(":", 1)
        if name.strip() != "lo":
            total += int(data.split()[0])
    return total


def snapshot():
    procs = processes()
    root, tree = plugin_tree(procs)
    plugin = 0
    if root is not None:
        plugin = procs[root]["own"] + procs[root]["reaped"] + sum(procs[p]["own"] for p in tree[1:])
    ui = sum(p["own"] for p in procs.values() if p["name"].startswith("steamwebhelpe"))
    helpers = sorted({procs[p]["name"] for p in tree[1:]})
    ram = sum(pss_mb(p) for p in tree) if tree else 0.0
    return {"plugin": plugin, "ui": ui, "sys": system_ticks(), "ram": ram, "helpers": helpers,
            "power": power_w(), "rx": rx_bytes(), "t": time.monotonic()}


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30
    first = previous = snapshot()
    plugin_ticks = ui_ticks = 0
    rams, powers, helpers = [first["ram"]], [], set(first["helpers"])
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(1)
        now = snapshot()
        plugin_ticks += max(0, now["plugin"] - previous["plugin"])
        ui_ticks += max(0, now["ui"] - previous["ui"])
        rams.append(now["ram"])
        helpers |= set(now["helpers"])
        if now["power"] is not None:
            powers.append(now["power"])
        previous = now
    elapsed = previous["t"] - first["t"]
    total = previous["sys"][0] - first["sys"][0]
    busy = total - (previous["sys"][1] - first["sys"][1])
    print(json.dumps({
        "seconds": round(elapsed),
        "plugin_cpu": round(100 * plugin_ticks / TICK / elapsed, 1),
        "plugin_cpu_seconds": round(plugin_ticks / TICK, 1),
        "plugin_ram_avg": round(sum(rams) / len(rams), 1),
        "plugin_ram_max": round(max(rams), 1),
        "ui_cpu": round(100 * ui_ticks / TICK / elapsed, 1),
        "system_cpu": round(100 * busy / total, 1) if total else None,
        "power_w": round(sum(powers) / len(powers), 2) if powers else None,
        "download_mb": round((previous["rx"] - first["rx"]) / 1e6, 2),
        "helpers": sorted(helpers),
    }))


if __name__ == "__main__":
    main()
