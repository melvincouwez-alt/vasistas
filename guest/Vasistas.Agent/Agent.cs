using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;

namespace Vasistas.Agent
{
    sealed class Tracked
    {
        public IntPtr Hwnd;
        public uint Id;
        public string Title;
        public RECT Rect;
        public string Kind;
        public uint Owner;
        public bool Minimized;
        public bool Occluded; // une autre fenêtre suivie la recouvre en partie dans l'invité
        public bool HostHidden; // sa fenêtre Linux est réduite ou masquée : rien à capturer
        public readonly Capture Capture = new Capture();
        public long NextCapture;
        public int Unchanged;   // captures successives sans changement : l'intervalle s'allonge
        public int Hit = -1;
        public string Cursor;
    }

    sealed class Agent
    {
        const int Version = 1;
        // La liste des fenêtres suit les événements de Windows (SetWinEventHook) ; le balayage
        // périodique n'est plus qu'un filet de sécurité.
        const int FallbackScanMs = 500, MinScanGapMs = 8, SyncMs = 3000, IdleWaitMs = 100;
        const int ActiveMs = 16, IdleMs = 100;
        // fenêtre recouverte, en mode écran QEMU : PrintWindow coûte 30 à 90 ms, deux fois par seconde
        // suffit ; image inchangée plusieurs fois de suite : jusqu'à OccludedMaxMs entre deux captures
        const int OccludedMs = 500, OccludedMaxMs = 2000;
        static readonly HashSet<string> IgnoredClasses = new HashSet<string>
        {
            "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
            "Windows.UI.Core.CoreWindow", "IME", "MSCTFIME UI", "Shell_InputSwitchTopLevelWindow",
            "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland",
        };
        // Fenêtres visibles qui ne cachent rien : ombres et halos posés autour des fenêtres
        static readonly HashSet<string> NonOccluding = new HashSet<string>
        {
            "MSO_BORDEREFFECT_WINDOW_CLASS", "SysShadow", "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
        };

        readonly Channel ch = new Channel();
        readonly Dictionary<IntPtr, Tracked> windows = new Dictionary<IntPtr, Tracked>();
        readonly uint myPid = (uint)Process.GetCurrentProcess().Id;
        readonly Stopwatch clock = Stopwatch.StartNew();
        IntPtr hidden;
        IntPtr lastForeground;
        uint lastActiveId;
        readonly HashSet<string> iconsSent = new HashSet<string>(); // une icône par application et par hôte
        ClipboardSync clipboard;
        volatile bool framebuffer; // l'hôte lit l'écran dans QEMU : seules les fenêtres recouvertes sont capturées
        long nextScan, nextStats, lastStatsAt, nextSync, lastScanAt = -1000, lastHello = -10000;
        volatile bool scanDue;                 // un événement Windows demande un balayage
        bool verboseStats;                     // statistiques toutes les 2 s (hôte en -v), sinon toutes les minutes
        static Native.WinEventProc winEventProc; // gardé : le ramasse-miettes ne doit pas le libérer
        HoverWorker hover;
        readonly object sync = new object();
        readonly AutoResetEvent captureWake = new AutoResetEvent(false); // fenêtres suivies, partagées avec le fil de capture
        bool wasReady;
        readonly Dictionary<IntPtr, string> cursorNames = new Dictionary<IntPtr, string>();

        public void Run()
        {
            Native.SetProcessDpiAwarenessContext(new IntPtr(-4)); // PER_MONITOR_AWARE_V2
            // Fenêtre de l'agent, hors écran : prend le premier plan pour fermer menus et popups
            hidden = Native.CreateWindowEx((uint)Native.WS_EX_TOOLWINDOW, "STATIC", "Vasistas",
                0x80000000u | 0x10000000u, -32000, -32000, 1, 1, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
            InitCursors();
            clipboard = new ClipboardSync(Send);
            Log.Sink = msg => { if (ch.HostReady) ch.Send(new Dictionary<string, object> { { "t", "log" }, { "msg", msg } }); };
            ch.Start();
            hover = new HoverWorker(this);
            InstallHooks();
            new Thread(CaptureLoop) { IsBackground = true, Name = "vasistas-capture" }.Start();
            ShareGuard.Start();
            Log.Write("agent démarré, pid " + myPid);

            var handles = new[] { ch.InboxEvent.SafeWaitHandle.DangerousGetHandle() };
            int wait = 10;
            while (true)
            {
                Native.MsgWaitForMultipleObjects(1, handles, false, (uint)wait, Native.QS_ALLINPUT);
                while (Native.PeekMessage(out MSG m, IntPtr.Zero, 0, 0, Native.PM_REMOVE))
                {
                    Native.TranslateMessage(ref m);
                    Native.DispatchMessage(ref m);
                }
                Monitor.Enter(sync);
                try
                {
                Dictionary<string, object> pendingMove = null;
                while (ch.Inbox.TryDequeue(out var msg))
                {
                    // d'une rafale de mouvements, seul le dernier compte
                    if (Str(msg, "t") == "mouse.move")
                    {
                        pendingMove = msg;
                        continue;
                    }
                    if (pendingMove != null) { Dispatch(pendingMove); pendingMove = null; }
                    Dispatch(msg);
                }
                if (pendingMove != null) Dispatch(pendingMove);
                if (wasReady && !ch.HostReady)
                {
                    wasReady = false;
                    Log.Write("hôte perdu");
                }
                if (!ch.HostReady) { wait = IdleWaitMs; continue; }
                long now = clock.ElapsedMilliseconds;
                if ((scanDue && now - lastScanAt >= MinScanGapMs) || now >= nextScan)
                {
                    scanDue = false;
                    lastScanAt = now;
                    nextScan = now + FallbackScanMs;
                    try { Scan(); }
                    catch (Exception e) { Log.Write("scan : " + e); }
                }
                if (now >= nextSync)
                {
                    nextSync = now + SyncMs;
                    Send(new Dictionary<string, object> { { "t", "sync" }, { "ids", windows.Values.Select(w => (object)w.Id).ToArray() } });
                }
                try { clipboard.Poll(now); }
                catch (Exception e) { Log.Write("presse-papiers : " + e.Message); }
                if (ch.TakeOverflow())
                    foreach (var w in windows.Values) { w.Capture.Invalidate(); w.NextCapture = now + 200; }
                if (now >= nextStats)
                {
                    int period = verboseStats ? 2000 : 60000;
                    double secs = Math.Max(0.1, (now - lastStatsAt) / 1000.0);
                    lastStatsAt = now;
                    nextStats = now + period;
                    if (Stats.Grabs > 0 || Stats.KeysIn > 0 || Stats.Scans > 0) Send(new Dictionary<string, object> { { "t", "log" }, { "msg", Stats.TakeReport(secs) } });
                }
                // Réveil au plus tard pour le prochain balayage de secours, le presse-papiers
                // (IdleWaitMs) ou un balayage demandé mais trop proche du précédent.
                long due = Math.Min(nextScan, now + IdleWaitMs);
                if (scanDue) due = Math.Min(due, lastScanAt + MinScanGapMs);
                wait = (int)Math.Max(1, Math.Min(IdleWaitMs, due - clock.ElapsedMilliseconds));
                }
                finally { Monitor.Exit(sync); }
            }
        }

        void InstallHooks()
        {
            winEventProc = OnWinEvent;
            var ranges = new (uint, uint)[]
            {
                (Native.EVENT_SYSTEM_FOREGROUND, Native.EVENT_SYSTEM_FOREGROUND),
                (Native.EVENT_SYSTEM_MENUPOPUPSTART, Native.EVENT_SYSTEM_MENUPOPUPEND),
                (Native.EVENT_SYSTEM_MOVESIZEEND, Native.EVENT_SYSTEM_MOVESIZEEND),
                (Native.EVENT_SYSTEM_MINIMIZESTART, Native.EVENT_SYSTEM_MINIMIZEEND),
                (Native.EVENT_OBJECT_CREATE, Native.EVENT_OBJECT_REORDER),     // création, destruction, affichage, masquage, ordre
                (Native.EVENT_OBJECT_LOCATIONCHANGE, Native.EVENT_OBJECT_NAMECHANGE),
                (Native.EVENT_OBJECT_CLOAKED, Native.EVENT_OBJECT_UNCLOAKED),
            };
            int ok = 0;
            foreach (var (min, max) in ranges)
                if (Native.SetWinEventHook(min, max, IntPtr.Zero, winEventProc, 0, 0,
                        Native.WINEVENT_OUTOFCONTEXT | Native.WINEVENT_SKIPOWNPROCESS) != IntPtr.Zero) ok++;
            Log.Write($"événements Windows : {ok}/{ranges.Length} accroches");
        }

        /// <summary>
        /// Appelé par la boucle de messages du fil principal. Ne fait que demander un balayage :
        /// les rafales (déplacement, redimensionnement) sont regroupées par MinScanGapMs.
        /// </summary>
        void OnWinEvent(IntPtr hook, uint ev, IntPtr hwnd, int idObject, int idChild, uint thread, uint time)
        {
            if (idObject != Native.OBJID_WINDOW || idChild != 0 || hwnd == IntPtr.Zero) return;
            if (ev == Native.EVENT_OBJECT_LOCATIONCHANGE || ev == Native.EVENT_OBJECT_NAMECHANGE || ev == Native.EVENT_OBJECT_REORDER)
            {
                // très fréquents (contrôles enfants, curseur texte) : fenêtres de premier niveau seulement
                if (Native.GetAncestor(hwnd, Native.GA_ROOT) != hwnd) return;
                if (ev == Native.EVENT_OBJECT_NAMECHANGE && !windows.ContainsKey(hwnd)) return;
            }
            Stats.Events++;
            scanDue = true;
        }

        void RequestScan() => scanDue = true;

        /// <summary>
        /// Fil de capture : les captures lentes ne retardent plus la souris ni le clavier,
        /// traités par la boucle principale.
        /// </summary>
        void CaptureLoop()
        {
            var due = new List<(Tracked tw, bool fromScreen)>();
            while (true)
            {
                if (!ch.HostReady) { Thread.Sleep(20); continue; }
                long now = clock.ElapsedMilliseconds;
                due.Clear();
                long next = now + IdleMs;
                lock (sync)
                {
                    var fg = Native.GetForegroundWindow();
                    foreach (var tw in windows.Values)
                    {
                        if (tw.Minimized || tw.HostHidden) continue;
                        if (framebuffer && (tw.Kind == "popup" || !tw.Occluded)) continue;
                        if (now < tw.NextCapture) { next = Math.Min(next, tw.NextCapture); continue; }
                        bool fast = !framebuffer && (tw.Kind == "popup" || tw.Hwnd == fg || Native.GetAncestor(fg, Native.GA_ROOTOWNER) == tw.Hwnd);
                        int occludedMs = Math.Min(OccludedMaxMs, OccludedMs << Math.Min(2, tw.Unchanged / 4));
                        tw.NextCapture = now + (fast ? ActiveMs : framebuffer ? occludedMs : IdleMs);
                        next = Math.Min(next, tw.NextCapture);
                        // la taille a pu changer depuis le dernier scan : prévenir l'hôte avant les tuiles
                        RECT r = Native.Bounds(tw.Hwnd);
                        if (r.Width != tw.Rect.Width || r.Height != tw.Rect.Height) Update(tw);
                        due.Add((tw, tw.Kind == "popup" || (tw.Hwnd == fg && Uncovered(tw))));
                    }
                }
                foreach (var (tw, fromScreen) in due)
                {
                    try { tw.Unchanged = tw.Capture.Grab(tw.Hwnd, tw.Id, fromScreen, ch) ? 0 : tw.Unchanged + 1; }
                    catch (Exception e) { Log.Write("capture " + tw.Id + " : " + e.Message); }
                }
                int wait = (int)(next - clock.ElapsedMilliseconds);
                if (wait > 0) captureWake.WaitOne(Math.Min(wait, IdleMs));
            }
        }

        // -- messages de l'hôte --

        void Dispatch(Dictionary<string, object> msg)
        {
            try { Handle(msg); }
            catch (Exception e) { Log.Write("message " + Str(msg, "t") + " : " + e); }
        }

        void Handle(Dictionary<string, object> m)
        {
            string t = Str(m, "t");
            if (t == "hello" || t == "display")
            {
                // échelle de l'écran hôte : Windows dessine directement à la bonne taille
                if (m.TryGetValue("scale", out var sc) && sc != null)
                {
                    try { Display.Apply((int)Math.Round(Convert.ToDouble(sc) * 100)); }
                    catch (Exception e) { Log.Write("échelle : " + e.Message); }
                }
                if (m.TryGetValue("resolution", out var res) && res is object[] wh && wh.Length == 2)
                {
                    try { Display.SetResolution(Convert.ToInt32(wh[0]), Convert.ToInt32(wh[1])); }
                    catch (Exception e) { Log.Write("résolution : " + e.Message); }
                }
                if (m.TryGetValue("stats", out var sv) && sv is bool verbose) verboseStats = verbose;
                if (m.TryGetValue("framebuffer", out var fbv) && fbv is bool fb && fb != framebuffer)
                {
                    framebuffer = fb;
                    // l'hôte lit l'écran tel quel : le pointeur de Windows y serait visible en double
                    if (fb) Cursors.Hide(); else Cursors.Restore();
                }
                if (t == "display") return;
                long nowHello = clock.ElapsedMilliseconds;
                // l'hôte répète hello tant qu'il n'a pas de réponse : une rafale ne doit pas tout renvoyer
                bool repeat = ch.HostReady && nowHello - lastHello < 1500;
                lastHello = nowHello;
                if (repeat) return;
                ch.ClearQueue();
                ch.HostReady = true;
                wasReady = true;
                Send(new Dictionary<string, object>
                {
                    { "t", "hello" }, { "version", Version },
                    { "screen", new[] { Native.GetSystemMetrics(Native.SM_CXSCREEN), Native.GetSystemMetrics(Native.SM_CYSCREEN) } },
                    { "dpi", (int)Native.GetDpiForSystem() },
                });
                foreach (var w in windows.Values) w.Capture.Dispose();
                windows.Clear(); // tout renvoyer
                iconsSent.Clear();
                Scan();
                return;
            }
            if (t == "launch") { Launch(m); return; }
            if (t == "debug.windows") { Explain(m); return; }
            if (t == "resend")
            {
                // l'hôte a perdu ces fenêtres (message manqué) : les lui renvoyer
                if (m.TryGetValue("ids", out var ids) && ids is object[] list)
                    foreach (var o in list)
                    {
                        var r = Find(Convert.ToInt32(o));
                        if (r != null) { SendNew(r, false); r.Capture.Invalidate(); r.NextCapture = 0; }
                    }
                return;
            }
            if (t == "exec") { Exec(m); return; }
            if (t == "probe") { Probe(m); return; }
            if (t == "clipboard") { clipboard.Apply(m); return; }
            if (t == "key") { Stats.KeysIn++; Input.Key(m); return; }

            var tw = Find(Int(m, "id"));
            if (tw == null) return;
            switch (t)
            {
                case "mouse.move":
                    Input.MoveTo(tw.Rect.Left + Int(m, "x"), tw.Rect.Top + Int(m, "y"));
                    hover.Request(tw, tw.Rect.Left + Int(m, "x"), tw.Rect.Top + Int(m, "y"));
                    break;
                case "mouse.button":
                    // pas de remontée si le clic tombe dans un menu ouvert (affiché dans l'image de sa fenêtre)
                    if (Bool(m, "down") && tw.Kind != "popup" && !InPopup(tw.Rect.Left + Int(m, "x"), tw.Rect.Top + Int(m, "y")))
                        Raise(tw);
                    Input.MoveTo(tw.Rect.Left + Int(m, "x"), tw.Rect.Top + Int(m, "y"));
                    Input.Button(Int(m, "button"), Bool(m, "down"));
                    tw.NextCapture = 0;
                    tw.Unchanged = 0;
                    captureWake.Set();
                    break;
                case "mouse.wheel":
                    Input.MoveTo(tw.Rect.Left + Int(m, "x"), tw.Rect.Top + Int(m, "y"));
                    Input.Wheel(Int(m, "dx"), Int(m, "dy"));
                    if (tw.Occluded) { tw.NextCapture = 0; tw.Unchanged = 0; captureWake.Set(); }
                    break;
                case "window.activate":
                    if (Native.IsIconic(tw.Hwnd)) Native.ShowWindow(tw.Hwnd, Native.SW_RESTORE);
                    EnsureOnScreen(tw);
                    Native.ForceForeground(tw.Hwnd);
                    RequestScan();
                    break;
                case "window.hosthidden":
                    // fenêtre Linux réduite ou entièrement masquée (GTK « suspended ») : plus de
                    // PrintWindow pour elle ; image refaite tout de suite quand elle revient
                    tw.HostHidden = Bool(m, "hidden");
                    if (!tw.HostHidden) { tw.Capture.Invalidate(); tw.NextCapture = 0; tw.Unchanged = 0; captureWake.Set(); }
                    break;
                case "window.deactivate":
                    if (Native.GetForegroundWindow() != hidden) Native.ForceForeground(hidden);
                    break;
                case "window.close":
                    Native.PostMessage(tw.Hwnd, Native.WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
                    break;
                case "window.resize":
                    Resize(tw, Int(m, "w"), Int(m, "h"));
                    // nouvelle géométrie envoyée tout de suite : l'hôte ne découpe pas l'ancienne
                    Update(tw);
                    RequestScan();
                    break;
                case "window.state":
                    string s = Str(m, "state");
                    if (s == "minimize") Native.ShowWindow(tw.Hwnd, 6); // SW_MINIMIZE
                    else Native.ShowWindow(tw.Hwnd, Native.SW_RESTORE);
                    break;
            }
        }

        void Launch(Dictionary<string, object> m)
        {
            var argv = m.TryGetValue("args", out var a) && a is object[] list
                ? list.Select(x => Convert.ToString(x)).ToArray() : new string[0];
            string cmd = Str(m, "cmd");
            var paths = argv.Append(cmd).Where(ShareGuard.IsSharePath).ToArray();
            if (paths.Length == 0)
            {
                StartProcess(m, cmd, argv);
                return;
            }
            // fichier d'un dossier Linux : son lecteur est vérifié (et remonté s'il est mort)
            // hors du fil principal, qui traite la souris et le clavier
            var t = new Thread(() =>
            {
                foreach (var p in paths)
                {
                    try { ShareGuard.EnsureFor(p); }
                    catch (Exception e) { Log.Write("lecteur de " + p + " : " + e.Message); }
                }
                StartProcess(m, cmd, argv);
            }) { IsBackground = true, Name = "vasistas-launch" };
            t.SetApartmentState(ApartmentState.STA); // ShellExecute veut un fil STA
            t.Start();
        }

        void StartProcess(Dictionary<string, object> m, string cmd, string[] argv)
        {
            var reply = new Dictionary<string, object> { { "t", "launched" }, { "req", Int(m, "req") } };
            try
            {
                string args = string.Join(" ", argv.Select(Quote));
                if (cmd.StartsWith("shell:", StringComparison.OrdinalIgnoreCase))
                    Process.Start(new ProcessStartInfo("explorer.exe", cmd + (args.Length > 0 ? " " + args : "")) { UseShellExecute = false });
                else
                    Process.Start(new ProcessStartInfo(cmd, args) { UseShellExecute = true });
                reply["ok"] = true;
            }
            catch (Exception e)
            {
                reply["ok"] = false;
                reply["error"] = e.Message;
            }
            Send(reply);
        }

        /// <summary>
        /// Script PowerShell lancé depuis l'hôte (installation, diagnostic). Tourne sur un fil
        /// à part ; la sortie (tronquée à 64 Ko) revient dans `exec.result`.
        /// </summary>
        void Exec(Dictionary<string, object> m)
        {
            int req = Int(m, "req");
            string script = Str(m, "script") ?? "";
            new Thread(() =>
            {
                var reply = new Dictionary<string, object> { { "t", "exec.result" }, { "req", req } };
                try
                {
                    // fichier temporaire : -EncodedCommand est limité par la longueur de ligne (32 Ko)
                    string file = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "vasistas-exec-" + req + ".ps1");
                    System.IO.File.WriteAllText(file, script, new System.Text.UTF8Encoding(true));
                    var psi = new ProcessStartInfo("powershell.exe",
                        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + file + "\"")
                    {
                        UseShellExecute = false, CreateNoWindow = true,
                        RedirectStandardOutput = true, RedirectStandardError = true,
                        StandardOutputEncoding = System.Text.Encoding.UTF8, StandardErrorEncoding = System.Text.Encoding.UTF8,
                    };
                    using (var p = Process.Start(psi))
                    {
                        var err = p.StandardError.ReadToEndAsync();
                        string output = p.StandardOutput.ReadToEnd();
                        p.WaitForExit();
                        output += err.Result;
                        if (output.Length > 65536) output = output.Substring(output.Length - 65536);
                        reply["code"] = p.ExitCode;
                        reply["out"] = output;
                    }
                    try { System.IO.File.Delete(file); } catch (Exception) { }
                }
                catch (Exception e)
                {
                    reply["code"] = -1;
                    reply["out"] = e.ToString();
                }
                Send(reply);
            }) { IsBackground = true, Name = "vasistas-exec" }.Start();
        }

        /// <summary>
        /// Sonde de latence vue de l'invité : touche injectée, puis relecture de l'écran
        /// (composé par DWM) toutes les millisecondes jusqu'au changement.
        /// </summary>
        void Probe(Dictionary<string, object> m)
        {
            var tw = Find(Int(m, "id"));
            if (tw == null) return;
            RECT r = tw.Rect;
            int req = Int(m, "req");
            Native.ForceForeground(tw.Hwnd);
            new Thread(() =>
            {
                Thread.Sleep(400);
                var results = new List<double>();
                using (var bmp = new System.Drawing.Bitmap(r.Width, r.Height))
                using (var g = System.Drawing.Graphics.FromImage(bmp))
                {
                    for (int i = 0; i < Math.Max(1, Int(m, "n")); i++)
                    {
                        g.CopyFromScreen(r.Left, r.Top, 0, 0, bmp.Size);
                        byte[] before = Snapshot(bmp);
                        var sw = Stopwatch.StartNew();
                        ushort sc = (ushort)(0x10 + i % 10);
                        Native.SendInput(1, new[] { new INPUT { type = Native.INPUT_KEYBOARD, keyScan = sc, keyFlags = Native.KEYEVENTF_SCANCODE } }, Marshal.SizeOf<INPUT>());
                        Native.SendInput(1, new[] { new INPUT { type = Native.INPUT_KEYBOARD, keyScan = sc, keyFlags = Native.KEYEVENTF_SCANCODE | Native.KEYEVENTF_KEYUP } }, Marshal.SizeOf<INPUT>());
                        double ms = -1;
                        while (sw.ElapsedMilliseconds < 500)
                        {
                            g.CopyFromScreen(r.Left, r.Top, 0, 0, bmp.Size);
                            if (!Same(before, Snapshot(bmp))) { ms = sw.Elapsed.TotalMilliseconds; break; }
                        }
                        results.Add(ms);
                        Thread.Sleep(150);
                    }
                }
                results.Sort();
                Send(new Dictionary<string, object> { { "t", "log" }, { "msg", "sonde invité (ms) : " + string.Join(" ", results.ConvertAll(x => x.ToString("F1"))) } });
            }) { IsBackground = true }.Start();
        }

        static byte[] Snapshot(System.Drawing.Bitmap bmp)
        {
            var data = bmp.LockBits(new System.Drawing.Rectangle(0, 0, bmp.Width, bmp.Height),
                System.Drawing.Imaging.ImageLockMode.ReadOnly, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
            var bytes = new byte[data.Stride * data.Height];
            Marshal.Copy(data.Scan0, bytes, 0, bytes.Length);
            bmp.UnlockBits(data);
            return bytes;
        }

        static bool Same(byte[] a, byte[] b)
        {
            if (a.Length != b.Length) return false;
            for (int i = 0; i < a.Length; i += 4) if (a[i] != b[i] || a[i + 1] != b[i + 1] || a[i + 2] != b[i + 2]) return false;
            return true;
        }

        static string Quote(string s) =>
            s.Length > 0 && s.IndexOfAny(new[] { ' ', '\t', '"' }) < 0 ? s : "\"" + s.Replace("\"", "\\\"") + "\"";

        void Raise(Tracked tw)
        {
            var root = Native.GetAncestor(tw.Hwnd, Native.GA_ROOT);
            if (Native.GetForegroundWindow() != root)
                Native.ForceForeground(root);
            else
                Native.SetWindowPos(root, Native.HWND_TOP, 0, 0, 0, 0, Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOACTIVATE);
        }

        void EnsureOnScreen(Tracked tw)
        {
            int sw = Native.GetSystemMetrics(Native.SM_CXSCREEN), sh = Native.GetSystemMetrics(Native.SM_CYSCREEN);
            RECT b = Native.Bounds(tw.Hwnd);
            if (b.Left >= 0 && b.Top >= 0 && b.Right <= sw && b.Bottom <= sh) return;
            Native.GetWindowRect(tw.Hwnd, out RECT wr);
            int nx = Math.Max(0, Math.Min(b.Left, sw - b.Width)) - (b.Left - wr.Left);
            int ny = Math.Max(0, Math.Min(b.Top, sh - b.Height)) - (b.Top - wr.Top);
            Native.SetWindowPos(tw.Hwnd, IntPtr.Zero, nx, ny, 0, 0, Native.SWP_NOSIZE | Native.SWP_NOZORDER | Native.SWP_NOACTIVATE);
        }

        void Resize(Tracked tw, int w, int h)
        {
            int sw = Native.GetSystemMetrics(Native.SM_CXSCREEN), sh = Native.GetSystemMetrics(Native.SM_CYSCREEN);
            w = Math.Max(1, Math.Min(w, sw));
            h = Math.Max(1, Math.Min(h, sh));
            if (Native.IsZoomed(tw.Hwnd)) Native.ShowWindow(tw.Hwnd, Native.SW_RESTORE);
            Native.GetWindowRect(tw.Hwnd, out RECT wr);
            RECT b = Native.Bounds(tw.Hwnd);
            int dw = wr.Width - b.Width, dh = wr.Height - b.Height;
            // la fenêtre est placée pour tenir entièrement à l'écran, sinon SendInput ne l'atteint pas
            int bx = Math.Max(0, Math.Min(b.Left, sw - w)), by = Math.Max(0, Math.Min(b.Top, sh - h));
            Native.SetWindowPos(tw.Hwnd, IntPtr.Zero, bx - (b.Left - wr.Left), by - (b.Top - wr.Top), w + dw, h + dh,
                Native.SWP_NOZORDER | Native.SWP_NOACTIVATE);
            tw.NextCapture = 0;
        }

        /// <summary>
        /// État vu par l'agent, pour `vasistas windows` : fenêtres suivies, fenêtres visibles
        /// qui en recouvrent, et avec `all` toutes les fenêtres visibles avec la raison du choix.
        /// </summary>
        void Explain(Dictionary<string, object> m)
        {
            bool all = Bool(m, "all");
            var list = new List<object>();
            Native.EnumWindows((h, lp) =>
            {
                if (!Native.IsWindowVisible(h)) return true;
                string why = IgnoreReason(h, out _);
                bool tracked = windows.TryGetValue(h, out var tw);
                bool occluder = !tracked && h != hidden && !Native.IsIconic(h) && !Native.Cloaked(h) && Occludes(h);
                if (!all && !tracked && !occluder) return true;
                RECT r = tracked ? tw.Rect : Native.Bounds(h);
                Native.GetWindowThreadProcessId(h, out uint pid);
                string exe = "";
                try { exe = Process.GetProcessById((int)pid).ProcessName; } catch (Exception) { }
                list.Add(new Dictionary<string, object>
                {
                    { "id", h.ToInt64() }, { "class", Native.ClassName(h) }, { "exe", exe }, { "title", Native.Title(h) },
                    { "rect", new[] { r.Left, r.Top, r.Width, r.Height } },
                    { "state", tracked ? "tracked" : "ignored" }, { "kind", tracked ? tw.Kind : "-" },
                    { "why", tracked ? (tw.Owner != 0 ? "propriétaire " + tw.Owner.ToString("x") : "") : (why ?? "pas encore vue") + (occluder ? ", recouvre" : "") },
                    { "occluded", tracked && tw.Occluded },
                });
                return true;
            }, IntPtr.Zero);
            Send(new Dictionary<string, object> { { "t", "debug.windows" }, { "req", Int(m, "req") }, { "windows", list.ToArray() } });
        }

        /// <summary>
        /// Survol sur un fil à part : WM_NCHITTEST attend jusqu'à 50 ms une application occupée, la
        /// boucle principale (souris, clavier) ne doit pas en pâtir. Seule la dernière position compte.
        /// </summary>
        sealed class HoverWorker
        {
            readonly Agent agent;
            readonly AutoResetEvent wake = new AutoResetEvent(false);
            readonly object gate = new object();
            Tracked tw;
            int x, y;
            bool pending;

            public HoverWorker(Agent agent)
            {
                this.agent = agent;
                new Thread(Loop) { IsBackground = true, Name = "vasistas-hover" }.Start();
            }

            public void Request(Tracked target, int sx, int sy)
            {
                lock (gate) { tw = target; x = sx; y = sy; pending = true; }
                wake.Set();
            }

            void Loop()
            {
                while (true)
                {
                    wake.WaitOne();
                    Tracked target;
                    int px, py;
                    lock (gate)
                    {
                        if (!pending) continue;
                        target = tw; px = x; py = y; pending = false;
                    }
                    try { agent.Hover(target, px, py); }
                    catch (Exception e) { Log.Write("survol : " + e.Message); }
                }
            }
        }

        // -- survol : WM_NCHITTEST et curseur --

        void Hover(Tracked tw, int sx, int sy)
        {
            var pt = new POINT { X = sx, Y = sy };
            // Comme Windows : la fenêtre sous le pointeur répond, ses parents seulement si
            // elle se déclare transparente. Interroger la fenêtre principale d'Office
            // renverrait HTCAPTION sur toute la bande des onglets.
            var target = Native.WindowFromPoint(pt);
            if (target == IntPtr.Zero) target = tw.Hwnd;
            int hit = 1;
            IntPtr lp = new IntPtr((sy << 16) | (sx & 0xFFFF));
            for (int depth = 0; depth < 16 && target != IntPtr.Zero; depth++)
            {
                if (Native.SendMessageTimeout(target, Native.WM_NCHITTEST, IntPtr.Zero, lp, Native.SMTO_ABORTIFHUNG, 50, out IntPtr res) == IntPtr.Zero)
                    break;
                hit = (short)res.ToInt64();
                if (hit != -1) break; // HTTRANSPARENT : on passe au parent
                target = Native.GetAncestor(target, 1); // GA_PARENT
                hit = 1;
            }
            var ci = new CURSORINFO { cbSize = Marshal.SizeOf<CURSORINFO>() };
            string cursor = "default";
            if (Native.GetCursorInfo(ref ci) && ci.hCursor != IntPtr.Zero)
            {
                if (cursorNames.TryGetValue(ci.hCursor, out var name)) cursor = name;
                else cursor = CustomCursor(ci.hCursor) ?? "default";
            }
            if (hit == tw.Hit && cursor == tw.Cursor) return;
            tw.Hit = hit;
            tw.Cursor = cursor;
            Send(new Dictionary<string, object> { { "t", "hover" }, { "id", tw.Id }, { "hit", hit }, { "cursor", cursor } });
        }

        readonly Dictionary<IntPtr, string> customCursors = new Dictionary<IntPtr, string>();

        /// <summary>
        /// Curseur propre à une application : son image part une fois vers l'hôte, qui
        /// affiche la même forme ; le survol ne transmet ensuite que son nom.
        /// </summary>
        string CustomCursor(IntPtr h)
        {
            if (customCursors.TryGetValue(h, out var known)) return known;
            string name = null;
            try
            {
                if (Native.GetIconInfo(h, out ICONINFO info))
                {
                    try
                    {
                        using (var icon = System.Drawing.Icon.FromHandle(h))
                        using (var bmp = icon.ToBitmap())
                        using (var ms = new System.IO.MemoryStream())
                        {
                            bmp.Save(ms, System.Drawing.Imaging.ImageFormat.Png);
                            name = "img:" + h.ToInt64().ToString("x");
                            Send(new Dictionary<string, object>
                            {
                                { "t", "cursor.image" }, { "name", name }, { "png", Convert.ToBase64String(ms.ToArray()) },
                                { "x", info.xHotspot }, { "y", info.yHotspot },
                            });
                        }
                    }
                    finally
                    {
                        if (info.hbmColor != IntPtr.Zero) Native.DeleteObject(info.hbmColor);
                        if (info.hbmMask != IntPtr.Zero) Native.DeleteObject(info.hbmMask);
                    }
                }
            }
            catch (Exception e) { Log.Write("curseur : " + e.Message); }
            customCursors[h] = name;
            return name;
        }

        void InitCursors()
        {
            var map = new (int id, string name)[]
            {
                (32512, "default"), (32513, "text"), (32514, "wait"), (32515, "crosshair"), (32516, "default"),
                (32642, "nwse-resize"), (32643, "nesw-resize"), (32644, "ew-resize"), (32645, "ns-resize"),
                (32646, "move"), (32648, "not-allowed"), (32649, "pointer"), (32650, "progress"), (32651, "help"),
            };
            foreach (var (id, name) in map)
            {
                var h = Native.LoadCursor(IntPtr.Zero, new IntPtr(id));
                if (h != IntPtr.Zero) cursorNames[h] = name;
            }
        }

        // -- suivi des fenêtres --

        /// <summary>Fenêtre visible de l'invité, dans l'ordre Z (de haut en bas).</summary>
        struct Visible { public IntPtr H; public RECT R; }

        void Scan()
        {
            long t0 = Stopwatch.GetTimestamp();
            var seen = new List<IntPtr>();
            var visible = new List<Visible>();
            Native.EnumWindows((h, _) =>
            {
                if (h == hidden || !Native.IsWindowVisible(h)) return true;
                if (Wanted(h))
                {
                    seen.Add(h);
                    visible.Add(new Visible { H = h }); // rectangle lu dans Tracked après Update
                }
                else if (!Native.IsIconic(h) && !Native.Cloaked(h))
                {
                    RECT r = Native.Bounds(h);
                    if (r.Width >= 4 && r.Height >= 4) visible.Add(new Visible { H = h, R = r });
                }
                return true;
            }, IntPtr.Zero);

            var fg = Native.GetForegroundWindow();
            // fenêtres disparues
            foreach (var h in windows.Keys.Where(h => !seen.Contains(h)).ToList())
            {
                var tw = windows[h];
                windows.Remove(h);
                tw.Capture.Dispose();
                Send(new Dictionary<string, object> { { "t", "window.close" }, { "id", tw.Id } });
            }
            // EnumWindows va du haut vers le bas : on crée les propriétaires d'abord
            seen.Reverse();
            foreach (var h in seen.Where(x => !IsPopup(x)).Concat(seen.Where(IsPopup)))
            {
                if (windows.TryGetValue(h, out var tw)) Update(tw);
                else Add(h);
            }
            UpdateOcclusion(visible);
            if (fg != lastForeground)
            {
                lastForeground = fg;
                if (windows.TryGetValue(fg, out var f))
                {
                    if (f.Kind != "popup") lastActiveId = f.Id;
                    Send(new Dictionary<string, object> { { "t", "window.focus" }, { "id", f.Id } });
                }
            }
            Stats.Scans++;
            Stats.ScanTicks += Stopwatch.GetTimestamp() - t0;
        }

        /// <summary>
        /// Une fenêtre suivie est « recouverte » si ce que l'hôte lirait à sa place dans l'écran de
        /// QEMU n'est pas elle : une fenêtre au-dessus la chevauche (suivie ou non : menu Démarrer,
        /// notification, fenêtre d'un autre programme), ou elle déborde de l'écran. L'agent la
        /// capture alors lui-même (PrintWindow). Les menus d'une fenêtre ne la recouvrent pas :
        /// ils font partie de son image.
        /// </summary>
        void UpdateOcclusion(List<Visible> visible)
        {
            int sw = Native.GetSystemMetrics(Native.SM_CXSCREEN), sh = Native.GetSystemMetrics(Native.SM_CYSCREEN);
            var above = new List<(RECT r, uint owner)>(); // owner : fenêtre dont c'est un menu, sinon 0
            foreach (var v in visible)
            {
                if (!windows.TryGetValue(v.H, out var tw))
                {
                    if (Occludes(v.H)) above.Add((v.R, 0));
                    continue;
                }
                if (tw.Kind == "popup")
                {
                    above.Add((tw.Rect, tw.Owner));
                    continue;
                }
                RECT b = tw.Rect;
                bool occ = tw.Minimized || b.Left < 0 || b.Top < 0 || b.Right > sw || b.Bottom > sh;
                if (!occ)
                    foreach (var (r, owner) in above)
                        if (owner != tw.Id && Overlap(r, b)) { occ = true; break; }
                if (!tw.Minimized) above.Add((b, 0));
                if (occ != tw.Occluded)
                {
                    tw.Occluded = occ;
                    if (occ) { tw.Capture.Invalidate(); tw.NextCapture = 0; captureWake.Set(); }
                    Send(new Dictionary<string, object> { { "t", "window.update" }, { "id", tw.Id }, { "occluded", occ } });
                }
            }
        }

        /// <summary>Chevauchement d'au moins 4 px dans les deux sens (ignore les liserés et ombres).</summary>
        static bool Overlap(RECT a, RECT b) =>
            Math.Min(a.Right, b.Right) - Math.Max(a.Left, b.Left) >= 4 &&
            Math.Min(a.Bottom, b.Bottom) - Math.Max(a.Top, b.Top) >= 4;

        /// <summary>Fenêtre non suivie qui masque ce qui est dessous.</summary>
        static bool Occludes(IntPtr h)
        {
            if ((Native.ExStyle(h) & Native.WS_EX_TRANSPARENT) != 0) return false; // traversée par la souris : décor
            return !NonOccluding.Contains(Native.ClassName(h));
        }

        /// <summary>Vrai si le point (écran de l'invité) tombe dans un menu ou une liste suivis.</summary>
        bool InPopup(int x, int y)
        {
            foreach (var p in windows.Values)
                if (p.Kind == "popup" && x >= p.Rect.Left && x < p.Rect.Right && y >= p.Rect.Top && y < p.Rect.Bottom)
                    return true;
            return false;
        }

        bool Wanted(IntPtr h)
        {
            if (IgnoreReason(h, out bool restore) != null) return false;
            if (restore) Native.ShowWindow(h, Native.SW_RESTORE);
            return true;
        }

        /// <summary>
        /// Raison de ne pas suivre une fenêtre, ou null s'il faut la suivre. Sans effet de bord ;
        /// `restore` : fenêtre principale réduite à restaurer pour la suivre. Les raisons sont
        /// affichées par `vasistas windows --all`.
        /// </summary>
        string IgnoreReason(IntPtr h, out bool restore)
        {
            restore = false;
            if (h == hidden) return "agent";
            if (!Native.IsWindowVisible(h)) return "invisible";
            if (Native.Cloaked(h)) return "masquée par DWM";
            Native.GetWindowThreadProcessId(h, out uint pid);
            if (pid == myPid) return "agent";
            if (IgnoredClasses.Contains(Native.ClassName(h))) return "classe du bureau Windows";
            RECT b = Native.Bounds(h);
            if (b.Width < 2 || b.Height < 2) return "trop petite";
            if (Native.IsIconic(h))
            {
                if (windows.ContainsKey(h)) return null;
                // Document ouvert depuis Fichier > Ouvrir de Word : la nouvelle fenêtre peut naître
                // réduite. Jamais suivie, elle restait inaccessible depuis Linux : toute fenêtre
                // principale réduite non suivie (titre, barre de titre, sans propriétaire) est restaurée.
                if (Native.Title(h).Length == 0) return "réduite, sans titre";
                if (Native.GetWindow(h, Native.GW_OWNER) != IntPtr.Zero) return "réduite, avec propriétaire";
                if ((Native.Style(h) & Native.WS_CAPTION) != Native.WS_CAPTION) return "réduite, sans barre de titre";
                if ((Native.ExStyle(h) & Native.WS_EX_TOOLWINDOW) != 0) return "réduite, fenêtre outil";
                restore = true;
                return null;
            }
            // fenêtres rangées hors écran par leur application
            int sw = Native.GetSystemMetrics(Native.SM_CXSCREEN), sh = Native.GetSystemMetrics(Native.SM_CYSCREEN);
            if ((b.Right <= 0 || b.Bottom <= 0 || b.Left >= sw || b.Top >= sh) && !windows.ContainsKey(h)) return "hors écran";
            return null;
        }

        bool IsPopup(IntPtr h)
        {
            long style = Native.Style(h);
            if ((style & Native.WS_POPUP) != 0 && (style & Native.WS_CAPTION) != Native.WS_CAPTION) return true;
            // Fenêtres de retour visuel : ligne de repère quand on tire une colonne, une ligne ou un
            // taquet de règle dans Word (13 x 824 px, 1877 x 11 px…), sans titre mais avec les bits
            // de barre de titre. En fenêtre à part sous Linux, elles volaient la souris et se
            // posaient sur le dock.
            return SiblingOf(h) != null;
        }

        /// <summary>Fenêtre suivie (hors popup) du même programme, si `h` n'a pas de titre.</summary>
        Tracked SiblingOf(IntPtr h)
        {
            if (Native.Title(h).Length != 0) return null;
            Native.GetWindowThreadProcessId(h, out uint pid);
            Tracked best = null;
            foreach (var tw in windows.Values)
            {
                if (tw.Kind == "popup" || tw.Hwnd == h) continue;
                Native.GetWindowThreadProcessId(tw.Hwnd, out uint p);
                if (p != pid) continue;
                if (best == null || tw.Id == lastActiveId) best = tw;
            }
            return best;
        }

        uint OwnerOf(IntPtr h, bool popup)
        {
            for (var o = Native.GetWindow(h, Native.GW_OWNER); o != IntPtr.Zero; o = Native.GetWindow(o, Native.GW_OWNER))
            {
                if (windows.TryGetValue(o, out var tw))
                {
                    // les popups se rattachent à une fenêtre normale ou un dialogue
                    if (tw.Kind != "popup") return tw.Id;
                    if (tw.Owner != 0) return tw.Owner;
                }
            }
            return popup ? lastActiveId : 0;
        }

        void Add(IntPtr h)
        {
            bool popup = IsPopup(h);
            var tw = new Tracked
            {
                Hwnd = h,
                Id = (uint)h.ToInt64(),
                Title = Native.Title(h),
                Kind = popup ? "popup" : (Native.GetWindow(h, Native.GW_OWNER) != IntPtr.Zero ? "dialog" : "normal"),
            };
            tw.Owner = OwnerOf(h, popup);
            if (popup && Native.GetWindow(h, Native.GW_OWNER) == IntPtr.Zero)
            {
                // sans propriétaire : se rattacher à la fenêtre du même programme plutôt qu'à la
                // dernière active (qui peut être Outlook quand on travaille dans Word)
                var sib = SiblingOf(h);
                if (sib != null) tw.Owner = sib.Id;
            }
            if (popup && tw.Owner == 0) tw.Kind = "popup";
            bool maximized = false;
            if (!popup)
            {
                int pref = Native.DWMWCP_DONOTROUND;
                Native.DwmSetWindowAttribute(h, Native.DWMWA_WINDOW_CORNER_PREFERENCE, ref pref, 4);
                if (Native.IsZoomed(h))
                {
                    // l'hôte gère l'agrandissement ; dans l'invité la fenêtre reste normale
                    maximized = true;
                    Native.ShowWindow(h, Native.SW_RESTORE);
                }
                EnsureOnScreen(tw);
            }
            tw.Rect = Native.Bounds(h);
            tw.Minimized = Native.IsIconic(h);
            windows[h] = tw;
            if (!popup) lastActiveId = Native.GetForegroundWindow() == h ? tw.Id : lastActiveId;
            SendNew(tw, maximized);
            tw.NextCapture = 0;
        }

        /// <summary>Annonce une fenêtre à l'hôte (nouvelle, ou renvoyée sur demande `resend`).</summary>
        void SendNew(Tracked tw, bool maximized)
        {
            var app = Apps.Of(tw.Hwnd);
            if (iconsSent.Add(app.Id))
            {
                string png = null;
                try { png = Apps.IconPng(app.ImagePath); } catch (Exception e) { Log.Write("icône " + app.Id + " : " + e.Message); }
                if (png != null) Send(new Dictionary<string, object> { { "t", "app.icon" }, { "app", app.Id }, { "png", png } });
            }
            Send(new Dictionary<string, object>
            {
                { "t", "window.new" }, { "id", tw.Id }, { "title", tw.Title },
                { "app", app.Id }, { "appName", app.Name }, { "exe", app.Exe },
                { "rect", new[] { tw.Rect.Left, tw.Rect.Top, tw.Rect.Width, tw.Rect.Height } },
                { "kind", tw.Kind }, { "owner", tw.Owner }, { "maximized", maximized },
                { "occluded", tw.Occluded }, { "minimized", tw.Minimized },
            });
        }

        void Update(Tracked tw)
        {
            var h = tw.Hwnd;
            var msg = new Dictionary<string, object> { { "t", "window.update" }, { "id", tw.Id } };
            if (tw.Kind != "popup")
            {
                if (Native.IsZoomed(h))
                {
                    Native.ShowWindow(h, Native.SW_RESTORE);
                    Send(new Dictionary<string, object> { { "t", "window.request" }, { "id", tw.Id }, { "action", "maximize" } });
                }
                bool min = Native.IsIconic(h);
                if (min && !tw.Minimized)
                    msg["minimized"] = true;
                else if (!min && tw.Minimized)
                    msg["minimized"] = false;
                tw.Minimized = min;
            }
            string title = Native.Title(h);
            if (title != tw.Title) { tw.Title = title; msg["title"] = title; }
            if (!tw.Minimized)
            {
                RECT r = Native.Bounds(h);
                if (r.Left != tw.Rect.Left || r.Top != tw.Rect.Top || r.Width != tw.Rect.Width || r.Height != tw.Rect.Height)
                {
                    tw.Rect = r;
                    msg["rect"] = new[] { r.Left, r.Top, r.Width, r.Height };
                }
            }
            if (msg.Count > 2) Send(msg);
        }

        /// <summary>
        /// Vrai si aucune autre fenêtre suivie (hors popups) ne la chevauche et qu'elle tient à l'écran :
        /// on peut alors copier l'écran, bien plus rapide que PrintWindow.
        /// </summary>
        bool Uncovered(Tracked tw)
        {
            int sw = Native.GetSystemMetrics(Native.SM_CXSCREEN), sh = Native.GetSystemMetrics(Native.SM_CYSCREEN);
            RECT b = tw.Rect;
            if (b.Left < 0 || b.Top < 0 || b.Right > sw || b.Bottom > sh) return false;
            foreach (var o in windows.Values)
            {
                if (o == tw || o.Kind == "popup" || o.Minimized) continue;
                RECT r = o.Rect;
                if (r.Left < b.Right && b.Left < r.Right && r.Top < b.Bottom && b.Top < r.Bottom) return false;
            }
            return true;
        }

        Tracked Find(int id)
        {
            foreach (var tw in windows.Values) if (tw.Id == (uint)id) return tw;
            return null;
        }

        void Send(Dictionary<string, object> msg) => ch.Send(msg);

        static string Str(Dictionary<string, object> m, string k) => m.TryGetValue(k, out var v) ? Convert.ToString(v) : null;
        static int Int(Dictionary<string, object> m, string k) => m.TryGetValue(k, out var v) && v != null ? Convert.ToInt32(v) : 0;
        static bool Bool(Dictionary<string, object> m, string k) => m.TryGetValue(k, out var v) && v is bool b && b;
    }

    /// <summary>
    /// Pointeurs standard de Windows remplacés par un pointeur transparent. Les applications
    /// gardent les mêmes handles (LoadCursor), donc le nom envoyé dans `hover` reste juste.
    /// </summary>
    static class Cursors
    {
        static readonly uint[] Ids = { 32512, 32513, 32514, 32515, 32516, 32640, 32641, 32642, 32643, 32644,
                                       32645, 32646, 32648, 32649, 32650, 32651, 32671, 32672 };

        [DllImport("magnification.dll")] static extern bool MagInitialize();
        [DllImport("magnification.dll")] static extern bool MagShowSystemCursor(bool show);
        static bool magnifier;

        public static void Hide()
        {
            // API de la loupe : masque le pointeur quelle que soit sa forme, curseurs propres
            // aux applications compris (le I de sélection d'Office, par exemple)
            try { magnifier = MagInitialize() && MagShowSystemCursor(false); }
            catch (Exception e) { Log.Write("loupe : " + e.Message); }
            var and = new byte[32 * 32 / 8];
            for (int i = 0; i < and.Length; i++) and[i] = 0xFF; // tout transparent
            var xor = new byte[32 * 32 / 8];
            foreach (var id in Ids)
            {
                var blank = Native.CreateCursor(IntPtr.Zero, 0, 0, 32, 32, and, xor);
                Native.SetSystemCursor(blank, id); // SetSystemCursor détruit `blank` lui-même
            }
            Log.Write("pointeurs Windows masqués" + (magnifier ? " (loupe)" : ""));
        }

        public static void Restore()
        {
            if (magnifier) { try { MagShowSystemCursor(true); } catch (Exception) { } }
            Native.SystemParametersInfo(Native.SPI_SETCURSORS, 0, IntPtr.Zero, 0);
        }
    }

    static class Input
    {
        static readonly int Size = Marshal.SizeOf<INPUT>();

        public static void MoveTo(int x, int y)
        {
            int vx = Native.GetSystemMetrics(Native.SM_XVIRTUALSCREEN), vy = Native.GetSystemMetrics(Native.SM_YVIRTUALSCREEN);
            int vw = Native.GetSystemMetrics(Native.SM_CXVIRTUALSCREEN), vh = Native.GetSystemMetrics(Native.SM_CYVIRTUALSCREEN);
            var i = new INPUT
            {
                type = Native.INPUT_MOUSE,
                mouseDx = (int)(((long)(x - vx) * 65535 + (vw - 1) / 2) / Math.Max(1, vw - 1)),
                mouseDy = (int)(((long)(y - vy) * 65535 + (vh - 1) / 2) / Math.Max(1, vh - 1)),
                mouseFlags = Native.MOUSEEVENTF_MOVE | Native.MOUSEEVENTF_ABSOLUTE | Native.MOUSEEVENTF_VIRTUALDESK,
            };
            Native.SendInput(1, new[] { i }, Size);
        }

        public static void Button(int button, bool down)
        {
            uint flags;
            int data = 0;
            switch (button)
            {
                case 1: flags = down ? Native.MOUSEEVENTF_LEFTDOWN : Native.MOUSEEVENTF_LEFTUP; break;
                case 2: flags = down ? Native.MOUSEEVENTF_MIDDLEDOWN : Native.MOUSEEVENTF_MIDDLEUP; break;
                case 3: flags = down ? Native.MOUSEEVENTF_RIGHTDOWN : Native.MOUSEEVENTF_RIGHTUP; break;
                case 8: flags = down ? Native.MOUSEEVENTF_XDOWN : Native.MOUSEEVENTF_XUP; data = 1; break;
                case 9: flags = down ? Native.MOUSEEVENTF_XDOWN : Native.MOUSEEVENTF_XUP; data = 2; break;
                default: return;
            }
            Native.SendInput(1, new[] { new INPUT { type = Native.INPUT_MOUSE, mouseFlags = flags, mouseData = data } }, Size);
        }

        public static void Wheel(int dx, int dy)
        {
            if (dy != 0)
                Native.SendInput(1, new[] { new INPUT { type = Native.INPUT_MOUSE, mouseFlags = Native.MOUSEEVENTF_WHEEL, mouseData = dy } }, Size);
            if (dx != 0)
                Native.SendInput(1, new[] { new INPUT { type = Native.INPUT_MOUSE, mouseFlags = Native.MOUSEEVENTF_HWHEEL, mouseData = dx } }, Size);
        }

        public static void Key(Dictionary<string, object> m)
        {
            bool down = m.TryGetValue("down", out var d) && d is bool b && b;
            var i = new INPUT { type = Native.INPUT_KEYBOARD };
            if (m.TryGetValue("vk", out var vk) && vk != null)
            {
                i.keyVk = (ushort)Convert.ToInt32(vk);
                i.keyFlags = down ? 0 : Native.KEYEVENTF_KEYUP;
            }
            else
            {
                i.keyScan = (ushort)Convert.ToInt32(m["sc"]);
                i.keyFlags = Native.KEYEVENTF_SCANCODE;
                if (m.TryGetValue("ext", out var e) && e is bool ext && ext) i.keyFlags |= Native.KEYEVENTF_EXTENDEDKEY;
                if (!down) i.keyFlags |= Native.KEYEVENTF_KEYUP;
            }
            if (Native.SendInput(1, new[] { i }, Size) == 1) Stats.KeysSent++;
        }
    }

    static class Program
    {
        [STAThread]
        static void Main()
        {
            using (var mutex = new Mutex(true, @"Global\Vasistas.Agent", out bool first))
            {
                if (!first) return;
                AppDomain.CurrentDomain.ProcessExit += (s, e) => Cursors.Restore();
                try { new Agent().Run(); }
                catch (Exception e) { Cursors.Restore(); Log.Write("arrêt : " + e); throw; }
            }
        }
    }
}
