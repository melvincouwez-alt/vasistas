using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Runtime.InteropServices;

namespace Vasistas.Agent
{
    /// <summary>
    /// Compression des tuiles par zstd natif (libzstd.dll officielle 1.5.7, win64), 2,5 fois plus
    /// rapide que deflate pour un taux un peu meilleur. La DLL est embarquée compressée dans l'exe
    /// (qui reste seul à copier) et posée dans %LOCALAPPDATA%\Vasistas\zstd-1.5.7 au premier usage.
    /// Ready faux (DLL refusée, absente…) : deflate comme avant.
    /// </summary>
    static class Zstd
    {
        const string Dll = "libzstd.dll";
        public const int Bound = 64 * 64 * 4 + 1024; // ZSTD_compressBound(16384) = 16 512

        public static readonly bool Ready = Load();

        [ThreadStatic] static IntPtr cctx;

        static bool Load()
        {
            try
            {
                byte[] dll;
                using (var gz = new GZipStream(typeof(Zstd).Assembly.GetManifestResourceStream("libzstd.dll.gz"), CompressionMode.Decompress))
                using (var ms = new MemoryStream())
                {
                    gz.CopyTo(ms);
                    dll = ms.ToArray();
                }
                string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Vasistas", "zstd-1.5.7");
                string path = Path.Combine(dir, Dll);
                // réécrite si elle diffère de celle de l'exe : jamais de DLL étrangère chargée
                if (!File.Exists(path) || !File.ReadAllBytes(path).SequenceEqual(dll))
                {
                    Directory.CreateDirectory(dir);
                    string tmp = path + "." + Environment.TickCount + ".tmp";
                    File.WriteAllBytes(tmp, dll);
                    if (File.Exists(path)) File.Delete(path);
                    File.Move(tmp, path);
                }
                // DllImport("libzstd.dll") retrouve le module déjà chargé par son nom
                if (LoadLibrary(path) == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                Log.Write("zstd " + ZSTD_versionNumber() + " prêt");
                return true;
            }
            catch (Exception e)
            {
                Log.Write("zstd écarté, deflate : " + e.Message);
                return false;
            }
        }

        /// <summary>Compresse len octets de src dans dst (Bound octets au moins) ; -1 si échec.</summary>
        public static unsafe int Compress(byte[] src, int len, byte[] dst)
        {
            if (cctx == IntPtr.Zero) cctx = ZSTD_createCCtx(); // une par fil, gardée pour sa vie
            if (cctx == IntPtr.Zero) return -1;
            fixed (byte* s = src, d = dst)
            {
                var n = ZSTD_compressCCtx(cctx, d, (UIntPtr)dst.Length, s, (UIntPtr)len, 1);
                return ZSTD_isError(n) != 0 ? -1 : (int)n;
            }
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr LoadLibrary(string path);
        [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)] static extern uint ZSTD_versionNumber();
        [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)] static extern IntPtr ZSTD_createCCtx();
        [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)] static extern uint ZSTD_isError(UIntPtr code);
        [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
        static extern unsafe UIntPtr ZSTD_compressCCtx(IntPtr cctx, byte* dst, UIntPtr cap, byte* src, UIntPtr size, int level);
    }
}
