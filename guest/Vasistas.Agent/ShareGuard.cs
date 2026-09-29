using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace Vasistas.Agent
{
    /// <summary>
    /// Lecteurs des dossiers Linux (virtiofs monté par WinFsp). Quand un nouveau dossier est
    /// branché à chaud, Windows réinitialise aussi les autres périphériques virtio-fs : leurs
    /// instances WinFsp restent accrochées à l'ancienne session et le lecteur ne répond plus
    /// (la lettre existe, le dossier racine est introuvable ; Word : « erreur lors de
    /// l'ouverture du fichier »). Toutes les 30 s, et avant d'ouvrir un fichier sur l'un d'eux,
    /// un lecteur mort est remonté (launchctl stop puis start). Un lecteur qui répond n'est
    /// jamais touché : des documents peuvent y être ouverts.
    /// </summary>
    static class ShareGuard
    {
        const string LaunchCtl = @"C:\Program Files (x86)\WinFsp\bin\launchctl-x64.exe";
        const int PeriodMs = 30000, CheckTimeoutMs = 3000, RefreshMs = 300000;
        static readonly object Lock = new object();
        static List<Instance> instances = new List<Instance>();
        static long refreshedAt = long.MinValue;
        static readonly Stopwatch Clock = Stopwatch.StartNew();
        static Timer timer;

        sealed class Instance
        {
            public string Name, Tag, Drive;
        }

        public static void Start()
        {
            if (File.Exists(LaunchCtl))
                timer = new Timer(_ => HealAll(), null, 5000, PeriodMs);
        }

        /// <summary>Vrai si le chemin est sur un lecteur autre que C: (« Y:\rapport.docx »).</summary>
        public static bool IsSharePath(string path) =>
            path != null && path.Length >= 3 && char.IsLetter(path[0]) && path[1] == ':' && path[2] == '\\'
            && char.ToUpperInvariant(path[0]) != 'C';

        /// <summary>Remonte, si besoin, le lecteur de ce chemin avant qu'une application l'ouvre.</summary>
        public static void EnsureFor(string path)
        {
            if (!IsSharePath(path) || !File.Exists(LaunchCtl))
                return;
            string drive = char.ToUpperInvariant(path[0]) + ":";
            lock (Lock)
            {
                if (Alive(drive))
                    return;
                Refresh(force: true);
                var inst = instances.FirstOrDefault(i => i.Drive == drive);
                if (inst != null)
                    Heal(inst);
            }
        }

        static void HealAll()
        {
            try
            {
                lock (Lock)
                {
                    Refresh(force: false);
                    foreach (var inst in instances.Where(i => !Alive(i.Drive)).ToList())
                    {
                        Refresh(force: true); // une instance peut avoir été arrêtée exprès
                        if (instances.Any(i => i.Name == inst.Name) && !Alive(inst.Drive))
                            Heal(inst);
                    }
                }
            }
            catch (Exception e)
            {
                Log.Write("lecteurs partagés : " + e.Message);
            }
        }

        static void Refresh(bool force)
        {
            long now = Clock.ElapsedMilliseconds;
            if (!force && now - refreshedAt < RefreshMs)
                return;
            refreshedAt = now;
            var found = new List<Instance>();
            foreach (var line in Run("list").Split('\n'))
            {
                var parts = line.Trim().Split(' ');
                if (parts.Length != 2 || parts[0] != "virtiofs")
                    continue;
                var m = Regex.Match(Run("info virtiofs " + parts[1]), "-t \"([^\"]+)\" -m \"([A-Za-z]:)\"");
                if (m.Success)
                    found.Add(new Instance { Name = parts[1], Tag = m.Groups[1].Value, Drive = m.Groups[2].Value.ToUpperInvariant() });
            }
            instances = found;
        }

        /// <summary>Le dossier racine répond ; délai borné (un montage mort peut aussi bloquer).</summary>
        static bool Alive(string drive)
        {
            var t = Task.Run(() => Directory.Exists(drive + "\\"));
            return t.Wait(CheckTimeoutMs) && t.Result;
        }

        static void Heal(Instance inst)
        {
            Log.Write($"lecteur {inst.Drive} ({inst.Tag}) ne répond plus : remontage");
            Run("stop virtiofs " + inst.Name);
            Thread.Sleep(1000);
            Run($"start virtiofs {inst.Name} {inst.Tag} {inst.Drive}");
            for (int i = 0; i < 20 && !Alive(inst.Drive); i++)
                Thread.Sleep(500);
            Log.Write($"lecteur {inst.Drive} : " + (Alive(inst.Drive) ? "remonté" : "toujours absent"));
        }

        static string Run(string args)
        {
            var psi = new ProcessStartInfo(LaunchCtl, args)
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
            };
            using (var p = Process.Start(psi))
            {
                string output = p.StandardOutput.ReadToEnd();
                p.WaitForExit(10000);
                return output;
            }
        }
    }
}
