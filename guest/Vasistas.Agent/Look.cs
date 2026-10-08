using System;
using System.Linq;
using System.Threading;
using Microsoft.Win32;

namespace Vasistas.Agent
{
    /// <summary>
    /// Apparence de Windows réglée sur celle d'elementary : mode sombre, couleur d'accent,
    /// lissage des polices. Chaque valeur n'est écrite que si elle change, et Windows n'est
    /// prévenu (WM_SETTINGCHANGE) que s'il y a eu un changement.
    /// </summary>
    static class Look
    {
        const string Personalize = @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize";
        const string Dwm = @"Software\Microsoft\Windows\DWM";
        const string Accent = @"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent";
        const string Push = @"Software\Microsoft\Windows\CurrentVersion\PushNotifications";

        // Nuances de AccentPalette, du plus clair au plus foncé : écart de luminosité (TSL) par
        // rapport à l'accent. Relevé sur le bleu par défaut (0078d4 : 99ebff 4cc2ff 0091f8 0078d4
        // 0067c0 003e92 001a68), Windows ne publie pas sa formule.
        static readonly double[] Shades = { 0.38, 0.23, 0.07, 0, -0.04, -0.13, -0.21 };

        /// <summary>Mode sombre ou clair, et couleur d'accent si `accent` (« #rrggbb ») est donnée.</summary>
        public static void Theme(bool dark, string accent)
        {
            int changed = 0;
            using (var k = Registry.CurrentUser.CreateSubKey(Personalize))
            {
                changed += SetDword(k, "AppsUseLightTheme", dark ? 0u : 1u);
                changed += SetDword(k, "SystemUsesLightTheme", dark ? 0u : 1u);
            }
            if (Parse(accent, out int r, out int g, out int b))
            {
                // DWM AccentColor et Accent\AccentColorMenu sont en ABGR, ColorizationColor en ARGB
                // (alpha C4, celui que pose la page Paramètres)
                uint abgr = Abgr(r, g, b), argb = 0xC4000000u | (uint)(r << 16 | g << 8 | b);
                using (var k = Registry.CurrentUser.CreateSubKey(Dwm))
                {
                    changed += SetDword(k, "AccentColor", abgr);
                    changed += SetDword(k, "ColorizationColor", argb);
                    changed += SetDword(k, "ColorizationAfterglow", argb);
                }
                using (var k = Registry.CurrentUser.CreateSubKey(Accent))
                {
                    var old = k.GetValue("AccentPalette") as byte[];
                    var pal = Palette(r, g, b, old);
                    changed += SetDword(k, "AccentColorMenu", abgr);
                    changed += SetDword(k, "StartColorMenu", Abgr(pal[16], pal[17], pal[18])); // nuance foncée 1
                    if (old == null || !old.SequenceEqual(pal))
                    {
                        k.SetValue("AccentPalette", pal, RegistryValueKind.Binary);
                        changed++;
                    }
                }
                // l'accent ne suit plus le fond d'écran
                using (var k = Registry.CurrentUser.CreateSubKey(@"Control Panel\Desktop"))
                    changed += SetDword(k, "AutoColorization", 0);
            }
            if (changed == 0) return;
            Log.Write($"thème {(dark ? "sombre" : "clair")}{(accent != null ? ", accent " + accent : "")} ({changed} valeur(s))");
            Broadcast("ImmersiveColorSet");
        }

        /// <summary>Lissage des polices : `grayscale` (niveaux de gris), `cleartype` ou `none`.</summary>
        public static void Fonts(string smoothing)
        {
            bool on = smoothing != "none";
            int type = smoothing == "cleartype" ? Native.FE_FONTSMOOTHINGCLEARTYPE : Native.FE_FONTSMOOTHINGSTANDARD;
            Native.SystemParametersInfo(Native.SPI_GETFONTSMOOTHING, 0, out int curOn, 0);
            Native.SystemParametersInfo(Native.SPI_GETFONTSMOOTHINGTYPE, 0, out int curType, 0);
            if ((curOn != 0) == on && (!on || curType == type)) return;
            // SPIF_SENDCHANGE diffuse WM_SETTINGCHANGE et attend chaque fenêtre : hors du fil principal
            new Thread(() =>
            {
                uint f = Native.SPIF_UPDATEINIFILE | Native.SPIF_SENDCHANGE;
                if (on) Native.SystemParametersInfo(Native.SPI_SETFONTSMOOTHINGTYPE, 0, new IntPtr(type), f);
                Native.SystemParametersInfo(Native.SPI_SETFONTSMOOTHING, on ? 1u : 0u, IntPtr.Zero, f);
                Log.Write("lissage des polices : " + smoothing);
            }) { IsBackground = true, Name = "vasistas-fonts" }.Start();
        }

        /// <summary>
        /// Bannières de notification de Windows permises ou non (boot.ps1 les coupe à chaque
        /// ouverture de session). Vrai si la valeur a changé.
        /// </summary>
        public static bool ToastsAllowed(bool on)
        {
            using (var k = Registry.CurrentUser.CreateSubKey(Push))
            {
                if (SetDword(k, "ToastEnabled", on ? 1u : 0u) == 0) return false;
            }
            Log.Write("notifications de Windows " + (on ? "permises" : "coupées"));
            return true;
        }

        /// <summary>
        /// WM_SETTINGCHANGE à toutes les fenêtres, sur un fil à part : SendMessageTimeout attend
        /// chaque application (celles qui ne répondent plus sont sautées).
        /// </summary>
        static void Broadcast(string area)
        {
            new Thread(() => Native.SendMessageTimeout(Native.HWND_BROADCAST, Native.WM_SETTINGCHANGE, IntPtr.Zero, area,
                Native.SMTO_ABORTIFHUNG, 5000, out _)) { IsBackground = true, Name = "vasistas-settingchange" }.Start();
        }

        static int SetDword(RegistryKey k, string name, uint value)
        {
            if (k.GetValue(name) is int cur && unchecked((uint)cur) == value) return 0;
            k.SetValue(name, unchecked((int)value), RegistryValueKind.DWord);
            return 1;
        }

        static uint Abgr(int r, int g, int b) => 0xFF000000u | (uint)(b << 16 | g << 8 | r);

        static bool Parse(string s, out int r, out int g, out int b)
        {
            r = g = b = 0;
            if (string.IsNullOrEmpty(s) || s.Length != 7 || s[0] != '#') return false;
            if (!int.TryParse(s.Substring(1), System.Globalization.NumberStyles.HexNumber, null, out int v)) return false;
            r = (v >> 16) & 0xFF; g = (v >> 8) & 0xFF; b = v & 0xFF;
            return true;
        }

        /// <summary>
        /// AccentPalette : 8 couleurs de 4 octets (R, G, B, 0), du plus clair au plus foncé ;
        /// la 8e (couleur d'appoint, orange par défaut) est gardée.
        /// </summary>
        static byte[] Palette(int r, int g, int b, byte[] old)
        {
            var pal = new byte[32];
            RgbToHsl(r, g, b, out double h, out double s, out double l);
            for (int i = 0; i < Shades.Length; i++)
            {
                HslToRgb(h, s, Math.Max(0.05, Math.Min(0.95, l + Shades[i])), out int cr, out int cg, out int cb);
                if (Shades[i] == 0) { cr = r; cg = g; cb = b; }
                pal[i * 4] = (byte)cr; pal[i * 4 + 1] = (byte)cg; pal[i * 4 + 2] = (byte)cb;
            }
            if (old != null && old.Length == 32) Array.Copy(old, 28, pal, 28, 4);
            else { pal[28] = 0xF7; pal[29] = 0x63; pal[30] = 0x0C; }
            return pal;
        }

        static void RgbToHsl(int r, int g, int b, out double h, out double s, out double l)
        {
            double rf = r / 255.0, gf = g / 255.0, bf = b / 255.0;
            double max = Math.Max(rf, Math.Max(gf, bf)), min = Math.Min(rf, Math.Min(gf, bf)), d = max - min;
            l = (max + min) / 2;
            if (d == 0) { h = s = 0; return; }
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            if (max == rf) h = (gf - bf) / d + (gf < bf ? 6 : 0);
            else if (max == gf) h = (bf - rf) / d + 2;
            else h = (rf - gf) / d + 4;
            h /= 6;
        }

        static void HslToRgb(double h, double s, double l, out int r, out int g, out int b)
        {
            if (s == 0) { r = g = b = (int)Math.Round(l * 255); return; }
            double q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
            double Hue(double t)
            {
                if (t < 0) t += 1;
                if (t > 1) t -= 1;
                if (t < 1 / 6.0) return p + (q - p) * 6 * t;
                if (t < 1 / 2.0) return q;
                if (t < 2 / 3.0) return p + (q - p) * (2 / 3.0 - t) * 6;
                return p;
            }
            r = (int)Math.Round(Hue(h + 1 / 3.0) * 255);
            g = (int)Math.Round(Hue(h) * 255);
            b = (int)Math.Round(Hue(h - 1 / 3.0) * 255);
        }
    }
}
