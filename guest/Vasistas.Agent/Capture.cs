using System;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;

namespace Vasistas.Agent
{
    /// <summary>
    /// Image d'une fenêtre : Windows.Graphics.Capture (Wgc.cs), sinon PrintWindow (ou copie
    /// d'écran pour les popups) dans une DIB, recadrée sur la zone visible, puis envoi des tuiles
    /// 64x64 qui ont changé.
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
        readonly byte[] packed = new byte[Zstd.Bound];
        Wgc wgc;
        bool wgcTried;
        int wgcMisses; // images WGC d'une autre taille que la fenêtre, d'affilée
        /// <summary>DWM a une nouvelle image de la fenêtre (fil quelconque).</summary>
        public Action FrameArrived;

        public void Invalidate() => hasPrev = false;

        /// <summary>
        /// Rend la DIB et les deux images (3 x largeur x hauteur x 4 octets) d'une fenêtre qui
        /// n'est plus capturée ; la prochaine capture les recrée et renvoie une image complète.
        /// </summary>
        public void Release()
        {
            lock (this)
            {
                StopWgc();
                if (w == 0 && dc == IntPtr.Zero) return;
                FreeDib();
                cur = prev = new byte[0];
                w = h = 0;
                hasPrev = false;
            }
        }

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
            if (bitmap == IntPtr.Zero || bits == IntPtr.Zero) FreeDib(); // mémoire refusée : pas de capture
        }

        void StopWgc()
        {
            wgc?.Dispose();
            wgc = null;
            wgcTried = false;
            wgcMisses = 0;
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
            lock (this) { disposed = true; StopWgc(); FreeDib(); }
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

            if (cw != w || chh != h)
            {
                w = cw;
                h = chh;
                cur = new byte[w * h * 4];
                prev = new byte[w * h * 4];
                hasPrev = false;
            }

            int ox = 0, oy = 0, stride = 0;
            byte* src = null;
            bool mapped = false;
            if (!fromScreen)
            {
                if (wgc != null && wgc.Closed) StopWgc();
                if (!wgcTried) { wgcTried = true; wgc = Wgc.Start(hwnd, () => FrameArrived?.Invoke()); }
                if (wgc != null)
                {
                    Native.GetWindowRect(hwnd, out RECT wr);
                    bool inside = bounds.Left >= wr.Left && bounds.Top >= wr.Top && bounds.Right <= wr.Right && bounds.Bottom <= wr.Bottom;
                    int fw = 0;
                    try { src = wgc.Map(cw, chh, inside ? wr.Width : cw, inside ? wr.Height : chh, out stride, out fw, out int fh); }
                    catch (Exception e)
                    {
                        // session cassée (périphérique perdu…) : PrintWindow jusqu'à la prochaine libération
                        Log.Write("WGC arrêté pour " + hwnd.ToInt64().ToString("x") + " : " + e.Message);
                        StopWgc();
                        wgcTried = true;
                        src = null;
                    }
                    if (src != null)
                    {
                        mapped = true;
                        wgcMisses = 0;
                        if (fw != cw) { ox = bounds.Left - wr.Left; oy = bounds.Top - wr.Top; }
                    }
                    else if (fw > 0 && ++wgcMisses >= 20)
                    {
                        // la taille des images ne colle jamais à la fenêtre : WGC n'apporte rien ici
                        Log.Write("WGC abandonné pour " + hwnd.ToInt64().ToString("x") + " : images à une autre taille");
                        StopWgc();
                        wgcTried = true;
                    }
                    // rien de neuf depuis la dernière image : DWM n'a rien recomposé
                    else if (fw == 0 && wgc != null && hasPrev) return false;
                }
            }
            if (mapped) { }  // image WGC en place
            else if (fromScreen)
            {
                EnsureDib(cw, chh);
                if (dc == IntPtr.Zero) return false;
                var screen = Native.GetDC(IntPtr.Zero);
                Native.BitBlt(dc, 0, 0, cw, chh, screen, bounds.Left, bounds.Top, Native.SRCCOPY | Native.CAPTUREBLT);
                Native.ReleaseDC(IntPtr.Zero, screen);
                ox = oy = 0;
            }
            else
            {
                Native.GetWindowRect(hwnd, out RECT wr);
                // la fenêtre peut changer de taille entre Bounds et GetWindowRect : la DIB couvre les deux
                EnsureDib(Math.Max(wr.Width, cw), Math.Max(wr.Height, chh));
                if (dc == IntPtr.Zero) return false;
                if (!Native.PrintWindow(hwnd, dc, Native.PW_RENDERFULLCONTENT)) return false;
                ox = bounds.Left - wr.Left;
                oy = bounds.Top - wr.Top;
                if (ox < 0 || oy < 0 || ox + cw > dibW || oy + chh > dibH) { ox = oy = 0; }
            }
            if (!mapped)
            {
                src = (byte*)bits;
                stride = dibW * 4;
            }
            else if (hasPrev && wgc.Regions != null)
            {
                try { return GrabRegions(src, stride, ox, oy, wgc.Regions, id, ch, t0); }
                finally { wgc.Unmap(); }
            }

            // recadrage dans cur, alpha forcé à 255
            try
            {
                fixed (byte* dst = cur)
                {
                    for (int y = 0; y < h; y++)
                    {
                        uint* s = (uint*)(src + (y + oy) * stride + ox * 4);
                        uint* d = (uint*)(dst + y * w * 4);
                        for (int x = 0; x < w; x++) d[x] = s[x] | 0xFF000000u;
                    }
                }
            }
            finally { if (mapped) wgc.Unmap(); }

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

        bool[] marks = new bool[0];

        /// <summary>
        /// Image WGC dont Windows donne les zones changées (24H2) : seules les tuiles qu'elles
        /// touchent sont comparées, mises à jour dans prev et envoyées. prev reste l'image complète.
        /// </summary>
        unsafe bool GrabRegions(byte* src, int stride, int ox, int oy,
                                System.Collections.Generic.List<Windows.Graphics.RectInt32> regions, uint id, Channel ch, long t0)
        {
            int cols = (w + Tile - 1) / Tile, rows = (h + Tile - 1) / Tile;
            if (marks.Length < cols * rows) marks = new bool[cols * rows];
            else Array.Clear(marks, 0, cols * rows);
            foreach (var rc in regions)
            {
                // coordonnées de l'image -> zone recadrée de la fenêtre
                int x0 = Math.Max(0, rc.X - ox), y0 = Math.Max(0, rc.Y - oy);
                int x1 = Math.Min(w, rc.X + rc.Width - ox), y1 = Math.Min(h, rc.Y + rc.Height - oy);
                for (int ty = y0 / Tile; ty * Tile < y1; ty++)
                    for (int tx = x0 / Tile; tx * Tile < x1; tx++) marks[ty * cols + tx] = true;
            }
            long t1 = System.Diagnostics.Stopwatch.GetTimestamp();
            Stats.CopyTicks += t1 - t0;
            bool sent = false;
            fixed (byte* p = prev)
            {
                for (int ty = 0; ty < rows; ty++)
                    for (int tx = 0; tx < cols; tx++)
                    {
                        if (!marks[ty * cols + tx]) continue;
                        int x = tx * Tile, y = ty * Tile, tw = Math.Min(Tile, w - x), th = Math.Min(Tile, h - y);
                        bool changed = false;
                        for (int yy = y; yy < y + th; yy++)
                        {
                            uint* s = (uint*)(src + (yy + oy) * stride + (x + ox) * 4);
                            uint* d = (uint*)(p + (yy * w + x) * 4);
                            for (int i = 0; i < tw; i++)
                            {
                                uint v = s[i] | 0xFF000000u;
                                if (d[i] != v) { d[i] = v; changed = true; }
                            }
                        }
                        if (!changed) continue;
                        SendTile(p, id, x, y, tw, th, ch);
                        sent = true;
                    }
            }
            Stats.DiffTicks += System.Diagnostics.Stopwatch.GetTimestamp() - t1;
            if (!sent) return false;
            Stats.Frames++;
            ch.Send(new System.Collections.Generic.Dictionary<string, object>
                { { "t", "frame" }, { "id", id }, { "w", w }, { "h", h } });
            return true;
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
            int n = ch.Zstd ? Zstd.Compress(tileBuf, row * th, packed) : -1;
            if (n >= 0)
                ch.SendTile(id, tx, ty, tw, th, packed, n, Channel.EncZstd);
            else
            {
                deflated.SetLength(0);
                using (var z = new DeflateStream(deflated, CompressionLevel.Fastest, true))
                    z.Write(tileBuf, 0, row * th);
                n = (int)deflated.Length;
                ch.SendTile(id, tx, ty, tw, th, deflated.GetBuffer(), n, Channel.EncDeflate);
            }
            Stats.Tiles++;
            Stats.Bytes += n;
        }
    }
}
