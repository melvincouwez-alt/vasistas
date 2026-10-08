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
        public int Dpi;         // DPI de la fenêtre dans Windows : l'hôte en tire son échelle
        public bool Occluded; // une autre fenêtre suivie la recouvre en partie dans l'invité
        public bool HostHidden; // sa fenêtre Linux est réduite ou masquée : rien à capturer
        public readonly Capture Capture = new Capture();
        public long NextCapture, LastCapture;
        public int Unchanged;   // captures successives sans changement : l'intervalle s'allonge
        public int Hit = -1;
        public string Cursor;
        public int Nc;          // hauteur de la barre de titre native de Windows (0 : dessinée par l'application)
    }

    sealed class Agent
    {
        const int Version = 1;
        // version de Vasistas pour laquelle l'agent est construit (champ agentVersion du hello) :
        // à tenir égale à VERSION de host/vasistas/version.py ; l'hôte propose une mise à jour si
        // l'agent est plus ancien (version.newer)
        const string AgentVersion = "0.9.1";
        // La liste des fenêtres suit les événements de Windows (SetWinEventHook) ; le balayage
        // périodique n'est plus qu'un filet de sécurité.
        const int FallbackScanMs = 500, MinScanGapMs = 8, SyncMs = 3000, IdleWaitMs = 500, NoHostWaitMs = 1000;
        const int ActiveMs = 16, IdleMs = 100;
        // fil de capture sans fenêtre à capturer : il dort jusqu'au signal de la boucle principale,
        // ce délai n'étant qu'un filet si un changement d'état passait inaperçu
        const int CaptureSleepMs = 1000;
        // fenêtre recouverte, en mode écran QEMU : PrintWindow coûte 30 à 90 ms, deux fois par seconde
        // suffit ; image inchangée plusieurs fois de suite : jusqu'à OccludedMaxMs entre deux captures
        const int OccludedMs = 500, OccludedMaxMs = 2000, ReleaseAfterMs = 10000;
        // fenêtre recouverte dont le contenu change (curseur texte, défilement, console) : capture
        // dès l'événement, à cette cadence au plus (le repos garde OccludedMs)
        const int EventCaptureMs = 50;
        // cadence choisie par l'hôte selon son profil de puissance (message capture)
        volatile int occludedBaseMs = OccludedMs;
        // minuterie à 1 ms voulue par l'hôte (message capture, faux sur batterie)
        bool timerWanted;
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
        bool captureIdle; // sous `sync` : le fil de capture dort faute de fenêtre à capturer
        bool wasReady;
        readonly Dictionary<IntPtr, string> cursorNames = new Dictionary<IntPtr, string>();
        // nouvelle fenêtre plus grande que l'écran de Windows ou hors de l'écran : ramenée dedans
        // avant window.new (champ `clamp` de hello/display, actif par défaut)
        bool clampNew = true;
        int lastScalePercent;      // dernière échelle demandée par l'hôte (redonnée après un changement de mode)
        const double ClampMax = 0.9;
        Toasts toasts;
        Tray tray;

        public void Run()
        {
            Native.SetProcessDpiAwarenessContext(new IntPtr(-4)); // PER_MONITOR_AWARE_V2
            Wgc.Prewarm();
            // Fenêtre de l'agent, hors écran : prend le premier plan pour fermer menus et popups
            hidden = Native.CreateWindowEx((uint)Native.WS_EX_TOOLWINDOW, "STATIC", "Vasistas",
                0x80000000u | 0x10000000u, -32000, -32000, 1, 1, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
            InitCursors();
            clipboard = new ClipboardSync(Send);
            // copie dans Windows : WM_CLIPBOARDUPDATE réveille la boucle principale, qui ne relit
            // plus le presse-papiers que toutes les IdleWaitMs en filet
            if (!Native.AddClipboardFormatListener(hidden))
                Log.Write("presse-papiers : pas d'avis de copie (" + Marshal.GetLastWin32Error() + "), relu toutes les " + IdleWaitMs + " ms");
            toasts = new Toasts(Send);
            tray = new Tray(Send, () => ch.HostReady);
            Log.Sink = msg => { if (ch.HostReady) ch.Send(new Dictionary<string, object> { { "t", "log" }, { "msg", msg } }); };
            ch.Start();
            hover = new HoverWorker(this);
            InstallHooks();
            new Thread(CaptureLoop) { IsBackground = true, Name = "vasistas-capture" }.Start();
            ShareGuard.Start();
            TimerRes.EnsureGlobal();
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
                    Input.ReleaseButtons();
                    Log.Write("hôte perdu");
                }
                TimerRes.Set(timerWanted && ch.HostReady && windows.Count > 0);
                if (!ch.HostReady) { wait = NoHostWaitMs; continue; } // InboxEvent réveille au hello
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
                // fenêtre devenue à capturer (nouvelle, rétablie, recouverte, mode d'image changé…) :
                // un seul test ici plutôt qu'un signal à chaque endroit qui change ces états
                if (captureIdle && windows.Values.Any(Capturable)) { captureIdle = false; captureWake.Set(); }
                // Réveil au plus tard pour le prochain balayage de secours, le presse-papiers
                // (IdleWaitMs, ou nouvel essai s'il était verrouillé) ou un balayage demandé mais
                // trop proche du précédent.
                long due = Math.Min(nextScan, now + IdleWaitMs);
                if (clipboard.RetryAt > now) due = Math.Min(due, clipboard.RetryAt);
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
                // contenu d'une fenêtre recouverte : défilement, valeur (ascenseurs), console
                (Native.EVENT_SYSTEM_SCROLLINGSTART, Native.EVENT_SYSTEM_SCROLLINGEND),
                (Native.EVENT_OBJECT_VALUECHANGE, Native.EVENT_OBJECT_VALUECHANGE),
                (Native.EVENT_CONSOLE_CARET, Native.EVENT_CONSOLE_UPDATE_SCROLL),
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
            if (hwnd == IntPtr.Zero) return;
            bool content = (ev == Native.EVENT_OBJECT_LOCATIONCHANGE && idObject != Native.OBJID_WINDOW)  // curseur texte, enfants
                || ev == Native.EVENT_OBJECT_VALUECHANGE || ev == Native.EVENT_SYSTEM_SCROLLINGSTART || ev == Native.EVENT_SYSTEM_SCROLLINGEND
                || (ev >= Native.EVENT_CONSOLE_CARET && ev <= Native.EVENT_CONSOLE_UPDATE_SCROLL);
            if (content)
            {
                // fenêtre recouverte capturée par l'agent : image refaite dès que son contenu bouge
                var root = Native.GetAncestor(hwnd, Native.GA_ROOT);
                if (root != IntPtr.Zero && windows.TryGetValue(root, out var otw) && otw.Occluded) CaptureSoon(otw);
                return;
            }
            if (idObject != Native.OBJID_WINDOW || idChild != 0) return;
            if ((ev == Native.EVENT_OBJECT_SHOW || ev == Native.EVENT_OBJECT_UNCLOAKED) && toasts.Enabled) toasts.Shown(hwnd);
            if (ev ==Native.EVENT_OBJECT_LOCATIONCHANGE || ev == Native.EVENT_OBJECT_NAMECHANGE || ev == Native.EVENT_OBJECT_REORDER)
            {
                // très fréquents (contrôles enfants, curseur texte) : fenêtres de premier niveau seulement
                if (Native.GetAncestor(hwnd, Native.GA_ROOT) != hwnd) return;
                if (ev == Native.EVENT_OBJECT_NAMECHANGE && !windows.ContainsKey(hwnd)) return;
            }
            Stats.Events++;
            scanDue = true;
        }

        void RequestScan() => scanDue = true;

        /// <summary>Fenêtre que le fil de capture doit capturer lui-même (pas lue dans l'écran de QEMU).</summary>
        /// <summary>
        /// Capture avancée (contenu changé : événement d'accessibilité ou nouvelle image de DWM),
        /// à EventCaptureMs au plus de la précédente.
        /// </summary>
        void CaptureSoon(Tracked tw)
        {
            if (!Capturable(tw)) return;
            long soon = Math.Max(tw.LastCapture + Math.Max(EventCaptureMs, occludedBaseMs / 5), 1);
            if (tw.NextCapture > soon) { tw.NextCapture = soon; captureWake.Set(); }
        }

        bool Capturable(Tracked tw) =>
            !tw.Minimized && !tw.HostHidden && !(framebuffer && (tw.Kind == "popup" || !tw.Occluded));

        /// <summary>
        /// Fil de capture : les captures lentes ne retardent plus la souris ni le clavier,
        /// traités par la boucle principale.
        /// </summary>
        void CaptureLoop()
        {
            var due = new List<(Tracked tw, bool fromScreen)>();
            while (true)
            {
                if (!ch.HostReady) { captureWake.WaitOne(1000); continue; } // réveillé par hello
                long now = clock.ElapsedMilliseconds;
                due.Clear();
                long next = now + IdleMs;
                bool any = false;
                lock (sync)
                {
                    var fg = Native.GetForegroundWindow();
                    foreach (var tw in windows.Values)
                    {
                        if (tw.Minimized || tw.HostHidden || !Capturable(tw))
                        {
                            // lue dans l'écran de QEMU depuis un moment : tampons de capture rendus (pas
                            // aussitôt, une fenêtre souvent recouverte puis découverte les réallouerait)
                            if (now > tw.NextCapture + ReleaseAfterMs) tw.Capture.Release();
                            continue;
                        }
                        any = true;
                        if (now < tw.NextCapture) { next = Math.Min(next, tw.NextCapture); continue; }
                        bool fast = !framebuffer && (tw.Kind == "popup" || tw.Hwnd == fg || Native.GetAncestor(fg, Native.GA_ROOTOWNER) == tw.Hwnd);
                        int baseMs = occludedBaseMs;
                        int occludedMs = Math.Min(Math.Max(OccludedMaxMs, baseMs), baseMs << Math.Min(2, tw.Unchanged / 4));
                        tw.LastCapture = now;
                        tw.NextCapture = now + (fast ? ActiveMs : framebuffer ? occludedMs : IdleMs);
                        next = Math.Min(next, tw.NextCapture);
                        // la taille a pu changer depuis le dernier scan : prévenir l'hôte avant les tuiles
                        RECT r = Native.Bounds(tw.Hwnd);
                        if (r.Width != tw.Rect.Width || r.Height != tw.Rect.Height) Update(tw);
                        due.Add((tw, tw.Kind == "popup" || (tw.Hwnd == fg && Uncovered(tw))));
                    }
                    captureIdle = !any; // relu par la boucle principale, qui réveille ce fil
                }
                foreach (var (tw, fromScreen) in due)
                {
                    try { tw.Unchanged = tw.Capture.Grab(tw.Hwnd, tw.Id, fromScreen, ch) ? 0 : tw.Unchanged + 1; }
                    catch (Exception e) { Log.Write("capture " + tw.Id + " : " + e.Message); }
                }
                int wait = any ? (int)Math.Min(next - clock.ElapsedMilliseconds, IdleMs) : CaptureSleepMs;
                if (wait > 0) captureWake.WaitOne(wait);
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
                    lastScalePercent = (int)Math.Round(Convert.ToDouble(sc) * 100);
                    try { Display.Apply(lastScalePercent); }
                    catch (Exception e) { Log.Write("échelle : " + e.Message); }
                }
                if (m.TryGetValue("resolution", out var res) && res is object[] wh && wh.Length == 2)
                {
                    try { Display.SetResolution(Convert.ToInt32(wh[0]), Convert.ToInt32(wh[1])); }
                    catch (Exception e) { Log.Write("résolution : " + e.Message); }
                    // Windows garde une échelle par mode : celle de l'hôte est redonnée après le changement
                    if (lastScalePercent > 0)
                        try { Display.Apply(lastScalePercent); } catch (Exception e) { Log.Write("échelle : " + e.Message); }
                }
                if (m.TryGetValue("stats", out var sv) && sv is bool verbose) verboseStats = verbose;
                if (m.TryGetValue("framebuffer", out var fbv) && fbv is bool fb && fb != framebuffer)
                {
                    framebuffer = fb;
                    // l'hôte lit l'écran tel quel : le pointeur de Windows y serait visible en double
                    if (fb) Cursors.Hide(); else Cursors.Restore();
                }
                if (m.TryGetValue("clamp", out var cv) && cv is bool clamp) clampNew = clamp;
                if (m.TryGetValue("notifications", out var nv) && nv is bool notify) toasts.SetEnabled(notify); // registre écrit seulement s'il change
                if (m.TryGetValue("tray", out var tv) && tv is bool trayOn && trayOn != tray.Enabled) tray.SetEnabled(trayOn);
                if (t == "display") return;
                long nowHello = clock.ElapsedMilliseconds;
                // l'hôte répète hello tant qu'il n'a pas de réponse : une rafale ne doit pas tout renvoyer
                bool repeat = ch.HostReady && nowHello - lastHello < 1500;
                lastHello = nowHello;
                if (repeat) return;
                ch.ClearQueue();
                ch.Zstd = m.TryGetValue("zstd", out var zv) && zv is bool zstd && zstd && Zstd.Ready;
                ch.HostReady = true;
                captureWake.Set();
                wasReady = true;
                Send(new Dictionary<string, object>
                {
                    { "t", "hello" }, { "version", Version }, { "agentVersion", AgentVersion },
                    { "screen", new[] { Native.GetSystemMetrics(Native.SM_CXSCREEN), Native.GetSystemMetrics(Native.SM_CYSCREEN) } },
                    { "dpi", (int)Native.GetDpiForSystem() },
                });
                foreach (var w in windows.Values) w.Capture.Dispose();
                windows.Clear(); // tout renvoyer
                iconsSent.Clear();
                Scan();
                tray.Resend();
                return;
            }
            if (t == "launch") { Launch(m); return; }
            if (t == "debug.windows") { Explain(m); return; }
            if (t == "bench.post")
            {
                // banc de l'hôte : un caractère posté à la fenêtre sans la mettre au premier plan
                // (latence d'une fenêtre recouverte) ; bench.posted part juste après, dans l'ordre
                var bw = Find(Int(m, "id"));
                if (bw != null) Native.PostMessage(bw.Hwnd, Native.WM_CHAR, new IntPtr('a'), IntPtr.Zero);
                Send(new Dictionary<string, object> { { "t", "bench.posted" }, { "id", Int(m, "id") }, { "i", Int(m, "i") } });
                return;
            }
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
            if (t == "clipboard") { clipboard.Apply(m); return; }
            if (t == "key") { Stats.KeysIn++; Input.Key(m); return; }
            if (t == "theme")
            {
                Look.Theme(Bool(m, "dark"), m.TryGetValue("accent", out var ac) && ac is string accent ? accent.ToLowerInvariant() : null);
                return;
            }
            if (t == "fonts") { Look.Fonts(Str(m, "smoothing") ?? "grayscale"); return; }
            if (t == "windows.reset") { ResetAll(m.TryGetValue("max", out var mx) && mx != null ? Convert.ToDouble(mx) : 0.8); return; }
            if (t == "notify.activate") { toasts.Activate(Int(m, "id")); return; }
            if (t == "tray.click") { tray.Click(Str(m, "key"), Str(m, "button") ?? "left", Int(m, "x"), Int(m, "y")); return; }
            if (t == "capture")
            {
                // profil de puissance de l'hôte : délai entre deux captures d'une fenêtre recouverte
                int ms = Int(m, "occluded_ms");
                if (ms > 0) occludedBaseMs = Math.Max(100, Math.Min(5000, ms));
                timerWanted = Int(m, "timer_ms") > 0;
                return;
            }

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
                case "window.place":
                    if (tw.Kind == "popup") break;
                    Unmaximize(tw);
                    Place(tw, Int(m, "x"), Int(m, "y"), Int(m, "w"), Int(m, "h"));
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
                    string dir = System.IO.Path.GetTempPath();
                    string file = System.IO.Path.Combine(dir, "vasistas-exec-" + req + ".ps1");
                    string outFile = System.IO.Path.Combine(dir, "vasistas-exec-" + req + ".out");
                    System.IO.File.WriteAllText(file, script, new System.Text.UTF8Encoding(true));
                    // sortie dans un fichier, pas dans un tube : un programme lancé par le script
                    // (Start-Process) héritait du tube et l'exec ne rendait la main qu'à sa fermeture
                    string cmd = "& '" + file + "' *>&1 | Out-File -FilePath '" + outFile + "' -Encoding utf8 -Width 4096; exit $LASTEXITCODE";
                    var psi = new ProcessStartInfo("powershell.exe",
                        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"" + cmd + "\"")
                    {
                        UseShellExecute = false, CreateNoWindow = true,
                    };
                    using (var p = Process.Start(psi))
                    {
                        p.WaitForExit();
                        string output = System.IO.File.Exists(outFile) ? System.IO.File.ReadAllText(outFile) : "";
                        if (output.Length > 65536) output = output.Substring(output.Length - 65536);
                        reply["code"] = p.ExitCode;
                        reply["out"] = output;
                    }
                    try { System.IO.File.Delete(outFile); } catch (Exception) { }
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

        /// <summary>Restaure sans l'activer une fenêtre agrandie ou réduite dans l'invité.</summary>
        static void Unmaximize(Tracked tw)
        {
            if (Native.IsZoomed(tw.Hwnd) || Native.IsIconic(tw.Hwnd)) Native.ShowWindow(tw.Hwnd, Native.SW_SHOWNOACTIVATE);
        }

        /// <summary>
        /// Pose la zone visible de la fenêtre (sans les bordures invisibles) au rectangle donné, en
        /// pixels physiques de l'écran de Windows. Ramenée dans l'écran : au-delà, SendInput ne
        /// l'atteint pas.
        /// </summary>
        void Place(Tracked tw, int x, int y, int w, int h)
        {
            int sw = Native.GetSystemMetrics(Native.SM_CXSCREEN), sh = Native.GetSystemMetrics(Native.SM_CYSCREEN);
            w = Math.Max(1, Math.Min(w, sw));
            h = Math.Max(1, Math.Min(h, sh));
            x = Math.Max(0, Math.Min(x, sw - w));
            y = Math.Max(0, Math.Min(y, sh - h));
            Native.GetWindowRect(tw.Hwnd, out RECT wr);
            RECT b = Native.Bounds(tw.Hwnd);
            Native.SetWindowPos(tw.Hwnd, IntPtr.Zero, x - (b.Left - wr.Left), y - (b.Top - wr.Top),
                w + wr.Width - b.Width, h + wr.Height - b.Height, Native.SWP_NOZORDER | Native.SWP_NOACTIVATE);
            tw.NextCapture = 0;
        }

        /// <summary>Zone de travail de l'écran de Windows (tout l'écran, la barre des tâches étant masquée).</summary>
        static RECT WorkArea()
        {
            if (Native.SystemParametersInfo(Native.SPI_GETWORKAREA, 0, out RECT r, 0) && r.Width > 0 && r.Height > 0) return r;
            return new RECT { Right = Native.GetSystemMetrics(Native.SM_CXSCREEN), Bottom = Native.GetSystemMetrics(Native.SM_CYSCREEN) };
        }

        static bool OffScreen(RECT b) =>
            b.Left < 0 || b.Top < 0 || b.Right > Native.GetSystemMetrics(Native.SM_CXSCREEN) || b.Bottom > Native.GetSystemMetrics(Native.SM_CYSCREEN);

        /// <summary>
        /// Taille ramenée à au plus `max` fois la zone de travail sur chaque axe, puis fenêtre centrée.
        /// La taille minimale de l'application est respectée sans l'envoyer nous-mêmes : pendant
        /// SetWindowPos, Windows envoie WM_WINDOWPOSCHANGING à la fenêtre, et DefWindowProc y
        /// applique WM_GETMINMAXINFO. La taille obtenue est relue pour centrer.
        /// </summary>
        void Fit(Tracked tw, double max)
        {
            RECT wa = WorkArea();
            RECT b = Native.Bounds(tw.Hwnd);
            int w = Math.Max(1, Math.Min(b.Width, (int)(wa.Width * max)));
            int h = Math.Max(1, Math.Min(b.Height, (int)(wa.Height * max)));
            Place(tw, wa.Left + (wa.Width - w) / 2, wa.Top + (wa.Height - h) / 2, w, h);
            RECT got = Native.Bounds(tw.Hwnd);
            if (got.Width != w || got.Height != h)
                Place(tw, wa.Left + Math.Max(0, (wa.Width - got.Width) / 2), wa.Top + Math.Max(0, (wa.Height - got.Height) / 2),
                    got.Width, got.Height);
        }

        /// <summary>
        /// `windows.reset` : fenêtres suivies (sauf popups et fenêtres réduites) restaurées,
        /// ramenées à `max` fois la zone de travail au plus et centrées.
        /// </summary>
        void ResetAll(double max)
        {
            max = Math.Max(0.2, Math.Min(1.0, max));
            int count = 0;
            foreach (var tw in windows.Values.ToList())
            {
                if (tw.Kind == "popup" || Native.IsIconic(tw.Hwnd)) continue;
                Unmaximize(tw);
                Fit(tw, max);
                Update(tw);
                count++;
            }
            RequestScan();
            Send(new Dictionary<string, object> { { "t", "windows.reset.done" }, { "count", count } });
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
                try { using (var p = Process.GetProcessById((int)pid)) exe = p.ProcessName; } catch (Exception) { }
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
            if (restore) Native.ShowWindowAsync(h, Native.SW_RESTORE);
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
                // pas de liseré de Windows : l'hôte dessine le cadre (coins, ombre)
                int none = Native.DWMWA_COLOR_NONE;
                Native.DwmSetWindowAttribute(h, Native.DWMWA_BORDER_COLOR, ref none, 4);
                if (Native.IsZoomed(h))
                {
                    // l'hôte gère l'agrandissement ; dans l'invité la fenêtre reste normale
                    maximized = true;
                    Native.ShowWindow(h, Native.SW_RESTORE);
                }
                // fenêtre née plus grande que l'écran de Windows, ou en partie dehors (taille
                // mémorisée sur un autre écran) : ramenée dedans avant que l'hôte ne la voie
                if (!maximized && clampNew && OffScreen(Native.Bounds(h))) Fit(tw, ClampMax);
                else EnsureOnScreen(tw);
            }
            tw.Rect = Native.Bounds(h);
            tw.Nc = popup ? 0 : Native.NativeCaption(h);
            tw.Minimized = Native.IsIconic(h);
            tw.Capture.FrameArrived = () => CaptureSoon(tw);
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
                { "occluded", tw.Occluded }, { "minimized", tw.Minimized }, { "nc", tw.Nc },
                // écran d'accueil (Word, Excel…) : pas de bord redimensionnable, hors mémoire de placement
                { "sizable", (Native.Style(tw.Hwnd) & Native.WS_THICKFRAME) != 0 },
                { "dpi", tw.Dpi = (int)Native.GetDpiForWindow(tw.Hwnd) },
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
                    Native.ShowWindowAsync(h, Native.SW_RESTORE);
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
            // Après un changement d'échelle, chaque fenêtre passe au nouveau DPI à son rythme :
            // l'hôte doit savoir à quelle échelle est dessinée l'image qu'il reçoit.
            int dpi = (int)Native.GetDpiForWindow(h);
            if (dpi > 0 && dpi != tw.Dpi) { tw.Dpi = dpi; msg["dpi"] = dpi; }
            if (tw.Kind != "popup" && !tw.Minimized)
            {
                int nc = Native.NativeCaption(h);
                if (nc != tw.Nc) { tw.Nc = nc; msg["nc"] = nc; }
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

        static readonly HashSet<int> held = new HashSet<int>();

        /// <summary>Hôte perdu en plein clic : bouton relâché, sinon tout devient un glisser.</summary>
        public static void ReleaseButtons()
        {
            foreach (int b in held.ToArray()) Button(b, false);
        }

        public static void Button(int button, bool down)
        {
            if (down) held.Add(button); else held.Remove(button);
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
