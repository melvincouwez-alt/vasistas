using System;
using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace Vasistas.Agent
{
    // Minuterie de Windows à 1 ms tant que des fenêtres sont affichées sur l'hôte. Par défaut
    // (15,6 ms), DWM compose sur ce pas avec le pilote DOD : ~41 images/s au lieu de ~49 (mesures
    // du 2026-10-06). Depuis Windows 10 2004 la demande ne vaut que pour le processus appelant,
    // sauf avec GlobalTimerResolutionRequests=1 (lu au démarrage de Windows).
    static class TimerRes
    {
        const string Kernel = @"SYSTEM\CurrentControlSet\Control\Session Manager\kernel";
        const uint OneMs = 10000; // en 100 ns

        [DllImport("ntdll.dll")]
        static extern int NtSetTimerResolution(uint desired, bool set, out uint current);

        public static bool Held { get; private set; }

        public static void EnsureGlobal()
        {
            try
            {
                using (var k = Registry.LocalMachine.CreateSubKey(Kernel))
                {
                    if (k.GetValue("GlobalTimerResolutionRequests") is int v && v == 1) return;
                    k.SetValue("GlobalTimerResolutionRequests", 1, RegistryValueKind.DWord);
                    Log.Write("minuterie globale activée, effective au prochain démarrage de Windows");
                }
            }
            catch (Exception e) { Log.Write("minuterie globale : " + e.Message); }
        }

        public static void Set(bool hold)
        {
            if (hold == Held) return;
            NtSetTimerResolution(OneMs, hold, out uint cur);
            Held = hold;
            Log.Write("minuterie " + (hold ? "tenue" : "rendue") + ", résolution " + cur / 10000.0 + " ms");
        }
    }
}
