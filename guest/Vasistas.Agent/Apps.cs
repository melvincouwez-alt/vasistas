using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Vasistas.Agent
{
    /// <summary>
    /// Application d'une fenêtre : identifiant stable (nom de l'exécutable), nom lisible et
    /// icône. L'hôte s'en sert pour donner à chaque fenêtre sa propre entrée dans le dock.
    /// </summary>
    sealed class AppInfo
    {
        public string Id, Name, Exe;
        public string ImagePath; // exécutable réel, pour l'icône
    }

    static class Apps
    {
        static readonly Dictionary<uint, AppInfo> byPid = new Dictionary<uint, AppInfo>();

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref int size);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern uint PrivateExtractIcons(string file, int index, int cx, int cy, IntPtr[] icons, uint[] ids, uint count, uint flags);
        [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr icon);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        static extern int GetApplicationUserModelId(IntPtr process, ref int length, StringBuilder id);
        const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

        public static AppInfo Of(IntPtr hwnd)
        {
            Native.GetWindowThreadProcessId(hwnd, out uint pid);
            lock (byPid)
                if (byPid.TryGetValue(pid, out var known)) return known;
            string exe = ExePath(pid, out string aumid);
            var info = new AppInfo { Exe = exe ?? "", ImagePath = exe, Id = "windows", Name = "Windows" };
            if (exe != null)
            {
                string stem = Path.GetFileNameWithoutExtension(exe);
                var id = new StringBuilder();
                foreach (char c in stem.ToLowerInvariant())
                    if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) id.Append(c);
                if (id.Length > 0) info.Id = id.ToString();
                try
                {
                    var v = FileVersionInfo.GetVersionInfo(exe);
                    info.Name = !string.IsNullOrWhiteSpace(v.FileDescription) ? v.FileDescription.Trim() : stem;
                }
                catch (Exception) { info.Name = stem; }
                // nom de fichier en guise de description (applis du Store) : le titre de la
                // fenêtre donne le nom localisé, en fin de titre (« Sans titre - Bloc-notes »)
                if (info.Name.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) || info.Name == stem)
                {
                    string title = Native.Title(hwnd);
                    int dash = title.LastIndexOf(" - ", StringComparison.Ordinal);
                    string tail = dash >= 0 ? title.Substring(dash + 3).Trim() : title.Trim();
                    if (tail.Length > 0) info.Name = tail;
                }
                // appli empaquetée : lancée par son identifiant, WindowsApps n'est pas exécutable directement
                if (aumid != null) info.Exe = @"shell:AppsFolder\" + aumid;
            }
            lock (byPid)
            {
                if (byPid.Count > 512) byPid.Clear();
                byPid[pid] = info;
            }
            return info;
        }

        /// <summary>Application déjà vue (fenêtre suivie) dont le nom lisible est `name`, sinon null.</summary>
        public static AppInfo ByName(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return null;
            lock (byPid)
                foreach (var a in byPid.Values)
                    if (string.Equals(a.Name, name.Trim(), StringComparison.OrdinalIgnoreCase)) return a;
            return null;
        }

        internal static string ExePath(uint pid, out string aumid)
        {
            aumid = null;
            var h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
            if (h == IntPtr.Zero) return null;
            try
            {
                var id = new StringBuilder(256);
                int len = id.Capacity;
                if (GetApplicationUserModelId(h, ref len, id) == 0) aumid = id.ToString();
                var sb = new StringBuilder(1024);
                int size = sb.Capacity;
                return QueryFullProcessImageName(h, 0, sb, ref size) ? sb.ToString() : null;
            }
            finally { CloseHandle(h); }
        }

        /// <summary>Icône de l'exécutable en PNG `max`x`max` (256 par défaut, ou la plus grande disponible en dessous), base64.</summary>
        public static string IconPng(string exe, int max = 256)
        {
            if (string.IsNullOrEmpty(exe) || exe.StartsWith("shell:")) return null;
            var icons = new IntPtr[1];
            var ids = new uint[1];
            foreach (int size in new[] { 256, 128, 64, 48, 32, 16 })
            {
                if (size > max) continue;
                if (PrivateExtractIcons(exe, 0, size, size, icons, ids, 1, 0) == 0 || icons[0] == IntPtr.Zero) continue;
                try
                {
                    using (var icon = Icon.FromHandle(icons[0]))
                    using (var bmp = icon.ToBitmap())
                    using (var ms = new MemoryStream())
                    {
                        bmp.Save(ms, ImageFormat.Png);
                        return Convert.ToBase64String(ms.ToArray());
                    }
                }
                finally { DestroyIcon(icons[0]); }
            }
            return null;
        }
    }
}
