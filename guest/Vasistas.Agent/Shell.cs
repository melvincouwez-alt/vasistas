using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Interop.UIAutomationClient;
using Microsoft.Win32;

namespace Vasistas.Agent
{
    /// <summary>
    /// UI Automation par son API COM (UIA3, types d'interop embarqués dans l'exe). L'API managée
    /// System.Windows.Automation (UIA2) ne voit pas le contenu XAML : essai réel, barre des tâches
    /// réduite à ses fenêtres Win32 et bannière réduite à son ScrollViewer. Les arbres sont lus
    /// dans la vue brute en un seul aller-retour (cache UIA du sous-arbre) ; repli sur le parcours
    /// nœud par nœud (RawViewWalker) si le cache échoue ou revient vide.
    /// </summary>
    static class Uia
    {
        public const int Button = 50000, Text = 50020, InvokePattern = 10000;

        [ThreadStatic] static IUIAutomation instance;
        public static IUIAutomation Get => instance ?? (instance = new CUIAutomation8());

        /// <summary>Nœud lu une fois : chaque propriété est un appel vers un autre processus.</summary>
        public sealed class Node
        {
            public IUIAutomationElement El;
            public Node Parent;
            public readonly List<Node> Children = new List<Node>();
            public int Type;
            public string Name = "", Class = "", Id = "";
            public IntPtr Hwnd;

            public IEnumerable<Node> Descendants()
            {
                foreach (var c in Children)
                {
                    yield return c;
                    foreach (var d in c.Descendants()) yield return d;
                }
            }

            public bool Under(Node ancestor)
            {
                for (var p = Parent; p != null; p = p.Parent) if (p == ancestor) return true;
                return false;
            }
        }

        // propriétés lues pour chaque nœud (identifiants UIA_*PropertyId)
        const int ControlTypeProp = 30003, NameProp = 30005, AutomationIdProp = 30011, ClassNameProp = 30012,
            NativeWindowHandleProp = 30020;
        const int ElementNotAvailable = unchecked((int)0x80040201); // UIA_E_ELEMENTNOTAVAILABLE
        static volatile bool cacheBroken;  // une exception du cache : parcours nœud par nœud ensuite

        /// <summary>Arbre brut sous `root`, borné en profondeur et en nombre de nœuds.</summary>
        public static Node Tree(IUIAutomationElement root, int maxDepth = 30, int maxNodes = 3000)
        {
            if (!cacheBroken)
            {
                string why;
                try
                {
                    var tree = CachedTree(root, maxDepth, maxNodes);
                    // cache vide alors que la vue brute a des enfants : on ne lui fait pas confiance
                    if (tree.Children.Count > 0 || Get.RawViewWalker.GetFirstChildElement(root) == null) return tree;
                    why = "sous-arbre vide";
                }
                catch (COMException e) when (e.HResult == ElementNotAvailable) { throw; } // fenêtre fermée entre-temps
                catch (Exception e) { why = e.GetType().Name + " " + e.Message; }
                // une fois pour toutes : ne pas payer le cache et le parcours à chaque lecture
                cacheBroken = true;
                Log.Write("UIA : cache abandonné, lecture nœud par nœud (" + why + ")");
            }
            return WalkedTree(root, maxDepth, maxNodes);
        }

        /// <summary>Tout le sous-arbre et ses propriétés en un appel (BuildUpdatedCache), au lieu
        /// de cinq appels vers l'Explorateur ou ShellExperienceHost par nœud.</summary>
        static Node CachedTree(IUIAutomationElement root, int maxDepth, int maxNodes)
        {
            var req = Get.CreateCacheRequest();
            foreach (int prop in new[] { ControlTypeProp, NameProp, AutomationIdProp, ClassNameProp, NativeWindowHandleProp })
                req.AddProperty(prop);
            req.TreeScope = TreeScope.TreeScope_Subtree;
            req.TreeFilter = Get.RawViewCondition;
            int count = 0;
            Node Read(IUIAutomationElement e, Node parent, int depth)
            {
                var n = new Node
                {
                    El = e, Parent = parent, Type = e.CachedControlType, Name = e.CachedName ?? "",
                    Class = e.CachedClassName ?? "", Id = e.CachedAutomationId ?? "", Hwnd = e.CachedNativeWindowHandle,
                };
                count++;
                if (depth >= maxDepth) return n;
                var kids = e.GetCachedChildren();
                for (int i = 0; kids != null && i < kids.Length && count < maxNodes; i++)
                    n.Children.Add(Read(kids.GetElement(i), n, depth + 1));
                return n;
            }
            return Read(root.BuildUpdatedCache(req), null, 0);
        }

        static Node WalkedTree(IUIAutomationElement root, int maxDepth, int maxNodes)
        {
            var walker = Get.RawViewWalker;
            int count = 0;
            Node Read(IUIAutomationElement e, Node parent, int depth)
            {
                var n = new Node { El = e, Parent = parent };
                count++;
                try
                {
                    n.Type = e.CurrentControlType;
                    n.Name = e.CurrentName ?? "";
                    n.Class = e.CurrentClassName ?? "";
                    n.Id = e.CurrentAutomationId ?? "";
                    n.Hwnd = e.CurrentNativeWindowHandle;
                }
                catch (COMException) { return n; } // élément disparu pendant la lecture
                if (depth >= maxDepth) return n;
                IUIAutomationElement c = null;
                try { c = walker.GetFirstChildElement(e); } catch (COMException) { }
                while (c != null && count < maxNodes)
                {
                    n.Children.Add(Read(c, n, depth + 1));
                    try { c = walker.GetNextSiblingElement(c); } catch (COMException) { c = null; }
                }
                return n;
            }
            return Read(root, null, 0);
        }

        public static Node TreeOf(IntPtr hwnd, int maxDepth = 30, int maxNodes = 3000) =>
            hwnd == IntPtr.Zero ? null : Tree(Get.ElementFromHandle(hwnd), maxDepth, maxNodes);

        /// <summary>Arbre lisible (type, classe, AutomationId, nom), pour régler les heuristiques dans la VM.</summary>
        public static string Dump(Node root, int maxLines = 400)
        {
            var sb = new StringBuilder();
            int lines = 0;
            void Walk(Node n, int depth)
            {
                if (lines++ >= maxLines) return;
                sb.Append(' ', depth * 2).Append(n.Type).Append(" [").Append(n.Class).Append("] #").Append(n.Id)
                  .Append(" « ").Append(n.Name.Replace("\n", " / ")).Append(" »");
                if (n.Hwnd != IntPtr.Zero) sb.Append(" hwnd ").Append(n.Hwnd.ToInt64().ToString("x"));
                sb.Append('\n');
                foreach (var c in n.Children) Walk(c, depth + 1);
            }
            Walk(root, 0);
            return sb.ToString();
        }

        public static bool TryInvoke(IUIAutomationElement e)
        {
            if (e == null || !((object)e.GetCurrentPattern(InvokePattern) is IUIAutomationInvokePattern p)) return false;
            p.Invoke();
            return true;
        }

        public static bool Invokable(Node n)
        {
            try { return (object)n.El.GetCurrentPattern(InvokePattern) != null; }
            catch (COMException) { return false; }
        }

        public static string Key(IUIAutomationElement e)
        {
            try { return string.Join(".", (int[])(object)e.GetRuntimeId()); }
            catch (Exception) { return null; }
        }
    }

    /// <summary>
    /// Fil dédié à UI Automation : un appel UIA vers l'Explorateur ou ShellExperienceHost peut
    /// attendre plusieurs secondes, ni la souris ni la capture ne doivent en pâtir. Tâches en
    /// file, plus un tic périodique tant qu'il renvoie vrai (du travail reste) ; sinon le fil
    /// dort jusqu'à la tâche suivante, qui relance le tic.
    /// </summary>
    sealed class UiaWorker
    {
        readonly BlockingCollection<Action> jobs = new BlockingCollection<Action>();
        readonly string name;
        readonly int periodMs;
        readonly Func<bool> tick;

        public UiaWorker(string name, int periodMs, Func<bool> tick)
        {
            this.name = name;
            this.periodMs = periodMs;
            this.tick = tick;
            var t = new Thread(Loop) { IsBackground = true, Name = name };
            t.SetApartmentState(ApartmentState.MTA); // client UIA : pas de fil STA sans boucle de messages
            t.Start();
        }

        public void Post(Action job) => jobs.Add(job);

        void Loop()
        {
            var clock = Stopwatch.StartNew();
            long next = 0;
            bool idle = false;
            while (true)
            {
                int wait = idle ? Timeout.Infinite : (int)Math.Max(0, next - clock.ElapsedMilliseconds);
                if (jobs.TryTake(out var job, wait))
                {
                    Run(() => { job(); return true; });
                    if (idle) { idle = false; next = clock.ElapsedMilliseconds + periodMs; }
                    continue;
                }
                next = clock.ElapsedMilliseconds + periodMs;
                idle = !Run(tick);
            }
        }

        bool Run(Func<bool> a)
        {
            try { return a(); }
            catch (Exception e) { Log.Write(name + " : " + e.GetType().Name + " " + e.Message); return true; }
        }
    }

    /// <summary>
    /// Notifications de Windows vers l'hôte. Les bannières (toasts) sont des fenêtres
    /// « Windows.UI.Core.CoreWindow » de ShellExperienceHost.exe titrées « Nouvelle notification ».
    /// À l'apparition, la fenêtre ne contient qu'un ScrollViewer vide (essai réel) : le contenu
    /// arrive après coup. Elle est donc relue toutes les 100 ms, et à chaque événement
    /// StructureChanged, jusqu'à trouver les textes. La bannière n'est poussée hors de l'écran
    /// qu'une fois lue (ou après 2,5 s) : déplacée trop tôt, elle risquerait de ne jamais se remplir.
    /// UserNotificationListener lirait les notifications proprement mais exige une identité de
    /// paquet (MSIX), hors de portée d'un exécutable .NET Framework.
    /// </summary>
    sealed class Toasts
    {
        const string ToastClass = "Windows.UI.Core.CoreWindow";
        // titre de la fenêtre de bannière, selon la langue de Windows (en minuscules)
        static readonly string[] Titles = { "notification", "notifica", "benachrichtigung", "melding", "powiadomienie" };
        const int WatchMs = 30000, HideAfterMs = 2500, EmptyDumpMs = 3000, KeepSent = 64;

        [ComVisible(true)]
        public sealed class Watched : IUIAutomationStructureChangedEventHandler
        {
            public Toasts Owner;
            public IUIAutomationElement Root;
            public long Start, Until;
            public bool Hidden, EmptyLogged, Subscribed;
            public readonly HashSet<string> Seen = new HashSet<string>();   // déjà envoyées
            public HashSet<string> Last = new HashSet<string>();            // lues à la lecture précédente

            // appelé sur un fil d'UIA : la relecture se fait sur le fil des bannières
            public void HandleStructureChangedEvent(IUIAutomationElement sender, StructureChangeType changeType, int[] runtimeId) =>
                Owner.RequestPoll();
        }

        readonly Action<Dictionary<string, object>> send;
        readonly UiaWorker worker;
        readonly Stopwatch clock = Stopwatch.StartNew();
        // fil UIA seulement :
        readonly Dictionary<IntPtr, Watched> watched = new Dictionary<IntPtr, Watched>();
        readonly Dictionary<int, (IntPtr h, IUIAutomationElement el)> sent = new Dictionary<int, (IntPtr, IUIAutomationElement)>();
        int nextId = 1;
        bool dumped;
        int pollPending;
        public volatile bool Enabled;

        public Toasts(Action<Dictionary<string, object>> send)
        {
            this.send = send;
            worker = new UiaWorker("vasistas-toasts", 100, Poll);
        }

        /// <summary>
        /// Option `notifications` de l'hôte : bannières permises dans Windows et relayées, ou coupées
        /// comme le fait boot.ps1.
        /// </summary>
        public void SetEnabled(bool on)
        {
            Enabled = on;
            try { Look.ToastsAllowed(on); }
            catch (Exception e) { Log.Write("notifications : " + e.Message); }
        }

        /// <summary>Fenêtre affichée (fil principal, événement Windows) : seules les bannières sont gardées.</summary>
        public void Shown(IntPtr h)
        {
            if (!Enabled || Native.ClassName(h) != ToastClass) return;
            worker.Post(() => Watch(h));
        }

        void RequestPoll()
        {
            if (Interlocked.Exchange(ref pollPending, 1) == 0)
                worker.Post(() => { pollPending = 0; Poll(); });
        }

        public void Activate(int id) => worker.Post(() =>
        {
            if (!sent.TryGetValue(id, out var s) || s.el == null || !Native.IsWindow(s.h)) return;
            try { if (!Uia.TryInvoke(s.el)) Log.Write("notification " + id + " : pas d'action"); }
            catch (COMException) { } // bannière déjà fermée : rien à faire
        });

        void Watch(IntPtr h)
        {
            if (!IsToast(h)) return;
            long now = clock.ElapsedMilliseconds;
            if (!watched.TryGetValue(h, out var w))
            {
                w = new Watched { Owner = this, Start = now, Root = Uia.Get.ElementFromHandle(h) };
                try
                {
                    Uia.Get.AddStructureChangedEventHandler(w.Root, TreeScope.TreeScope_Subtree, null, w);
                    w.Subscribed = true;
                }
                catch (Exception e) { Log.Write("bannière, StructureChanged : " + e.Message); }
                watched[h] = w;
            }
            w.Until = now + WatchMs;
            Read(h, w);
        }

        void Forget(IntPtr h, Watched w)
        {
            watched.Remove(h);
            if (!w.Subscribed) return;
            try { Uia.Get.RemoveStructureChangedEventHandler(w.Root, w); }
            catch (Exception) { }
        }

        static bool IsToast(IntPtr h)
        {
            if (!Native.IsWindow(h) || Native.ClassName(h) != ToastClass) return false;
            Native.GetWindowThreadProcessId(h, out uint pid);
            try
            {
                using (var p = Process.GetProcessById((int)pid))
                    if (!p.ProcessName.Equals("ShellExperienceHost", StringComparison.OrdinalIgnoreCase)) return false;
            }
            catch (Exception) { return false; }
            string title = Native.Title(h).ToLowerInvariant();
            return Titles.Any(title.Contains);
        }

        /// <summary>Relit les bannières suivies ; faux s'il n'en reste aucune (le fil s'endort).</summary>
        bool Poll()
        {
            if (watched.Count == 0) return false;
            long now = clock.ElapsedMilliseconds;
            foreach (var kv in watched.ToList())
            {
                var h = kv.Key;
                // bannière fermée ou masquée par Windows : la fenêtre peut resservir pour la suivante
                if (!Enabled || !Native.IsWindow(h) || !Native.IsWindowVisible(h) || Native.Cloaked(h) || now > kv.Value.Until)
                {
                    Forget(h, kv.Value);
                    continue;
                }
                Read(h, kv.Value);
            }
            return watched.Count > 0;
        }

        /// <summary>Bannière poussée hors de l'écran, sans la fermer : elle reste cliquable par UIA.</summary>
        static void Hide(IntPtr h)
        {
            Native.GetWindowRect(h, out RECT r);
            if (r.Right <= -10000 || r.Bottom <= -10000) return;
            Native.SetWindowPos(h, IntPtr.Zero, -32000, -32000, 0, 0, Native.SWP_NOSIZE | Native.SWP_NOZORDER | Native.SWP_NOACTIVATE);
        }

        void Read(IntPtr h, Watched w)
        {
            long age = clock.ElapsedMilliseconds - w.Start;
            Uia.Node root;
            try { root = Uia.Tree(Uia.Get.ElementFromHandle(h), 25, 500); }
            catch (COMException) { return; }
            var banners = Banners(root);
            if (banners.Count > 0 && !dumped)
            {
                dumped = true;
                Log.Write($"bannière de notification lue après {age} ms (UIA) :\n" + Uia.Dump(root));
            }
            else if (banners.Count == 0 && age >= EmptyDumpMs && !w.EmptyLogged)
            {
                w.EmptyLogged = true;
                Log.Write($"bannière de notification toujours vide après {age} ms (UIA) :\n" + Uia.Dump(root));
            }
            var now = new HashSet<string>();
            foreach (var (el, texts) in banners)
            {
                string sig = string.Join("\n", texts);
                now.Add(sig);
                // envoyée quand deux lectures successives concordent : la bannière se remplit en
                // plusieurs fois, une lecture trop tôt donnerait une notification tronquée
                if (!w.Last.Contains(sig) || !w.Seen.Add(sig)) continue;
                int id = nextId++;
                sent[id] = (h, el);
                if (sent.Count > KeepSent) sent.Remove(sent.Keys.Min());
                Split(texts, out string appName, out string title, out string body);
                var app = Apps.ByName(appName);
                send(new Dictionary<string, object>
                {
                    { "t", "notify" }, { "id", id }, { "app", app?.Id ?? "" }, { "appName", appName },
                    { "title", title }, { "body", body },
                });
            }
            w.Last = now;
            if (!w.Hidden && (w.Seen.Count > 0 || age >= HideAfterMs))
            {
                w.Hidden = true;
                Hide(h);
            }
        }

        /// <summary>
        /// Textes d'une bannière Windows 11, dans l'ordre : nom de l'application (en-tête), titre,
        /// lignes du corps. Avec deux textes seulement, pas d'en-tête.
        /// </summary>
        static void Split(List<string> texts, out string appName, out string title, out string body)
        {
            appName = title = body = "";
            if (texts.Count >= 3)
            {
                appName = texts[0];
                title = texts[1];
                body = string.Join("\n", texts.Skip(2));
            }
            else if (texts.Count == 2) { title = texts[0]; body = texts[1]; }
            else if (texts.Count == 1) title = texts[0];
        }

        /// <summary>
        /// Bannières d'une fenêtre (il peut y en avoir plusieurs empilées) : éléments cliquables
        /// (InvokePattern) portant au moins deux textes, les plus extérieurs. Les boutons d'action
        /// (Fermer, Répondre) n'en portent qu'un. Sans élément Text, le nom d'un élément cliquable
        /// sur plusieurs lignes en tient lieu. À défaut, toute la fenêtre, sans action.
        /// </summary>
        static List<(IUIAutomationElement el, List<string> texts)> Banners(Uia.Node root)
        {
            var found = new List<(Uia.Node node, List<string> texts)>();
            foreach (var n in root.Descendants())
            {
                if (!Uia.Invokable(n)) continue;
                var texts = Texts(n);
                if (texts.Count < 2)
                {
                    var lines = n.Name.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries)
                                      .Select(s => s.Trim()).Where(s => s.Length > 0).ToList();
                    if (lines.Count >= 2) texts = lines;
                }
                if (texts.Count >= 2) found.Add((n, texts));
            }
            found.RemoveAll(c => found.Any(o => o.node != c.node && c.node.Under(o.node)));
            var result = found.Select(f => (f.node.El, f.texts)).ToList();
            if (result.Count == 0)
            {
                var texts = Texts(root);
                if (texts.Count > 0) result.Add((null, texts));
            }
            return result;
        }

        /// <summary>Textes (éléments Text) sous `n`, sauf les libellés des boutons d'action.</summary>
        static List<string> Texts(Uia.Node n)
        {
            var buttons = n.Descendants().Where(d => d.Type == Uia.Button).ToList();
            var texts = new List<string>();
            foreach (var t in n.Descendants())
            {
                if (t.Type != Uia.Text || buttons.Any(b => t.Under(b))) continue;
                string s = t.Name.Trim();
                if (s.Length > 0 && !texts.Contains(s)) texts.Add(s);
            }
            return texts;
        }
    }

    /// <summary>
    /// Icônes de la zone de notification de Windows (OneDrive, Office…) vers l'hôte, relues toutes
    /// les 3 s. Par UI Automation d'abord : boutons XAML « NotifyItemIcon » de la barre des tâches
    /// (Windows 11 22H2 et suivants), lue depuis Shell_TrayWnd et depuis ses fenêtres
    /// DesktopWindowContentBridge (île XAML), ou boutons des barres d'outils de SysPager et
    /// NotifyIconOverflowWindow (ancienne zone Win32). Les icônes cachées sont toutes promues dans
    /// la zone visible (NotifyIconSettings\IsPromoted) : la fenêtre de dépassement n'existe
    /// qu'ouverte, et l'ouvrir volerait le premier plan. Si UIA ne trouve rien (barre XAML pas
    /// chargée, barre masquée), repli sur NotifyIconSettings : entrées dont le processus tourne,
    /// sans menu (voir DoClick).
    /// </summary>
    sealed class Tray
    {
        const string Settings = @"Control Panel\NotifyIconSettings";
        const string Bridge = "Windows.UI.Composition.DesktopWindowContentBridge";

        sealed class Item
        {
            public string Key, Tooltip, Exe = "", Png = "", Source = "uia";
            public IUIAutomationElement El;
        }

        sealed class Setting
        {
            public string Key, Exe, Tip;
            public byte[] Snapshot;
        }

        readonly Action<Dictionary<string, object>> send;
        readonly UiaWorker worker;
        readonly Stopwatch clock = Stopwatch.StartNew();
        // fil UIA seulement :
        readonly Dictionary<string, string> pngByExe = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        string lastSig, lastSource;
        long nextPromote;
        bool dumped;
        public volatile bool Enabled;

        readonly Func<bool> hostReady;

        public Tray(Action<Dictionary<string, object>> send, Func<bool> hostReady)
        {
            this.send = send;
            this.hostReady = hostReady;
            worker = new UiaWorker("vasistas-tray", 3000, Tick);
        }

        public void SetEnabled(bool on)
        {
            Enabled = on;
            Resend();
        }

        /// <summary>Nouvel hôte : liste renvoyée tout de suite, même sans changement.</summary>
        public void Resend() => worker.Post(() => { lastSig = null; nextPromote = 0; Tick(); });

        public void Click(string key, string button, int x, int y) => worker.Post(() => DoClick(key, button));

        /// <summary>Faux (le fil s'endort) si coupé ou sans hôte : Resend, appelé à chaque hello
        /// et à l'activation, relance la lecture.</summary>
        bool Tick()
        {
            if (!Enabled || !hostReady()) return false;
            long now = clock.ElapsedMilliseconds;
            if (now >= nextPromote)
            {
                nextPromote = now + 60000;
                Promote();
            }
            var items = Enumerate();
            string source = items.Count > 0 ? items[0].Source : "";
            if (source != lastSource && source.Length > 0)
            {
                lastSource = source;
                Log.Write("zone de notification : " + items.Count + " icône(s) par " +
                          (source == "uia" ? "UI Automation" : "NotifyIconSettings (repli, sans menu)"));
            }
            string sig = string.Join("\u0001", items.Select(i => i.Key + "\u0002" + i.Tooltip + "\u0002" + i.Exe + "\u0002" + i.Png.GetHashCode()));
            if (sig == lastSig) return true;
            lastSig = sig;
            send(new Dictionary<string, object>
            {
                { "t", "tray" },
                { "items", items.Select(i => (object)new Dictionary<string, object>
                    { { "key", i.Key }, { "tooltip", i.Tooltip }, { "png", i.Png }, { "exe", i.Exe }, { "source", i.Source } }).ToArray() },
            });
            return true;
        }

        List<Item> Enumerate()
        {
            var settings = ReadSettings();
            var items = FromUia(settings);
            return items.Count > 0 ? items : FromSettings(settings);
        }

        List<Item> FromUia(List<Setting> settings)
        {
            var nodes = new List<Uia.Node>();
            var seen = new HashSet<string>();
            void Add(Uia.Node n)
            {
                string k = Uia.Key(n.El);
                if (k == null || seen.Add(k)) nodes.Add(n);
            }
            bool XamlIcon(Uia.Node n) => n.Id == "NotifyItemIcon" || n.Class == "SystemTray.NormalButton";

            var bar = Native.FindWindow("Shell_TrayWnd", null);
            Uia.Node barTree = null;
            if (bar != IntPtr.Zero)
            {
                // ancienne zone Win32 : boutons de la barre d'outils de SysPager
                var pager = Native.FindWindowEx(Native.FindWindowEx(bar, IntPtr.Zero, "TrayNotifyWnd", null), IntPtr.Zero, "SysPager", null);
                var tb = Uia.TreeOf(Native.FindWindowEx(pager, IntPtr.Zero, "ToolbarWindow32", null), 2);
                if (tb != null) foreach (var c in tb.Children) if (c.Type == Uia.Button) Add(c);
                // zone XAML, depuis la barre et depuis chaque île XAML (DesktopWindowContentBridge)
                barTree = Uia.TreeOf(bar);
                foreach (var n in barTree.Descendants()) if (XamlIcon(n)) Add(n);
                // l'île est sous la barre : relue à part seulement si la vue brute de la barre n'y entre pas
                if (nodes.Count == 0)
                    for (var br = Native.FindWindowEx(bar, IntPtr.Zero, Bridge, null); br != IntPtr.Zero; br = Native.FindWindowEx(bar, br, Bridge, null))
                    foreach (var n in Uia.TreeOf(br).Descendants()) if (XamlIcon(n)) Add(n);
            }
            var overflow = Native.FindWindow("NotifyIconOverflowWindow", null);
            if (overflow != IntPtr.Zero)
            {
                var tb = Uia.TreeOf(Native.FindWindowEx(overflow, IntPtr.Zero, "ToolbarWindow32", null), 2);
                if (tb != null) foreach (var c in tb.Children) if (c.Type == Uia.Button) Add(c);
            }
            var xo = Uia.TreeOf(Native.FindWindow("TopLevelWindowForOverflowXamlIsland", null));
            if (xo != null) foreach (var n in xo.Descendants()) if (XamlIcon(n)) Add(n);

            var items = new List<Item>();
            foreach (var n in nodes)
            {
                string tip = n.Name.Trim();
                if (tip.Length == 0) continue;
                var item = new Item { Tooltip = tip, El = n.El };
                var s = Match(tip, settings);
                if (s != null)
                {
                    settings.Remove(s);
                    item.Key = "nis:" + s.Key;
                    item.Exe = s.Exe ?? "";
                    item.Png = Snapshot(s);
                }
                if (item.Png.Length == 0) item.Png = ExeIcon(item.Exe);
                items.Add(item);
            }
            Unique(items);
            if (items.Count == 0 && barTree != null && !dumped)
            {
                dumped = true;
                var sb = new StringBuilder("zone de notification : aucune icône par UI Automation, repli sur NotifyIconSettings ; barre des tâches (UIA brute) :\n");
                sb.Append(Uia.Dump(barTree));
                for (var br = Native.FindWindowEx(bar, IntPtr.Zero, Bridge, null); br != IntPtr.Zero; br = Native.FindWindowEx(bar, br, Bridge, null))
                    sb.Append("île XAML ").Append(br.ToInt64().ToString("x")).Append(" :\n").Append(Uia.Dump(Uia.TreeOf(br)));
                Log.Write(sb.ToString());
            }
            return items;
        }

        /// <summary>Repli sans UI Automation : entrées de NotifyIconSettings dont le processus tourne.</summary>
        List<Item> FromSettings(List<Setting> settings)
        {
            var running = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var p in Process.GetProcesses())
            {
                try { string exe = Apps.ExePath((uint)p.Id, out _); if (exe != null) running.Add(exe); }
                catch (Exception) { }
                finally { p.Dispose(); }
            }
            var items = new List<Item>();
            foreach (var s in settings)
            {
                if (string.IsNullOrEmpty(s.Exe) || !running.Contains(s.Exe)) continue;
                string tip = s.Tip;
                if (string.IsNullOrEmpty(tip))
                {
                    tip = Description(s.Exe);
                    if (string.IsNullOrEmpty(tip)) tip = Path.GetFileNameWithoutExtension(s.Exe);
                }
                var item = new Item { Key = "nis:" + s.Key, Tooltip = tip, Exe = s.Exe, Png = Snapshot(s), Source = "registry" };
                if (item.Png.Length == 0) item.Png = ExeIcon(s.Exe);
                items.Add(item);
            }
            Unique(items);
            return items;
        }

        static void Unique(List<Item> items)
        {
            var keys = new HashSet<string>();
            foreach (var item in items)
            {
                string baseKey = item.Key ?? "tip:" + item.Tooltip, key = baseKey;
                for (int n = 2; !keys.Add(key); n++) key = baseKey + "#" + n;
                item.Key = key;
            }
        }

        static string Snapshot(Setting s) =>
            s.Snapshot != null && s.Snapshot.Length > 8 && s.Snapshot[0] == 0x89 && s.Snapshot[1] == (byte)'P'
                ? Convert.ToBase64String(s.Snapshot) : "";

        string ExeIcon(string exe)
        {
            if (string.IsNullOrEmpty(exe)) return "";
            if (!pngByExe.TryGetValue(exe, out var png))
            {
                try { png = Apps.IconPng(exe, 64); } catch (Exception) { png = null; }
                pngByExe[exe] = png = png ?? "";
            }
            return png;
        }

        /// <summary>
        /// Entrée de NotifyIconSettings d'une icône : infobulle initiale identique, sinon nom de
        /// l'exécutable ou description du fichier contenus dans l'infobulle (« OneDrive - Personnel »).
        /// </summary>
        static Setting Match(string tip, List<Setting> settings)
        {
            var s = settings.FirstOrDefault(x => string.Equals(x.Tip, tip, StringComparison.OrdinalIgnoreCase));
            if (s != null) return s;
            foreach (var x in settings)
            {
                if (string.IsNullOrEmpty(x.Exe)) continue;
                string stem = Path.GetFileNameWithoutExtension(x.Exe);
                if (stem.Length >= 3 && tip.IndexOf(stem, StringComparison.OrdinalIgnoreCase) >= 0) return x;
                string desc = Description(x.Exe);
                if (desc.Length > 0 && tip.IndexOf(desc, StringComparison.OrdinalIgnoreCase) >= 0) return x;
            }
            return null;
        }

        // fil de la zone de notification seulement : description relue au plus une fois par exécutable
        static readonly Dictionary<string, string> descByExe = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        static string Description(string exe)
        {
            if (!descByExe.TryGetValue(exe, out var desc))
            {
                try { desc = FileVersionInfo.GetVersionInfo(exe).FileDescription?.Trim(); } catch (Exception) { }
                descByExe[exe] = desc = desc ?? "";
            }
            return desc;
        }

        static List<Setting> ReadSettings()
        {
            var list = new List<Setting>();
            using (var k = Registry.CurrentUser.OpenSubKey(Settings))
            {
                if (k == null) return list;
                foreach (var name in k.GetSubKeyNames())
                    using (var s = k.OpenSubKey(name))
                    {
                        if (s == null) continue;
                        list.Add(new Setting
                        {
                            Key = name, Exe = KnownPath(s.GetValue("ExecutablePath") as string),
                            Tip = (s.GetValue("InitialTooltip") as string)?.Trim(), Snapshot = s.GetValue("IconSnapshot") as byte[],
                        });
                    }
            }
            return list;
        }

        /// <summary>
        /// Toutes les icônes dans la zone visible : IsPromoted=1 pour chaque entrée de
        /// NotifyIconSettings (Windows 11 23H2 et suivants), et EnableAutoTray=0 (versions antérieures).
        /// </summary>
        static void Promote()
        {
            int n = 0;
            using (var k = Registry.CurrentUser.OpenSubKey(Settings))
            {
                if (k != null)
                    foreach (var name in k.GetSubKeyNames())
                        using (var s = k.OpenSubKey(name, true))
                        {
                            if (s == null || (s.GetValue("IsPromoted") is int v && v == 1)) continue;
                            s.SetValue("IsPromoted", 1, RegistryValueKind.DWord);
                            n++;
                        }
            }
            using (var k = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Explorer"))
                if (!(k.GetValue("EnableAutoTray") is int a && a == 0)) { k.SetValue("EnableAutoTray", 0, RegistryValueKind.DWord); n++; }
            if (n > 0) Log.Write($"zone de notification : {n} icône(s) cachée(s) rendue(s) visible(s)");
        }

        /// <summary>
        /// Clic sur une icône. Gauche : InvokePattern, sans toucher au pointeur. Droit (ou gauche
        /// sans Invoke) : vrai clic, après avoir fait sortir la barre des tâches masquée en
        /// amenant le pointeur au bas de l'écran ; le menu s'ouvre près de l'icône, en bas à droite
        /// de l'écran de Windows, et arrive à l'hôte comme une fenêtre `popup`. Icône du repli
        /// (NotifyIconSettings) : gauche = fenêtre du programme au premier plan, ou programme
        /// relancé s'il n'en a pas ; droit = rien, le menu n'est pas atteignable sans UIA.
        /// </summary>
        void DoClick(string key, string button)
        {
            var item = Enumerate().FirstOrDefault(i => i.Key == key);
            if (item == null) { Log.Write("zone de notification : icône " + key + " introuvable"); return; }
            if (item.El == null)
            {
                if (button == "right") { Log.Write("zone de notification : menu de " + key + " inaccessible (repli sans UIA)"); return; }
                ActivateExe(item.Exe);
                return;
            }
            if (button != "right")
            {
                try { if (Uia.TryInvoke(item.El)) return; }
                catch (COMException e) { Log.Write("zone de notification, Invoke : " + e.Message); }
            }
            if (!Reveal()) Log.Write("zone de notification : la barre des tâches ne s'affiche pas");
            tagRECT r;
            try { r = item.El.CurrentBoundingRectangle; }
            catch (COMException) { Log.Write("zone de notification : icône " + key + " disparue"); return; }
            int sw = Native.GetSystemMetrics(Native.SM_CXSCREEN), sh = Native.GetSystemMetrics(Native.SM_CYSCREEN);
            int cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
            if (r.right <= r.left || cx < 0 || cy < 0 || cx >= sw || cy >= sh)
            {
                Log.Write($"zone de notification : icône {key} hors de l'écran ({r.left},{r.top},{r.right},{r.bottom})");
                return;
            }
            int b = button == "right" ? 3 : 1;
            Input.MoveTo(cx, cy);
            Thread.Sleep(30);
            Input.Button(b, true);
            Input.Button(b, false);
        }

        static void ActivateExe(string exe)
        {
            if (string.IsNullOrEmpty(exe)) return;
            foreach (var p in Process.GetProcesses())
            {
                try
                {
                    if (p.MainWindowHandle == IntPtr.Zero) continue;
                    if (!string.Equals(Apps.ExePath((uint)p.Id, out _), exe, StringComparison.OrdinalIgnoreCase)) continue;
                    Native.ForceForeground(p.MainWindowHandle);
                    return;
                }
                catch (Exception) { }
                finally { p.Dispose(); }
            }
            try { Process.Start(new ProcessStartInfo(exe) { UseShellExecute = true }); }
            catch (Exception e) { Log.Write("zone de notification : " + exe + " : " + e.Message); }
        }

        /// <summary>Barre des tâches masquée automatiquement : sortie en amenant le pointeur au bas de l'écran.</summary>
        static bool Reveal()
        {
            var bar = Native.FindWindow("Shell_TrayWnd", null);
            if (bar == IntPtr.Zero) return false;
            int sw = Native.GetSystemMetrics(Native.SM_CXSCREEN), sh = Native.GetSystemMetrics(Native.SM_CYSCREEN);
            Native.GetWindowRect(bar, out RECT r);
            if (r.Bottom <= sh) return true;
            Input.MoveTo(sw - 40, sh - 1);
            for (int i = 0; i < 40; i++)
            {
                Thread.Sleep(25);
                Native.GetWindowRect(bar, out r);
                if (r.Bottom <= sh) { Thread.Sleep(50); return true; } // icônes XAML posées
            }
            return false;
        }

        [DllImport("shell32.dll")] static extern int SHGetKnownFolderPath(ref Guid id, uint flags, IntPtr token, out IntPtr path);

        /// <summary>« {GUID de dossier connu}\Microsoft OneDrive\OneDrive.exe » en chemin complet.</summary>
        static string KnownPath(string p)
        {
            if (string.IsNullOrEmpty(p) || p[0] != '{') return p;
            int end = p.IndexOf('}');
            if (end < 0 || !Guid.TryParse(p.Substring(0, end + 1), out var id)) return p;
            if (SHGetKnownFolderPath(ref id, 0, IntPtr.Zero, out IntPtr ptr) != 0) return p;
            try { return Marshal.PtrToStringUni(ptr) + p.Substring(end + 1); }
            finally { Marshal.FreeCoTaskMem(ptr); }
        }
    }
}
