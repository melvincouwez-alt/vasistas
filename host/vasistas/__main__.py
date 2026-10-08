"""Ligne de commande : vasistas vm install|start|stop|status, run, launch, open, files, fake-guest, desktop,
indicator, restore, diagnose."""

import argparse
import logging
import os
import sys

from .i18n import _

# Rendu GTK en Vulkan (radv) : 24 à 26 ms au lieu de 38 à 40 en GL sur la fenêtre active
# (docs/performances-affichage.md, étape 5). VK_DRIVER_FILES plus bas le tient sur l'AMD : sans
# lui, GTK pouvait prendre la carte NVIDIA alors que Gala affiche sur l'AMD. GSK_RENDERER=gl
# dans l'environnement revient à l'ancien rendu, comme `renderer` dans config.json (onglet Avancé).
def _renderer():
    import json
    from .vm import CONFIG
    try:
        choice = json.loads(CONFIG.read_text()).get("renderer")
    except (OSError, ValueError):
        choice = None
    if choice in ("vulkan", "gl"):
        return choice
    return "vulkan" if os.path.exists("/usr/share/vulkan/icd.d/radeon_icd.json") else "gl"


os.environ.setdefault("GSK_RENDERER", _renderer())
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

    p_vm = sub.add_parser("vm", help=_("machine virtuelle"))
    p_vm.add_argument("action", choices=["install", "start", "stop", "status"])
    p_vm.add_argument("--force", action="store_true", help=_("réinstaller par-dessus le disque existant"))

    sub.add_parser("run", help=_("afficher les fenêtres de l'invité"))
    p_launch = sub.add_parser("launch", help=_("lancer un programme Windows"))
    p_launch.add_argument("command")
    p_launch.add_argument("args", nargs=argparse.REMAINDER)
    p_app = sub.add_parser("launch-app", help=_("lancer une application Windows connue par son identifiant"))
    p_app.add_argument("app")
    p_app.add_argument("args", nargs=argparse.REMAINDER)
    p_open = sub.add_parser("open", help=_("ouvrir des fichiers Linux dans l'application Windows désignée"))
    p_open.add_argument("files", nargs="+")
    p_files = sub.add_parser("files", help=_("extensions ouvertes dans Windows (list|apply|set EXT APP|unset EXT)"))
    p_files.add_argument("action", choices=["list", "apply", "set", "unset"])
    p_files.add_argument("ext", nargs="?")
    p_files.add_argument("app", nargs="?")
    p_fold = sub.add_parser("folders", help=_("dossiers Windows reliés aux dossiers Linux (list|link|unlink)"))
    p_fold.add_argument("action", choices=["list", "link", "unlink"])
    p_fold.add_argument("keys", nargs="*", help=_("documents downloads pictures music videos desktop"))
    p_exec = sub.add_parser("exec", help=_("exécuter un script PowerShell dans l'invité (sortie affichée)"))
    p_exec.add_argument("script", help=_("texte du script, ou @fichier.ps1"))
    sub.add_parser("fake-guest", help=_("invité de test, sans VM"))
    sub.add_parser("desktop", help=_("installer les lanceurs .desktop"))
    sub.add_parser("companion", help=_("application compagnon (VM et applications du menu)"))
    sub.add_parser("indicator", help=_("indicateur dans le panneau (état de Windows, applications, commandes)"))
    p_boot = sub.add_parser("boot", help=_("démarrer la VM puis afficher ses fenêtres (ouverture de session)"))
    p_boot.add_argument("--sleep", action="store_true", help=_("mettre Windows en veille dès que Windows est prêt"))
    p_var = sub.add_parser("variant", help=_("version d'une application (ex. outlook new|classic)"))
    p_var.add_argument("app")
    p_var.add_argument("choice", nargs="?")
    p_bench = sub.add_parser("bench", help=_("mesurer le coût au repos et la latence (docs/perf.jsonl)"))
    p_bench.add_argument("--idle", type=int, default=20, help=_("secondes de mesure au repos (0 : aucune)"))
    p_bench.add_argument("--latency", type=int, default=30, help=_("nombre de frappes pour mesurer la latence (0 : "
                                                                   "aucune)"))
    p_bench.add_argument("--occluded", type=int, default=0, help=_("nombre de frappes pour mesurer la latence d'une "
                                                                   "fenêtre recouverte (0 : aucune)"))
    p_bench.add_argument("--dwm", type=int, default=0, help=_("secondes de mesure de la cadence de DWM (0 : aucune)"))
    p_bench.add_argument("--label", default="", help=_("étiquette de la mesure"))
    p_bench.add_argument("--force", action="store_true", help=_("mesurer la latence même si Windows est en cours "
                                                                "d'utilisation"))
    sub.add_parser("reset-windows", help=_("remettre toutes les fenêtres Windows centrées sur leur écran"))
    p_win = sub.add_parser("windows", help=_("fenêtres de l'invité, suivies ou non, et pourquoi"))
    p_win.add_argument("--all", action="store_true", help=_("afficher aussi les fenêtres ignorées"))
    p_slim = sub.add_parser("slim", help=_("alléger Windows (télémétrie, applis, services)"))
    p_slim.add_argument("action", choices=["status", "apply", "restore", "snapshot", "rollback", "list"])
    p_slim.add_argument("keys", nargs="*", help=_("éléments à alléger, ou option --level"))
    p_slim.add_argument("--level", type=int, choices=[1, 2, 3])
    p_rest = sub.add_parser("restore", help=_("points de restauration du disque de Windows"))
    p_rest.add_argument("action", choices=["list", "create", "revert", "delete"])
    p_rest.add_argument("name", nargs="?", help=_("nom du point (create : facultatif)"))
    p_rest.add_argument("--yes", action="store_true", help=_("exécuter revert sans demander de confirmation"))
    p_diag = sub.add_parser("diagnose", help=_("vérifier l'ordinateur et Vasistas"))
    p_diag.add_argument("--report", action="store_true", help=_("rapport complet, anonymisé, à copier"))

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
        if args.sleep:
            from .sleep import BOOT_SLEEP_ENV
            os.environ[BOOT_SLEEP_ENV] = "1"  # lu par SleepManager
        vm.start()
        argv = ["run"] + rest

    if args.cmd == "indicator":
        from . import indicator
        return indicator.main()

    if args.cmd == "desktop":
        from . import desktop, indicator
        res = desktop.install()
        indicator.sync_autostart()
        return res

    if args.cmd == "files":
        from . import files
        if args.action == "apply":
            files.apply()
        elif args.action in ("set", "unset"):
            if not args.ext or (args.action == "set" and not args.app):
                print(_("usage : vasistas files set EXT APP | unset EXT"), file=sys.stderr)
                return 2
            files.set_designation(args.ext, args.app if args.action == "set" else None)
        desig = files.designations()
        for app, name, exts in files.catalog(desig):
            on = [e for e in exts if desig.get(e) == app]
            off = [e for e in exts if desig.get(e) != app]
            print(_("{name} ({app}) : {exts}", name=name, app=app, exts=' '.join('.' + e for e in on) or '-')
                  + (_("   [non désignées : {exts}]", exts=' '.join('.' + e for e in off)) if off else ""))
        return 0

    if args.cmd == "folders":
        from . import folders
        keys = args.keys or (folders.SUGGESTED if args.action == "link" else [])
        for k in keys:
            if k not in folders.BY_KEY:
                print(_("dossier inconnu : {key} (valeurs possibles : {keys})", key=k, keys=' '.join(folders.BY_KEY)),
                      file=sys.stderr)
                return 2
            try:
                res = folders.link(k) if args.action == "link" else folders.unlink(k)
                print(f"{k} -> {res}")
            except (RuntimeError, OSError) as e:
                print(f"{k} : {e}", file=sys.stderr)
        current = folders.status()
        for k, name, *_rest in folders.FOLDERS:
            print(f"{name:16} {current.get(k)}   (Linux : {folders.linux_dir(k)})")
        return 0

    if args.cmd == "variant":
        from . import desktop
        if args.app not in desktop.VARIANTS:
            print(_("aucune variante pour {app} (applications concernées : {apps})", app=args.app,
                    apps=', '.join(desktop.VARIANTS)), file=sys.stderr)
            return 2
        choices = desktop.VARIANTS[args.app]
        if args.choice:
            if args.choice not in choices:
                print(_("choix possibles : {choices}", choices=', '.join(choices)), file=sys.stderr)
                return 2
            desktop.set_variant(args.app, args.choice)
        print(_("{app} : {variant} (choix : {choices})", app=args.app, variant=desktop.variant(args.app),
                choices=', '.join(choices)))
        return 0

    if args.cmd == "slim":
        import json
        from . import slim
        if args.action == "list":
            for it in slim.CATALOG:
                mark = "" if it.get("reversible", True) else _("  (définitif)")
                print(f"{it['level']} {it['key']:18} {_(it['title'])}{mark}")
            return 0
        if args.action == "snapshot":
            print(_("instantané créé") if slim.snapshot() else _("instantané déjà présent"))
            return 0
        if args.action == "rollback":
            slim.rollback()
            print(_("disque de Windows restauré à l'instantané"))
            return 0
        keys = args.keys or (slim.keys_for_level(args.level) if args.level else [])
        if args.action == "status":
            res = slim.status()
        else:
            if not keys:
                print(_("précisez des éléments ou l'option --level"), file=sys.stderr)
                return 2
            if args.action == "apply":
                slim.snapshot()
            res = getattr(slim, args.action)(keys)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 1 if res.get("errors") else 0

    if args.cmd == "restore":
        return _restore(args)

    if args.cmd == "diagnose":
        from . import diagnose
        if args.report:
            sys.stdout.write(diagnose.report(diagnose.run_all()))
            return 0
        marks = {"ok": "✓", "warn": "!", "error": "✗"}

        def show(c):
            print(_("{mark} {title} : {detail}", mark=marks[c.state], title=c.title, detail=c.detail) + (f"\n    {c.command}" if c.command else ""))
        results = diagnose.run_all(on_result=show)
        return max({"ok": 0, "warn": 1, "error": 2}[c.state] for c in results)

    if args.cmd == "bench":
        import json
        from . import benchsuite
        try:
            res = benchsuite.run(args.idle, args.latency, args.label, args.force, args.occluded, args.dwm)
        except OSError as e:
            print(_("hôte Vasistas injoignable ({e}) : lancez `vasistas run`", e=e), file=sys.stderr)
            return 2
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "reset-windows":
        from . import control
        try:
            res = control.request({"reset_windows": True}, timeout=10)
        except OSError:
            print(_("Aucune fenêtre Windows ouverte : rien à réinitialiser."))
            return 0
        if res.get("error"):
            print(res["error"], file=sys.stderr)
            return 2
        print(_("{n} fenêtre(s) remise(s) en place.", n=res.get("count", 0)))
        return 0

    if args.cmd == "windows":
        from . import control
        try:
            res = control.request({"explain": True, "all": args.all}, timeout=10)
        except OSError as e:
            print(_("hôte Vasistas injoignable ({e}) : lancez `vasistas run`", e=e), file=sys.stderr)
            return 2
        if res.get("error"):
            print(res["error"], file=sys.stderr)
            return 2
        for w in res.get("windows", []):
            x, y, ww, hh = w.get("rect", (0, 0, 0, 0))
            mark = {"tracked": "+", "ignored": " "}.get(w.get("state"), "?")
            print(f"{mark} {w.get('id', 0):>8x} {w.get('kind', '-'):7} {ww:>5}x{hh:<5} @{x},{y} "
                  f"{w.get('class', '')[:28]:28} {w.get('exe', '')[:16]:16} {w.get('why', '')}"
                  f"{_('  [recouverte]') if w.get('occluded') else ''}  « {w.get('title', '')[:40]} »")
        return 0

    if args.cmd == "exec":
        from . import control
        script = args.script
        if script.startswith("@"):
            with open(script[1:], encoding="utf-8") as f:
                script = f.read()
        try:
            res = control.request({"exec": script}, timeout=600)
        except OSError as e:
            print(_("hôte Vasistas injoignable ({e}) : lancez `vasistas run`", e=e), file=sys.stderr)
            return 2
        if res.get("error"):
            print(res["error"], file=sys.stderr)
            return 2
        out = res.get("out", "")
        sys.stdout.write(out if out.endswith("\n") or not out else out + "\n")
        return res.get("code", 1) & 0xFF

    from . import app
    return app.main(argv)


def _restore(args):
    from . import restore
    try:
        if args.action == "list":
            pts = restore.points(with_sizes=True)
            for p in pts:
                kind = _("auto") if p["auto"] else _("manuel")
                size = restore.fmt_size(p["size"]) if p["size"] is not None else ""
                print(f"{p['tag']:24} {kind:7} {size:>9}  {p['label']}")
            if not pts:
                print(_("Aucun point de restauration."))
            print(_("Espace libre : {n} Go", n=f"{restore.free_bytes() / 1e9:.0f}"))
            return 0
        if args.action == "create":
            p = restore.create(name=args.name)
            print(_("Point créé : {label}", label=p["label"]))
            return 0
        if not args.name:
            print(_("Précisez le point de restauration (nom affiché par « vasistas restore list »)."), file=sys.stderr)
            return 2
        if args.action == "delete":
            p = restore.delete(args.name)
            print(_("Point de restauration supprimé : {label}", label=p["label"]))
            return 0
        p = restore.find(args.name)
        if not args.yes:
            print(_("Tout le disque de Windows va être restauré au point « {label} ». Les modifications faites dans "
                    "Windows depuis ce point (applications, réglages, fichiers hors dossiers partagés) seront perdues.",
                    label=p["label"]))
            if not sys.stdin.isatty() or input(_("Tapez « oui » pour continuer : ")).strip().lower() not in ("oui", "yes"):
                print(_("Annulé."))
                return 1
        restore.revert(p["tag"])
        print(_("Disque de Windows restauré au point : {label}", label=p["label"]))
        return 0
    except restore.RestoreError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
