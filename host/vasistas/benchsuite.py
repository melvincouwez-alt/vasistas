"""Banc de référence : coût au repos et latence, pour juger chaque réglage sur des chiffres.

`vasistas bench [--idle S] [--latency N] [--occluded N] [--dwm S] [--label TEXTE]`
- repos : processeur de QEMU (fils vCPU / autres) et de l'hôte GTK, mémoire résidente ;
- latence : touche envoyée -> première zone modifiée de l'écran, dans le Bloc-notes
  (ouvert pour l'occasion puis refermé s'il ne l'était pas) ;
- dwm : compositions par seconde du bureau de Windows (DwmFlush) ;
- occluded : latence d'une fenêtre recouverte dans Windows (console derrière le Bloc-notes).
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


def _window(fragment):
    """Fenêtre suivie dont le titre contient `fragment` (hors menus), ou None."""
    for w in control.request({"windows": True}, timeout=5).get("windows", []):
        if fragment.lower() in (w.get("title") or "").lower() and w.get("kind") != "popup":
            return w
    return None


def _open(cmd, args, fragment, timeout=20):
    """(fenêtre, ouverte par nous) : lancée si aucune fenêtre au titre `fragment` n'est suivie."""
    w = _window(fragment)
    if w is not None:
        return w, False
    control.request({"send": {"t": "launch", "req": 0, "cmd": cmd, "args": args}}, timeout=5)
    deadline = time.monotonic() + timeout
    while w is None and time.monotonic() < deadline:
        time.sleep(0.5)
        w = _window(fragment)
    if w is None:
        raise RuntimeError(f"{fragment} introuvable")
    time.sleep(2)
    return w, True


DWM_PS = r"""
Add-Type @"
using System;using System.Diagnostics;using System.Runtime.InteropServices;using System.Threading;
public static class Dwm {
 [DllImport("dwmapi.dll")] static extern int DwmFlush();
 [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr a, int x,int y,int w,int hh,uint f);
 public static double Run(long hh, int secs){
  var h=new IntPtr(hh); bool stop=false;
  var t=new Thread(()=>{int i=0; while(!stop){ SetWindowPos(h,IntPtr.Zero,100+(i%%200),100+(i%%100),900,600,0x14); i+=7; Thread.Sleep(4);} });
  t.Start(); DwmFlush(); int n=0; var sw=Stopwatch.StartNew();
  while(sw.Elapsed.TotalSeconds<secs){ DwmFlush(); n++; }
  stop=true; t.Join();
  return n/sw.Elapsed.TotalSeconds;
 }}
"@
[Dwm]::Run(%d, %d).ToString("F1", [Globalization.CultureInfo]::InvariantCulture)
"""


def dwm_rate(wid, secs=4):
    """Compositions de DWM par seconde pendant qu'une fenêtre bouge sans arrêt (DwmFlush)."""
    res = control.request({"exec": DWM_PS % (wid, secs)}, timeout=secs + 60)
    try:
        return float((res.get("out") or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def occluded(notepad_wid, n=10):
    """Latence d'une fenêtre recouverte dans Windows : console classique (cmd) posée derrière le
    Bloc-notes, caractère posté par l'agent (sans premier plan) -> image reçue par les tuiles.
    La console est plus haute que le Bloc-notes pour rester en partie visible sous Linux, sinon
    l'agent cesse de la capturer (hosthidden)."""
    w, opened = _open("conhost.exe", ["cmd.exe"], "conhost")
    wid = w["id"]
    try:
        for msg in ({"t": "window.place", "id": wid, "x": 400, "y": 100, "w": 1000, "h": 1300},
                    {"t": "window.place", "id": notepad_wid, "x": 0, "y": 0, "w": 1400, "h": 900},
                    {"t": "window.activate", "id": notepad_wid}):
            control.request({"send": msg}, timeout=5)
        time.sleep(2)
        return control.request({"bench_occluded": {"wid": wid, "n": n}}, timeout=n * 4 + 10)
    finally:
        if opened:
            control.request({"send": {"t": "window.close", "id": wid}}, timeout=5)


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


def latency(n=30, force=False, occluded_n=0, dwm_s=0):
    """Latence touche -> image dans le Bloc-notes, cadence de DWM et latence d'une fenêtre
    recouverte. Le banc prend le premier plan et tape : refusé si quelqu'un s'est servi du poste
    depuis moins de IDLE_BEFORE_LATENCY_S."""
    idle_s = desktop_idle_s()
    if idle_s is None:
        idle_s = control.request({"status": True}, timeout=5).get("idle_s") or 0
    if not force and idle_s < IDLE_BEFORE_LATENCY_S:
        return {"skipped": f"poste utilisé il y a {idle_s} s (banc de latence reporté)"}
    w, opened = _open("notepad", [], "Bloc-notes")
    wid = w["id"]
    try:
        res = {}
        if n:
            res = control.request({"bench": n, "wid": wid}, timeout=n * 2 + 30)
        if dwm_s:
            res["dwm_per_s"] = dwm_rate(wid, dwm_s)
        if occluded_n:
            res["occluded"] = occluded(wid, occluded_n)
        return res
    finally:
        if opened:
            control.request({"send": {"t": "window.close", "id": wid}}, timeout=5)


def run(idle_s=20, latency_n=30, label="", force=False, occluded_n=0, dwm_s=0):
    res = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "label": label}
    if idle_s:
        res["idle"] = idle(idle_s)
    if latency_n or occluded_n or dwm_s:
        res["latency"] = latency(latency_n, force, occluded_n, dwm_s)
    try:
        res["host"] = control.request({"stats": True}, timeout=5)
    except OSError:
        pass
    PERF_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PERF_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")
    return res
