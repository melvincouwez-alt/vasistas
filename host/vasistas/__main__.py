"""Ligne de commande : vasistas vm install|start|stop|status, run, launch, open, files, fake-guest, desktop."""

import argparse
import logging
import os
import sys

# Rendu GTK en OpenGL : avec Vulkan, GTK peut prendre la carte NVIDIA alors que Gala affiche
# sur l'AMD, et l'import des objets de synchronisation échoue (erreur de protocole Wayland).
os.environ.setdefault("GSK_RENDERER", "gl")
# Carte graphique intégrée seulement : sans ces variables, libglvnd charge aussi l'EGL de
# NVIDIA (constaté le 2026-09-29 : /dev/nvidia0 ouvert par l'hôte), ce qui réveille la carte
# dédiée et coûte de la batterie.
_MESA_EGL = "/usr/share/glvnd/egl_vendor.d/50_mesa.json"
if os.path.exists(_MESA_EGL):
    os.environ.setdefault("__EGL_VENDOR_LIBRARY_FILENAMES", _MESA_EGL)
os.environ.setdefault("__GLX_VENDOR_LIBRARY_NAME", "mesa")
_RADEON_VK = "/usr/share/vulkan/icd.d/radeon_icd.json"
if os.path.exists(_RADEON_VK):
    os.environ.setdefault("VK_DRIVER_FILES", _RADEON_VK)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="vasistas")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_vm = sub.add_parser("vm", help="machine virtuelle")
    p_vm.add_argument("action", choices=["install", "start", "stop", "status"])
    p_vm.add_argument("--force", action="store_true", help="réinstaller par-dessus le disque existant")

    sub.add_parser("run", help="afficher les fenêtres de l'invité")
    p_launch = sub.add_parser("launch", help="lancer un programme Windows")
    p_launch.add_argument("command")
    p_launch.add_argument("args", nargs=argparse.REMAINDER)
    p_app = sub.add_parser("launch-app", help="lancer une application Windows connue par son identifiant")
    p_app.add_argument("app")
    p_app.add_argument("args", nargs=argparse.REMAINDER)
    p_open = sub.add_parser("open", help="ouvrir des fichiers Linux dans l'application Windows désignée")
    p_open.add_argument("files", nargs="+")
    p_files = sub.add_parser("files", help="extensions ouvertes dans Windows (list|apply|set EXT APP|unset EXT)")
    p_files.add_argument("action", choices=["list", "apply", "set", "unset"])
    p_files.add_argument("ext", nargs="?")
    p_files.add_argument("app", nargs="?")
    p_fold = sub.add_parser("folders", help="dossiers Windows reliés aux dossiers Linux (list|link|unlink)")
    p_fold.add_argument("action", choices=["list", "link", "unlink"])
    p_fold.add_argument("keys", nargs="*", help="documents downloads pictures music videos desktop")
    p_exec = sub.add_parser("exec", help="exécuter un script PowerShell dans l'invité (sortie affichée)")
    p_exec.add_argument("script", help="texte du script, ou @fichier.ps1")
    sub.add_parser("fake-guest", help="invité de test, sans VM")
    sub.add_parser("desktop", help="installer les lanceurs .desktop")
    sub.add_parser("companion", help="application compagnon (VM et applications du menu)")
    sub.add_parser("boot", help="démarrer la VM puis afficher ses fenêtres (ouverture de session)")
    p_var = sub.add_parser("variant", help="version d'une application (ex. outlook new|classic)")
    p_var.add_argument("app")
    p_var.add_argument("choice", nargs="?")
    p_bench = sub.add_parser("bench", help="mesurer le coût au repos et la latence (docs/perf.jsonl)")
    p_bench.add_argument("--idle", type=int, default=20, help="secondes de mesure au repos (0 : aucune)")
    p_bench.add_argument("--latency", type=int, default=30, help="touches pour la latence (0 : aucune)")
    p_bench.add_argument("--label", default="", help="étiquette de la mesure")
    p_bench.add_argument("--force", action="store_true", help="latence même si Windows sert en ce moment")
    p_win = sub.add_parser("windows", help="fenêtres de l'invité, suivies ou non, et pourquoi")
    p_win.add_argument("--all", action="store_true", help="aussi les fenêtres ignorées")
    p_slim = sub.add_parser("slim", help="alléger Windows (télémétrie, applis, services)")
    p_slim.add_argument("action", choices=["status", "apply", "restore", "snapshot", "rollback", "list"])
    p_slim.add_argument("keys", nargs="*", help="éléments, ou --level")
    p_slim.add_argument("--level", type=int, choices=[1, 2, 3])

    # Les options inconnues (GTK) passent à l'application
    args, rest = parser.parse_known_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.cmd == "vm":
        from . import vm
        if args.action == "install":
            return vm.install(force=args.force)
        if args.action == "start":
            vm.start()
        elif args.action == "stop":
            vm.stop()
        print(vm.status())
        return 0

    if args.cmd == "fake-guest":
        from . import fakeguest
        return fakeguest.main()

    if args.cmd == "companion":
        from . import companion
        return companion.main(rest)

    if args.cmd == "boot":
        from . import vm
        vm.start()
        argv = ["run"] + rest

    if args.cmd == "desktop":
        from . import desktop
        return desktop.install()

    if args.cmd == "files":
        from . import files
        if args.action == "apply":
            files.apply()
        elif args.action in ("set", "unset"):
            if not args.ext or (args.action == "set" and not args.app):
                print("usage : vasistas files set EXT APP | unset EXT", file=sys.stderr)
                return 2
            files.set_designation(args.ext, args.app if args.action == "set" else None)
        desig = files.designations()
        for app, name, exts in files.catalog(desig):
            on = [e for e in exts if desig.get(e) == app]
            off = [e for e in exts if desig.get(e) != app]
            print(f"{name} ({app}) : {' '.join('.' + e for e in on) or '-'}"
                  + (f"   [non désignées : {' '.join('.' + e for e in off)}]" if off else ""))
        return 0

    if args.cmd == "folders":
        from . import folders
        keys = args.keys or (folders.SUGGESTED if args.action == "link" else [])
        for k in keys:
            if k not in folders.BY_KEY:
                print(f"inconnu : {k} ({' '.join(folders.BY_KEY)})", file=sys.stderr)
                return 2
            try:
                res = folders.link(k) if args.action == "link" else folders.unlink(k)
                print(f"{k} -> {res}")
            except (RuntimeError, OSError) as e:
                print(f"{k} : {e}", file=sys.stderr)
        current = folders.status()
        for k, name, *_ in folders.FOLDERS:
            print(f"{name:16} {current.get(k)}   (Linux : {folders.linux_dir(k)})")
        return 0

    if args.cmd == "variant":
        from . import desktop
        if args.app not in desktop.VARIANTS:
            print(f"pas de variantes pour {args.app} ({', '.join(desktop.VARIANTS)})", file=sys.stderr)
            return 2
        choices = desktop.VARIANTS[args.app]
        if args.choice:
            if args.choice not in choices:
                print(f"choix possibles : {', '.join(choices)}", file=sys.stderr)
                return 2
            desktop.set_variant(args.app, args.choice)
        print(f"{args.app} : {desktop.variant(args.app)} (choix : {', '.join(choices)})")
        return 0

    if args.cmd == "slim":
        import json
        from . import slim
        if args.action == "list":
            for it in slim.CATALOG:
                mark = "" if it.get("reversible", True) else "  (définitif)"
                print(f"{it['level']} {it['key']:18} {it['title']}{mark}")
            return 0
        if args.action == "snapshot":
            print("instantané pris" if slim.snapshot() else "instantané déjà présent")
            return 0
        if args.action == "rollback":
            slim.rollback()
            print("disque revenu à l'instantané")
            return 0
        keys = args.keys or (slim.keys_for_level(args.level) if args.level else [])
        if args.action == "status":
            res = slim.status()
        else:
            if not keys:
                print("préciser des éléments ou --level", file=sys.stderr)
                return 2
            if args.action == "apply":
                slim.snapshot()
            res = getattr(slim, args.action)(keys)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 1 if res.get("errors") else 0

    if args.cmd == "bench":
        import json
        from . import benchsuite
        try:
            res = benchsuite.run(args.idle, args.latency, args.label, args.force)
        except OSError as e:
            print(f"hôte Vasistas injoignable ({e}) : lancer `vasistas run`", file=sys.stderr)
            return 2
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "windows":
        from . import control
        try:
            res = control.request({"explain": True, "all": args.all}, timeout=10)
        except OSError as e:
            print(f"hôte Vasistas injoignable ({e}) : lancer `vasistas run`", file=sys.stderr)
            return 2
        if res.get("error"):
            print(res["error"], file=sys.stderr)
            return 2
        for w in res.get("windows", []):
            x, y, ww, hh = w.get("rect", (0, 0, 0, 0))
            mark = {"tracked": "+", "ignored": " "}.get(w.get("state"), "?")
            print(f"{mark} {w.get('id', 0):>8x} {w.get('kind', '-'):7} {ww:>5}x{hh:<5} @{x},{y} "
                  f"{w.get('class', '')[:28]:28} {w.get('exe', '')[:16]:16} {w.get('why', '')}"
                  f"{'  [recouverte]' if w.get('occluded') else ''}  « {w.get('title', '')[:40]} »")
        return 0

    if args.cmd == "exec":
        from . import control
        script = args.script
        if script.startswith("@"):
            with open(script[1:], encoding="utf-8") as f:
                script = f.read()
        try:
            res = control.request({"exec": script})
        except OSError as e:
            print(f"hôte Vasistas injoignable ({e}) : lancer `vasistas run`", file=sys.stderr)
            return 2
        if res.get("error"):
            print(res["error"], file=sys.stderr)
            return 2
        out = res.get("out", "")
        sys.stdout.write(out if out.endswith("\n") or not out else out + "\n")
        return res.get("code", 1) & 0xFF

    from . import app
    return app.main(argv)


if __name__ == "__main__":
    sys.exit(main())
