using System;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;

namespace Vasistas.Agent
{
    /// <summary>
    /// Image d'une fenêtre : PrintWindow (ou copie d'écran pour les popups) dans une DIB,
    /// recadrée sur la zone visible, puis envoi des tuiles 64x64 qui ont changé.
    /// </summary>
    static class Stats
    {
        public static int Grabs, Frames, Tiles, KeysIn, KeysSent, Scans, Events;
        public static long Bytes, CopyTicks, DiffTicks, ScanTicks;
        public static readonly System.Diagnostics.Stopwatch Watch = System.Diagnostics.Stopwatch.StartNew();

        public static string TakeReport(double seconds)
        {
            double ms(long t) => t * 1000.0 / System.Diagnostics.Stopwatch.Frequency;
            string r = $"stats {seconds:F1}s : {Grabs / seconds:F0} captures/s, {Frames / seconds:F0} images/s, " +
                       $"{Tiles / seconds:F0} tuiles/s, {Bytes / seconds / 1e6:F1} Mo/s, " +
                       $"copie {(Grabs > 0 ? ms(CopyTicks) / Grabs : 0):F1} ms, diff+envoi {(Grabs > 0 ? ms(DiffTicks) / Grabs : 0):F1} ms, " +
                       $"touches reçues {KeysIn}, injectées {KeysSent}, " +
                       $"{Scans / seconds:F0} balayages/s ({(Scans > 0 ? ms(ScanTicks) / Scans : 0):F2} ms), {Events / seconds:F0} événements/s";
            KeysIn = KeysSent = Scans = Events = 0;
            ScanTicks = 0;
            Grabs = Frames = Tiles = 0;
            Bytes = CopyTicks = DiffTicks = 0;
            return r;
        }
    }

    sealed class Capture : IDisposable
    {
        const int Tile = 64;

        IntPtr dc, bitmap, old, bits;
        int dibW, dibH;
        byte[] cur = new byte[0], prev = new byte[0];
        int w, h;
        bool hasPrev;
        readonly byte[] tileBuf = new byte[Tile * Tile * 4];
        readonly MemoryStream deflated = new MemoryStream();

        public void Invalidate() => hasPrev = false;

        void EnsureDib(int width, int height)
        {
            if (dc != IntPtr.Zero && width <= dibW && height <= dibH) return;
            FreeDib();
            dibW = Math.Max(width, 1);
            dibH = Math.Max(height, 1);
            var screen = Native.GetDC(IntPtr.Zero);
            dc = Native.CreateCompatibleDC(screen);
            Native.ReleaseDC(IntPtr.Zero, screen);
            var bmi = new BITMAPINFOHEADER
            {
                biSize = Marshal.SizeOf<BITMAPINFOHEADER>(),
                biWidth = dibW,
                biHeight = -dibH, // de haut en bas
                biPlanes = 1,
                biBitCount = 32,
            };
            bitmap = Native.CreateDIBSection(dc, ref bmi, 0, out bits, IntPtr.Zero, 0);
            old = Native.SelectObject(dc, bitmap);
        }

        void FreeDib()
        {
            if (dc == IntPtr.Zero) return;
            Native.SelectObject(dc, old);
            Native.DeleteObject(bitmap);
            Native.DeleteDC(dc);
            dc = IntPtr.Zero;
        }

        bool disposed;

        // Grab tourne sur le fil de capture, Dispose sur la boucle principale
        public void Dispose()
        {
            lock (this) { disposed = true; FreeDib(); }
        }

        /// <summary>Capture et envoie les tuiles modifiées. Renvoie faux si rien n'a été envoyé.</summary>
        public bool Grab(IntPtr hwnd, uint id, bool fromScreen, Channel ch)
        {
            lock (this) return !disposed && GrabLocked(hwnd, id, fromScreen, ch);
        }

        unsafe bool GrabLocked(IntPtr hwnd, uint id, bool fromScreen, Channel ch)
        {
            long t0 = System.Diagnostics.Stopwatch.GetTimestamp();
            Stats.Grabs++;
            RECT bounds = Native.Bounds(hwnd);
            int cw = bounds.Width, chh = bounds.Height;
            if (cw <= 0 || chh <= 0 || cw > 16384 || chh > 16384) return false;

            int ox, oy, stride;
            if (fromScreen)
            {
                EnsureDib(cw, chh);
                var screen = Native.GetDC(IntPtr.Zero);
                Native.BitBlt(dc, 0, 0, cw, chh, screen, bounds.Left, bounds.Top, Native.SRCCOPY | Native.CAPTUREBLT);
                Native.ReleaseDC(IntPtr.Zero, screen);
                ox = oy = 0;
            }
            else
            {
                Native.GetWindowRect(hwnd, out RECT wr);
                EnsureDib(wr.Width, wr.Height);
                if (!Native.PrintWindow(hwnd, dc, Native.PW_RENDERFULLCONTENT)) return false;
                ox = bounds.Left - wr.Left;
                oy = bounds.Top - wr.Top;
                if (ox < 0 || oy < 0 || ox + cw > wr.Width || oy + chh > wr.Height) { ox = oy = 0; }
            }
            stride = dibW * 4;

            if (cw != w || chh != h)
            {
                w = cw;
                h = chh;
                cur = new byte[w * h * 4];
                prev = new byte[w * h * 4];
                hasPrev = false;
            }

            // recadrage dans cur, alpha forcé à 255
            byte* src = (byte*)bits;
            fixed (byte* dst = cur)
            {
                for (int y = 0; y < h; y++)
                {
                    uint* s = (uint*)(src + (y + oy) * stride + ox * 4);
                    uint* d = (uint*)(dst + y * w * 4);
                    for (int x = 0; x < w; x++) d[x] = s[x] | 0xFF000000u;
                }
            }

            long t1 = System.Diagnostics.Stopwatch.GetTimestamp();
            Stats.CopyTicks += t1 - t0;
            bool sent = false;
            fixed (byte* c = cur, p = prev)
            {
                for (int ty = 0; ty < h; ty += Tile)
                {
                    int th = Math.Min(Tile, h - ty);
                    for (int tx = 0; tx < w; tx += Tile)
                    {
                        int tw = Math.Min(Tile, w - tx);
                        if (hasPrev && Same(c, p, tx, ty, tw, th)) continue;
                        SendTile(c, id, tx, ty, tw, th, ch);
                        sent = true;
                    }
                }
            }
            var tmp = prev; prev = cur; cur = tmp;
            Stats.DiffTicks += System.Diagnostics.Stopwatch.GetTimestamp() - t1;
            if (sent) Stats.Frames++;
            if (sent || !hasPrev)
            {
                hasPrev = true;
                ch.Send(new System.Collections.Generic.Dictionary<string, object>
                    { { "t", "frame" }, { "id", id }, { "w", w }, { "h", h } });
                return true;
            }
            return false;
        }

        unsafe bool Same(byte* a, byte* b, int tx, int ty, int tw, int th)
        {
            int rowStride = w * 4;
            for (int y = ty; y < ty + th; y++)
            {
                ulong* pa = (ulong*)(a + y * rowStride + tx * 4);
                ulong* pb = (ulong*)(b + y * rowStride + tx * 4);
                int n = tw / 2;
                for (int i = 0; i < n; i++) if (pa[i] != pb[i]) return false;
                if ((tw & 1) != 0 && ((uint*)pa)[tw - 1] != ((uint*)pb)[tw - 1]) return false;
            }
            return true;
        }

        unsafe void SendTile(byte* c, uint id, int tx, int ty, int tw, int th, Channel ch)
        {
            int row = tw * 4;
            fixed (byte* t = tileBuf)
            {
                for (int y = 0; y < th; y++)
                    Buffer.MemoryCopy(c + ((ty + y) * w + tx) * 4, t + y * row, row, row);
            }
            deflated.SetLength(0);
            using (var z = new DeflateStream(deflated, CompressionLevel.Fastest, true))
                z.Write(tileBuf, 0, row * th);
            ch.SendTile(id, tx, ty, tw, th, deflated.GetBuffer(), (int)deflated.Length, true);
            Stats.Tiles++;
            Stats.Bytes += deflated.Length;
        }
    }
}
