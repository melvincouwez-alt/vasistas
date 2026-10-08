using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

namespace Vasistas.Agent
{
    /// <summary>
    /// Port virtio-serial org.vasistas.0 en E/S overlapped natives : un fil lit les trames,
    /// les écritures viennent du fil principal avec délai maximal.
    /// </summary>
    sealed class Channel
    {
        const string PortPath = @"\\.\Global\org.vasistas.0";
        const byte TJson = 1, TTile = 2;
        // JSON de l'hôte ; le plus gros est une image du presse-papiers (24 Mio de PNG, en base64)
        const int MaxHostFrame = 40 << 20;

        public readonly ConcurrentQueue<Dictionary<string, object>> Inbox = new ConcurrentQueue<Dictionary<string, object>>();
        public readonly AutoResetEvent InboxEvent = new AutoResetEvent(false);
        public volatile bool HostReady;
        /// <summary>L'hôte décode les tuiles zstd (champ zstd de son hello) et la DLL est chargée.</summary>
        public volatile bool Zstd;
        public const byte EncDeflate = 1, EncZstd = 2;

        readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
        readonly object writeLock = new object();
        SafeFileHandle handle;
        IntPtr writeOv, readOv;
        IntPtr writeEvent, readEvent;

        public void Start()
        {
            writeOv = Marshal.AllocHGlobal(32);
            readOv = Marshal.AllocHGlobal(32);
            writeEvent = Win32.CreateEvent(IntPtr.Zero, true, false, null);
            readEvent = Win32.CreateEvent(IntPtr.Zero, true, false, null);
            new Thread(ReadLoop) { IsBackground = true, Name = "vasistas-read" }.Start();
            new Thread(WriteLoop) { IsBackground = true, Name = "vasistas-write" }.Start();
        }

        bool Open()
        {
            var h = Win32.CreateFile(PortPath, Win32.GENERIC_READ | Win32.GENERIC_WRITE, 0, IntPtr.Zero,
                Win32.OPEN_EXISTING, Win32.FILE_FLAG_OVERLAPPED, IntPtr.Zero);
            if (h.IsInvalid)
            {
                h.Dispose();
                return false;
            }
            handle = h;
            return true;
        }

        void Close()
        {
            HostReady = false;
            var h = handle;
            handle = null;
            h?.Dispose();
        }

        // -- lecture --

        void ReadLoop()
        {
            while (true)
            {
                if (handle == null && !Open())
                {
                    Thread.Sleep(2000);
                    continue;
                }
                Log.Write("port ouvert");
                // un hôte déjà connecté ne renverra pas hello de lui-même : on se signale
                Announce(new Dictionary<string, object> { { "t", "agent.start" } });
                try
                {
                    var pending = new List<byte>();
                    while (true)
                    {
                        int r = ReadSome(readBuf, 4096); // lectures plus grandes : ERROR_NO_SYSTEM_RESOURCES vu avec vioserial
                        if (r == 0)
                        {
                            // hôte absent : le port renvoie 0 octet. Avant le hello, 250 ms suffisent
                            // (l'hôte le répète chaque seconde, les octets attendent dans le pilote) ;
                            // après, 50 ms comme avant : le pilote ne devrait jamais rendre 0 octet à
                            // un hôte connecté, mais rien ne le garantit pour toutes ses versions
                            Thread.Sleep(HostReady ? 50 : 250);
                            continue;
                        }
                        pending.AddRange(new ArraySegment<byte>(readBuf, 0, r));
                        Parse(pending);
                    }
                }
                catch (Exception e)
                {
                    Log.Write("lecture interrompue : " + e.Message);
                    lock (writeLock) Close();
                    Thread.Sleep(1000);
                }
            }
        }

        readonly byte[] readBuf = new byte[65536];

        /// <summary>
        /// Découpe les trames « VS » + longueur + type. Une trame incohérente (flux repris
        /// au milieu d'une trame après une coupure) fait chercher le marqueur suivant.
        /// </summary>
        void Parse(List<byte> buf)
        {
            while (true)
            {
                int start = 0;
                while (start + 1 < buf.Count && !(buf[start] == (byte)'V' && buf[start + 1] == (byte)'S')) start++;
                if (start > 0) buf.RemoveRange(0, start);
                if (buf.Count < 7) return;
                int len = buf[2] | buf[3] << 8 | buf[4] << 16 | buf[5] << 24;
                byte type = buf[6];
                if (len < 1 || len > MaxHostFrame || type != TJson)
                {
                    buf.RemoveRange(0, 2);
                    continue;
                }
                if (buf.Count < 6 + len) return;
                byte[] body = buf.GetRange(7, len - 1).ToArray();
                buf.RemoveRange(0, 6 + len);
                try
                {
                    if (json.DeserializeObject(System.Text.Encoding.UTF8.GetString(body)) is Dictionary<string, object> msg)
                    {
                        Inbox.Enqueue(msg);
                        InboxEvent.Set();
                    }
                }
                catch (ArgumentException) { } // JSON invalide : trame ignorée
                catch (InvalidOperationException) { }
            }
        }

        unsafe int ReadSome(byte[] buf, int count)
        {
            var h = handle ?? throw new IOException("port fermé");
            PrepareOverlapped(readOv, readEvent);
            fixed (byte* p = buf)
            {
                if (!Win32.ReadFile(h, (IntPtr)p, (uint)count, IntPtr.Zero, readOv))
                {
                    int err = Marshal.GetLastWin32Error();
                    if (err != Win32.ERROR_IO_PENDING) throw new Win32Exception(err);
                }
                if (!Win32.GetOverlappedResult(h, readOv, out uint done, true))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                return (int)done;
            }
        }

        static void PrepareOverlapped(IntPtr ov, IntPtr ev)
        {
            for (int i = 0; i < 32; i += 8) Marshal.WriteInt64(ov, i, 0);
            Win32.ResetEvent(ev);
            Marshal.WriteIntPtr(ov, 24, ev);
        }

        // -- écriture --

        public void Send(Dictionary<string, object> msg) => Write(JsonFrame(msg));

        byte[] JsonFrame(Dictionary<string, object> msg)
        {
            byte[] body = System.Text.Encoding.UTF8.GetBytes(json.Serialize(msg));
            var frame = new byte[7 + body.Length];
            frame[0] = (byte)'V'; frame[1] = (byte)'S';
            BitConverter.GetBytes(body.Length + 1).CopyTo(frame, 2);
            frame[6] = TJson;
            body.CopyTo(frame, 7);
            return frame;
        }

        public void SendTile(uint id, int x, int y, int w, int h, byte[] payload, int payloadLen, byte enc)
        {
            var frame = new byte[7 + 13 + payloadLen];
            frame[0] = (byte)'V'; frame[1] = (byte)'S';
            BitConverter.GetBytes(1 + 13 + payloadLen).CopyTo(frame, 2);
            frame[6] = TTile;
            BitConverter.GetBytes(id).CopyTo(frame, 7);
            BitConverter.GetBytes((ushort)x).CopyTo(frame, 11);
            BitConverter.GetBytes((ushort)y).CopyTo(frame, 13);
            BitConverter.GetBytes((ushort)w).CopyTo(frame, 15);
            BitConverter.GetBytes((ushort)h).CopyTo(frame, 17);
            frame[19] = enc;
            Buffer.BlockCopy(payload, 0, frame, 20, payloadLen);
            Write(frame, droppable: true);
        }

        // -- file d'envoi : la boucle principale ne se bloque jamais sur le port --

        const long MaxQueuedBytes = 32L << 20;
        readonly ConcurrentQueue<byte[]> outbox = new ConcurrentQueue<byte[]>();
        readonly AutoResetEvent outboxEvent = new AutoResetEvent(false);
        long queuedBytes;
        int overflowed;

        /// <summary>Vrai (une seule fois) si des tuiles ont été jetées : il faut renvoyer des images complètes.</summary>
        public bool TakeOverflow() => Interlocked.Exchange(ref overflowed, 0) != 0;

        /// <summary>Vide la file (nouvel hôte : inutile de lui envoyer l'arriéré de l'ancien).</summary>
        public void ClearQueue()
        {
            while (outbox.TryDequeue(out var d)) Interlocked.Add(ref queuedBytes, -d.Length);
        }

        /// <summary>Message envoyé même sans hello de l'hôte (annonce de démarrage de l'agent).</summary>
        public void Announce(Dictionary<string, object> msg)
        {
            var frame = JsonFrame(msg);
            Interlocked.Add(ref queuedBytes, frame.Length);
            outbox.Enqueue(frame);
            outboxEvent.Set();
        }

        void Write(byte[] data, bool droppable = false)
        {
            if (!HostReady) return;
            if (droppable && Interlocked.Read(ref queuedBytes) > MaxQueuedBytes)
            {
                Interlocked.Exchange(ref overflowed, 1);
                return;
            }
            Interlocked.Add(ref queuedBytes, data.Length);
            outbox.Enqueue(data);
            outboxEvent.Set();
        }

        void WriteLoop()
        {
            while (true)
            {
                outboxEvent.WaitOne();
                while (outbox.TryDequeue(out var data))
                {
                    Interlocked.Add(ref queuedBytes, -data.Length);
                    if (!WriteNow(data))
                    {
                        HostReady = false;
                        ClearQueue();
                    }
                }
            }
        }

        unsafe bool WriteNow(byte[] data)
        {
            lock (writeLock)
            {
                var h = handle;
                if (h == null) return false;
                int off = 0;
                fixed (byte* p = data)
                {
                    while (off < data.Length)
                    {
                        PrepareOverlapped(writeOv, writeEvent);
                        if (!Win32.WriteFile(h, (IntPtr)(p + off), (uint)(data.Length - off), IntPtr.Zero, writeOv))
                        {
                            int err = Marshal.GetLastWin32Error();
                            if (err != Win32.ERROR_IO_PENDING)
                            {
                                Log.Write("écriture : erreur " + err);
                                return false;
                            }
                        }
                        // sans délai : QEMU jette les données quand aucun hôte n'est connecté,
                        // l'écriture finit donc toujours par aboutir
                        if (!Win32.GetOverlappedResult(h, writeOv, out uint done, true))
                        {
                            Log.Write("écriture : erreur " + Marshal.GetLastWin32Error());
                            return false;
                        }
                        off += (int)done;
                    }
                }
                return true;
            }
        }
    }

    static class Win32
    {
        public const uint GENERIC_READ = 0x80000000, GENERIC_WRITE = 0x40000000;
        public const uint OPEN_EXISTING = 3, FILE_FLAG_OVERLAPPED = 0x40000000;
        public const int ERROR_IO_PENDING = 997;

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool ReadFile(SafeFileHandle h, IntPtr buf, uint count, IntPtr read, IntPtr ov);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool WriteFile(SafeFileHandle h, IntPtr buf, uint count, IntPtr written, IntPtr ov);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetOverlappedResult(SafeFileHandle h, IntPtr ov, out uint done, bool wait);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        public static extern IntPtr CreateEvent(IntPtr sec, bool manual, bool initial, string name);
        [DllImport("kernel32.dll")] public static extern bool ResetEvent(IntPtr h);
    }

    static class Log
    {
        static readonly string Path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "vasistas-agent.log");
        static readonly object Lock = new object();

        /// <summary>Copie des messages vers l'hôte (journal de l'application GTK).</summary>
        public static Action<string> Sink;

        public static void Write(string msg)
        {
            try { Sink?.Invoke(msg); } catch (Exception) { }
            lock (Lock)
            {
                try { File.AppendAllText(Path, DateTime.Now.ToString("HH:mm:ss.fff ") + msg + Environment.NewLine); }
                catch (IOException) { }
            }
        }
    }
}
