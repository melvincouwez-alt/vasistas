"""Banc de référence : coût au repos et latence, pour juger chaque réglage sur des chiffres.

`vasistas bench [--idle S] [--latency N] [--label TEXTE]`
- repos : processeur de QEMU (fils vCPU / autres) et de l'hôte GTK, mémoire résidente ;
- latence : touche envoyée -> première zone modifiée de l'écran, dans le Bloc-notes
  (ouvert pour l'occasion puis refermé s'il ne l'était pas).
Chaque mesure est ajoutée à docs/perf.jsonl (une ligne JSON) avec son étiquette.
"""

import json
import os
import time
from pathlib import Path

from . import control, vm

PERF_LOG = vm.REPO / "docs" / "perf.jsonl"
HZ = os.sysconf("SC_CLK_TCK")


def _threads(pid):
    """{tid: (nom, ticks)} des fils d'un processus."""
    out = {}
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return out
    for tid in tids:
        try:
            s = Path(f"/proc/{pid}/task/{tid}/stat").read_text()
        except OSError:
            continue
        name = s[s.index("(") + 1:s.rindex(")")]
        f = s.rsplit(")", 1)[1].split()
        out[tid] = (name, int(f[11]) + int(f[12]))
    return out


def _rss_mb(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


KVM_KEYS = ("exits", "halt_exits", "irq_injections", "insn_emulation", "mmio_exits", "io_exits")


def _kvm_stats():
    """Compteurs KVM cumulés des vCPU (QMP query-stats), ou {} si indisponibles."""
    try:
        q = vm.Qmp()
        try:
            res = q.execute("query-stats", {"target": "vcpu", "providers": [{"provider": "kvm"}]})
        finally:
            q.close()
    except (OSError, EOFError, RuntimeError):
        return {}
    tot = {}
    for vcpu in res:
        for st in vcpu.get("stats", []):
            if st["name"] in KVM_KEYS and isinstance(st["value"], int):
                tot[st["name"]] = tot.get(st["name"], 0) + st["value"]
    return tot


def idle(seconds=20):
    """Processeur consommé au repos, en % d'un cœur, et sorties KVM par seconde."""
    qpid = vm.pid()
    hpid = control.request({"status": True}, timeout=5).get("pid")
    k_a = _kvm_stats()
    a_q, a_h = _threads(qpid) if qpid else {}, _threads(hpid) if hpid else {}
    time.sleep(seconds)
    b_q, b_h = _threads(qpid) if qpid else {}, _threads(hpid) if hpid else {}
    k_b = _kvm_stats()

    def pct(a, b, pred=lambda n: True):
        ticks = sum(v - a[t][1] for t, (n, v) in b.items() if t in a and pred(n))
        return round(ticks / HZ / seconds * 100, 1)

    return {
        "qemu_cpu": pct(a_q, b_q),
        "qemu_vcpu": pct(a_q, b_q, lambda n: n.startswith("CPU ")),
        "qemu_other": pct(a_q, b_q, lambda n: not n.startswith("CPU ")),
        "host_cpu": pct(a_h, b_h),
        "qemu_rss_mb": _rss_mb(qpid) if qpid else None,
        "host_rss_mb": _rss_mb(hpid) if hpid else None,
        "kvm_per_s": {k: round((k_b[k] - k_a.get(k, 0)) / seconds) for k in k_b},
        "seconds": seconds,
    }


def _notepad():
    for w in control.request({"windows": True}, timeout=5).get("windows", []):
        if "Bloc-notes" in (w.get("title") or "") and w.get("kind") != "popup":
            return w["id"]
    return None


IDLE_BEFORE_LATENCY_S = 120


def desktop_idle_s():
    """Secondes sans clavier ni souris sur tout le bureau (moniteur d'inactivité de Gala)."""
    import subprocess
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
                              "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                              "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"],
                             capture_output=True, text=True, timeout=3).stdout
        return int(out.strip("()\n ").split()[1].rstrip(",")) // 1000
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def latency(n=30, force=False):
    """Latence touche -> image dans le Bloc-notes. Le banc prend le premier plan et tape :
    refusé si quelqu'un s'est servi de Windows depuis moins de IDLE_BEFORE_LATENCY_S."""
    idle_s = desktop_idle_s()
    if idle_s is None:
        idle_s = control.request({"status": True}, timeout=5).get("idle_s") or 0
    if not force and idle_s < IDLE_BEFORE_LATENCY_S:
        return {"skipped": f"poste utilisé il y a {idle_s} s (banc de latence reporté)"}
    wid = _notepad()
    opened = False
    if wid is None:
        control.request({"send": {"t": "launch", "req": 0, "cmd": "notepad", "args": []}}, timeout=5)
        opened = True
        deadline = time.monotonic() + 20
        while wid is None and time.monotonic() < deadline:
            time.sleep(0.5)
            wid = _notepad()
        if wid is None:
            return {"error": "Bloc-notes introuvable"}
        time.sleep(2)
    try:
        return control.request({"bench": n, "wid": wid}, timeout=n * 2 + 30)
    finally:
        if opened:
            control.request({"send": {"t": "window.close", "id": wid}}, timeout=5)


def run(idle_s=20, latency_n=30, label="", force=False):
    res = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "label": label}
    if idle_s:
        res["idle"] = idle(idle_s)
    if latency_n:
        res["latency"] = latency(latency_n, force)
    try:
        res["host"] = control.request({"stats": True}, timeout=5)
    except OSError:
        pass
    PERF_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PERF_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")
    return res
