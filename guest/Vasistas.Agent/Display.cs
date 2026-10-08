using System;
using System.Runtime.InteropServices;

namespace Vasistas.Agent
{
    /// <summary>
    /// Échelle d'affichage de Windows, changée à chaud comme le fait la page Paramètres :
    /// DisplayConfigGetDeviceInfo / SetDeviceInfo avec les types non documentés -3 et -4.
    /// L'échelle y est un rang relatif à l'échelle recommandée, dans la liste ci-dessous.
    /// </summary>
    static class Display
    {
        static readonly int[] Scales = { 100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500 };
        const int GetDpiScale = -3, SetDpiScale = -4;
        const uint QDC_ONLY_ACTIVE_PATHS = 2;

        [StructLayout(LayoutKind.Sequential)]
        struct Header
        {
            public int type;
            public int size;
            public uint adapterLow;
            public int adapterHigh;
            public uint id;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct GetScale
        {
            public Header header;
            public int minRel, curRel, maxRel;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct SetScale
        {
            public Header header;
            public int rel;
        }

        [DllImport("user32.dll")] static extern int GetDisplayConfigBufferSizes(uint flags, out uint paths, out uint modes);
        [DllImport("user32.dll")] static extern int QueryDisplayConfig(uint flags, ref uint numPaths, byte[] paths, ref uint numModes, byte[] modes, IntPtr topology);
        [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref GetScale info);
        [DllImport("user32.dll")] static extern int DisplayConfigSetDeviceInfo(ref SetScale info);

        const int PathSize = 72, ModeSize = 64;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct DEVMODE
        {
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
            public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;
            public int dmFields;
            public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;
            public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
            public short dmLogPixels;
            public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
            public int dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2;
            public int dmPanningWidth, dmPanningHeight;
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool EnumDisplaySettings(string device, int mode, ref DEVMODE dm);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int ChangeDisplaySettingsEx(string device, ref DEVMODE dm, IntPtr hwnd, uint flags, IntPtr param);
        const int ENUM_CURRENT_SETTINGS = -1;
        const int DM_PELSWIDTH = 0x80000, DM_PELSHEIGHT = 0x100000;
        static bool modesLogged;

        /// <summary>
        /// Écran de Windows au moins aussi grand que l'écran hôte (pixels physiques). Le pilote
        /// virtio-gpu ne propose qu'une liste fixe de modes : sans le mode exact (2880x1800 du
        /// portable, par exemple), on prend le plus petit mode qui le contient. Sinon une fenêtre
        /// agrandie sous Linux serait rognée à la taille de l'écran de Windows (bandes noires).
        /// </summary>
        public static void SetResolution(int width, int height)
        {
            var cur = new DEVMODE { dmSize = (short)Marshal.SizeOf<DEVMODE>() };
            bool haveCur = EnumDisplaySettings(null, ENUM_CURRENT_SETTINGS, ref cur);
            if (haveCur && cur.dmPelsWidth == width && cur.dmPelsHeight == height)
                return;
            DEVMODE? best = null, largest = null;
            var seen = new System.Collections.Generic.HashSet<string>();
            for (int i = 0; ; i++)
            {
                var m = new DEVMODE { dmSize = (short)Marshal.SizeOf<DEVMODE>() };
                if (!EnumDisplaySettings(null, i, ref m)) break;
                seen.Add(m.dmPelsWidth + "x" + m.dmPelsHeight);
                long area = (long)m.dmPelsWidth * m.dmPelsHeight;
                if (largest == null || area > (long)largest.Value.dmPelsWidth * largest.Value.dmPelsHeight) largest = m;
                if (m.dmPelsWidth >= width && m.dmPelsHeight >= height &&
                    (best == null || area < (long)best.Value.dmPelsWidth * best.Value.dmPelsHeight))
                    best = m;
            }
            var pick = best ?? largest;
            if (pick == null) return;
            var mode = pick.Value;
            if (haveCur && cur.dmPelsWidth == mode.dmPelsWidth && cur.dmPelsHeight == mode.dmPelsHeight)
                return; // déjà sur le meilleur mode possible
            mode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT;
            int rc = ChangeDisplaySettingsEx(null, ref mode, IntPtr.Zero, 0, IntPtr.Zero);
            string note = mode.dmPelsWidth == width && mode.dmPelsHeight == height ? "" : $" pour {width}x{height} demandé";
            Log.Write($"résolution {mode.dmPelsWidth}x{mode.dmPelsHeight}{note} (code {rc})");
            if (best == null && !modesLogged)
            {
                modesLogged = true;
                Log.Write($"aucun mode ne contient {width}x{height} ; modes : " + string.Join(" ", seen));
            }
        }

        /// <summary>Règle l'écran principal à l'échelle la plus proche de `percent`.</summary>
        public static void Apply(int percent)
        {
            if (GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, out uint np, out uint nm) != 0 || np == 0) return;
            var paths = new byte[np * PathSize];
            var modes = new byte[nm * ModeSize];
            if (QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ref np, paths, ref nm, modes, IntPtr.Zero) != 0) return;

            // DISPLAYCONFIG_PATH_INFO.sourceInfo : adapterId (LUID) puis id
            var h = new Header
            {
                adapterLow = BitConverter.ToUInt32(paths, 0),
                adapterHigh = BitConverter.ToInt32(paths, 4),
                id = BitConverter.ToUInt32(paths, 8),
            };
            var get = new GetScale { header = h };
            get.header.type = GetDpiScale;
            get.header.size = Marshal.SizeOf<GetScale>();
            if (DisplayConfigGetDeviceInfo(ref get) != 0) return;

            int recommended = -get.minRel; // rang de l'échelle recommandée dans Scales
            int target = 0;
            for (int i = 1; i < Scales.Length; i++)
                if (Math.Abs(Scales[i] - percent) < Math.Abs(Scales[target] - percent)) target = i;
            int rel = Math.Max(get.minRel, Math.Min(get.maxRel, target - recommended));
            if (rel == get.curRel) return;

            var set = new SetScale { header = h, rel = rel };
            set.header.type = SetDpiScale;
            set.header.size = Marshal.SizeOf<SetScale>();
            int rc = DisplayConfigSetDeviceInfo(ref set);
            Log.Write($"échelle demandée {percent} %, appliquée {Scales[Math.Max(0, Math.Min(Scales.Length - 1, recommended + rel))]} % (code {rc})");
        }
    }
}
