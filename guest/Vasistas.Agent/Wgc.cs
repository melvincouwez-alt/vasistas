using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.WindowsRuntime;
using Windows.Foundation;
using Windows.Graphics;
using Windows.Graphics.Capture;
using Windows.Graphics.DirectX;
using Windows.Graphics.DirectX.Direct3D11;
using Windows.Security.Authorization.AppCapabilityAccess;

namespace Vasistas.Agent
{
    /// <summary>
    /// Image d'une fenêtre par Windows.Graphics.Capture : DWM livre une image seulement quand son
    /// contenu change, et la copie vers le CPU (texture de transfert D3D11) coûte 21 à 27 ms pour
    /// 6 Mpx sur WARP, contre 89 à 122 pour PrintWindow (docs/performances-affichage.md, étape 3).
    /// Sans WGC ou sans capture sans liseré, Capture garde PrintWindow.
    /// </summary>
    sealed class Wgc : IDisposable
    {
        static readonly Guid IidItem = new Guid("79C3F95B-31F7-4EC2-A464-632EF5D30760");
        static readonly Guid IidDxgiDevice = new Guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c");
        static readonly Guid IidTexture2D = new Guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c");
        static readonly Guid IidMultithread = new Guid("9B7E4E00-342C-4106-A19F-4F2704F689F0");
        const DirectXPixelFormat Bgra = DirectXPixelFormat.B8G8R8A8UIntNormalized;

        static readonly object InitLock = new object();
        static bool? available;
        static IntPtr device, context;
        static IDirect3DDevice rtDevice;
        static CreateTexture2DFn createTexture;
        static CopyResourceFn copyResource;
        static MapFn map;
        static UnmapFn unmap;
        static GetDescFn getDesc;
        static CopySubresourceRegionFn copyRegion;

        GraphicsCaptureItem item;
        Direct3D11CaptureFramePool pool;
        GraphicsCaptureSession session;
        SizeInt32 poolSize;
        IntPtr staging;
        int stagingW, stagingH;
        // dernière image livrée, prise dès son arrivée : le pool (2 tampons) n'est jamais plein,
        // DWM ne s'arrête pas et une fenêtre qui redevient recouverte ne ressort pas une image ancienne
        Direct3D11CaptureFrame latest;
        readonly object frameLock = new object();
        // zones changées depuis la dernière lecture, images sautées comprises (Windows 11 24H2) ;
        // pendingFull : au moins une image sans cette liste, tout est à recopier
        readonly List<RectInt32> pending = new List<RectInt32>();
        bool pendingFull = true;
        bool stagingValid; // la texture de transfert tient l'image entière la plus récente lue
        /// <summary>Zones changées de l'image rendue par Map (coordonnées de l'image), ou null : toute l'image.</summary>
        public List<RectInt32> Regions;
        /// <summary>Fenêtre détruite ou capture arrêtée par Windows : à remplacer par PrintWindow.</summary>
        public volatile bool Closed;

        static bool Available
        {
            get
            {
                lock (InitLock)
                {
                    if (available == null)
                    {
                        try { available = Init(); }
                        catch (Exception e) { Log.Write("WGC indisponible : " + e.Message); available = false; }
                    }
                    return available.Value;
                }
            }
        }

        static bool Init()
        {
            if (!GraphicsCaptureSession.IsSupported()) { Log.Write("WGC indisponible : non pris en charge"); return false; }
            // liseré jaune autour des fenêtres capturées : il se verrait dans l'écran lu par l'hôte
            var op = GraphicsCaptureAccess.RequestAccessAsync(GraphicsCaptureAccessKind.Borderless);
            var sw = Stopwatch.StartNew();
            while (op.Status == AsyncStatus.Started && sw.ElapsedMilliseconds < 3000) System.Threading.Thread.Sleep(10);
            if (op.Status != AsyncStatus.Completed || op.GetResults() != AppCapabilityAccessStatus.Allowed)
            {
                Log.Write("WGC écarté : capture sans liseré refusée (" + op.Status + ")");
                return false;
            }

            // D3D11_CREATE_DEVICE_BGRA_SUPPORT ; matériel (WARP derrière le pilote de base) puis WARP
            int hr = D3D11CreateDevice(IntPtr.Zero, 1, IntPtr.Zero, 0x20, IntPtr.Zero, 0, 7, out device, out _, out context);
            if (hr < 0) hr = D3D11CreateDevice(IntPtr.Zero, 5, IntPtr.Zero, 0x20, IntPtr.Zero, 0, 7, out device, out _, out context);
            if (hr < 0) { Log.Write($"WGC écarté : D3D11CreateDevice 0x{hr:x8}"); return false; }
            var iid = IidDxgiDevice;
            Marshal.ThrowExceptionForHR(Marshal.QueryInterface(device, ref iid, out IntPtr dxgi));
            try
            {
                Marshal.ThrowExceptionForHR(CreateDirect3D11DeviceFromDXGIDevice(dxgi, out IntPtr inspectable));
                rtDevice = (IDirect3DDevice)Marshal.GetObjectForIUnknown(inspectable);
                Marshal.Release(inspectable);
            }
            finally { Marshal.Release(dxgi); }
            // méthodes D3D11 par leur rang dans la table virtuelle (ID3D11Device, ID3D11DeviceContext)
            createTexture = Vt<CreateTexture2DFn>(device, 5);
            map = Vt<MapFn>(context, 14);
            unmap = Vt<UnmapFn>(context, 15);
            copyResource = Vt<CopyResourceFn>(context, 47);
            // le pool libre-fil peut toucher au périphérique depuis son fil : contexte protégé
            var mt = IidMultithread;
            if (Marshal.QueryInterface(context, ref mt, out IntPtr multithread) >= 0)
            {
                Vt<SetMultithreadProtectedFn>(multithread, 5)(multithread, 1);
                Marshal.Release(multithread);
            }
            Log.Write("WGC prêt");
            return true;
        }

        /// <summary>Prépare D3D et l'accès sans liseré hors du fil de capture (jusqu'à 3 s).</summary>
        public static void Prewarm() => System.Threading.ThreadPool.QueueUserWorkItem(_ => { var a = Available; });

        /// <summary>Capture WGC de la fenêtre, ou null (PrintWindow reste alors en place).</summary>
        public static Wgc Start(IntPtr hwnd, Action frameArrived)
        {
            if (!Available) return null;
            var w = new Wgc();
            try
            {
                var interop = (IGraphicsCaptureItemInterop)WindowsRuntimeMarshal.GetActivationFactory(typeof(GraphicsCaptureItem));
                var iid = IidItem;
                IntPtr p = interop.CreateForWindow(hwnd, ref iid);
                w.item = (GraphicsCaptureItem)Marshal.GetObjectForIUnknown(p);
                Marshal.Release(p);
                w.poolSize = w.item.Size;
                w.pool = Direct3D11CaptureFramePool.CreateFreeThreaded(rtDevice, Bgra, 2, w.poolSize);
                w.pool.FrameArrived += (s, a) => { if (w.Take()) frameArrived?.Invoke(); };
                w.item.Closed += (s, a) => w.Closed = true;
                w.session = w.pool.CreateCaptureSession(w.item);
                w.session.IsCursorCaptureEnabled = false;
                w.session.IsBorderRequired = false;
                // zones changées jointes à chaque image (24H2) ; image toujours rendue entière
                try { w.session.DirtyRegionMode = GraphicsCaptureDirtyRegionMode.ReportOnly; }
                catch (Exception) { }
                w.session.StartCapture();
                return w;
            }
            catch (Exception e)
            {
                Log.Write("WGC refusé pour " + hwnd.ToInt64().ToString("x") + " : " + e.Message);
                w.Dispose();
                return null;
            }
        }

        /// <summary>Vide le pool (fil du pool), ne garde que la plus récente ; vrai si nouvelle image.</summary>
        bool Take()
        {
            Direct3D11CaptureFrame frame = null, f;
            var regions = new List<RectInt32>();
            bool full = false;
            try
            {
                while ((f = pool?.TryGetNextFrame()) != null)
                {
                    frame?.Dispose();
                    frame = f;
                    // zones de chaque image, même sautée : la texture de transfert doit toutes les recevoir
                    try
                    {
                        var dirty = f.DirtyRegions;
                        if (dirty == null || dirty.Count == 0) full = true;
                        else regions.AddRange(dirty);
                    }
                    catch (Exception) { full = true; } // Windows d'avant 24H2
                }
            }
            catch (Exception) { Closed = true; }
            if (frame == null) return false;
            lock (frameLock)
            {
                latest?.Dispose();
                latest = frame;
                if (full || pending.Count + regions.Count > 64) { pendingFull = true; pending.Clear(); }
                else if (!pendingFull) pending.AddRange(regions);
            }
            return true;
        }

        /// <summary>
        /// Dernière image livrée depuis l'appel précédent, en mémoire (BGRA, haut en bas) ; null si
        /// rien de neuf, ou si elle n'a ni la taille (w1, h1) ni (w2, h2) : width et height disent
        /// alors la sienne. Unmap après lecture.
        /// </summary>
        public unsafe byte* Map(int w1, int h1, int w2, int h2, out int stride, out int width, out int height)
        {
            stride = width = height = 0;
            Regions = null;
            Direct3D11CaptureFrame frame;
            List<RectInt32> regions;
            lock (frameLock)
            {
                frame = latest;
                latest = null;
                regions = pendingFull ? null : new List<RectInt32>(pending);
                pending.Clear();
                pendingFull = false;
            }
            if (frame == null) return null;
            using (frame)
            {
                var size = frame.ContentSize;
                // fenêtre redimensionnée : tampons à la nouvelle taille pour les images suivantes
                if (size.Width != poolSize.Width || size.Height != poolSize.Height)
                {
                    poolSize = size;
                    pool.Recreate(rtDevice, Bgra, 2, size);
                }
                width = size.Width;
                height = size.Height;
                // taille inattendue (redimensionnement en cours) : pas de copie pour rien
                if (!(width == w1 && height == h1) && !(width == w2 && height == h2))
                {
                    stagingValid = false;
                    return null;
                }
                var surface = frame.Surface;
                var iid = IidTexture2D;
                IntPtr texture = ((IDirect3DDxgiInterfaceAccess)surface).GetInterface(ref iid);
                try
                {
                    if (getDesc == null) getDesc = Vt<GetDescFn>(texture, 10);
                    getDesc(texture, out var desc);
                    if (staging == IntPtr.Zero || desc.Width != stagingW || desc.Height != stagingH)
                    {
                        FreeStaging();
                        desc.MipLevels = desc.ArraySize = 1;
                        desc.SampleCount = 1;
                        desc.SampleQuality = 0;
                        desc.Usage = 3;               // D3D11_USAGE_STAGING
                        desc.BindFlags = 0;
                        desc.CPUAccessFlags = 0x20000; // D3D11_CPU_ACCESS_READ
                        desc.MiscFlags = 0;
                        Marshal.ThrowExceptionForHR(createTexture(device, ref desc, IntPtr.Zero, out staging));
                        stagingW = desc.Width;
                        stagingH = desc.Height;
                        stagingValid = false;
                    }
                    if (stagingValid && regions != null)
                    {
                        // seules les zones changées : le reste de la texture de transfert est à jour
                        if (copyRegion == null) copyRegion = Vt<CopySubresourceRegionFn>(context, 46);
                        foreach (var rc in regions)
                        {
                            var box = new Box
                            {
                                Left = Math.Max(0, rc.X), Top = Math.Max(0, rc.Y),
                                Right = Math.Min(stagingW, rc.X + rc.Width), Bottom = Math.Min(stagingH, rc.Y + rc.Height), Back = 1,
                            };
                            if (box.Right > box.Left && box.Bottom > box.Top)
                                copyRegion(context, staging, 0, box.Left, box.Top, 0, texture, 0, ref box);
                        }
                        Regions = regions;
                    }
                    else
                    {
                        copyResource(context, staging, texture);
                        stagingValid = true;
                    }
                }
                finally
                {
                    Marshal.Release(texture);
                    Marshal.ReleaseComObject(surface);
                }
                int hr = map(context, staging, 0, 1, 0, out var m); // D3D11_MAP_READ
                if (hr < 0) throw new COMException("Map", hr);
                stride = (int)m.RowPitch;
                if (width > stagingW || height > stagingH)
                {
                    Unmap();
                    return null;
                }
                return (byte*)m.Data;
            }
        }

        public void Unmap() => unmap(context, staging, 0);

        void FreeStaging()
        {
            if (staging == IntPtr.Zero) return;
            Marshal.Release(staging);
            staging = IntPtr.Zero;
        }

        public void Dispose()
        {
            try { session?.Dispose(); } catch { }
            try { pool?.Dispose(); } catch { }
            lock (frameLock)
            {
                latest?.Dispose();
                latest = null;
            }
            if (item != null) Marshal.ReleaseComObject(item);
            session = null;
            pool = null;
            item = null;
            FreeStaging();
        }

        static T Vt<T>(IntPtr obj, int slot) =>
            Marshal.GetDelegateForFunctionPointer<T>(Marshal.ReadIntPtr(Marshal.ReadIntPtr(obj), slot * IntPtr.Size));

        [StructLayout(LayoutKind.Sequential)]
        struct Texture2DDesc
        {
            public int Width, Height, MipLevels, ArraySize, Format, SampleCount, SampleQuality, Usage, BindFlags, CPUAccessFlags, MiscFlags;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct Box
        {
            public int Left, Top, Front, Right, Bottom, Back;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct Mapped
        {
            public IntPtr Data;
            public uint RowPitch, DepthPitch;
        }

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int CreateTexture2DFn(IntPtr dev, ref Texture2DDesc desc, IntPtr init, out IntPtr tex);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate void GetDescFn(IntPtr tex, out Texture2DDesc desc);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate void CopyResourceFn(IntPtr ctx, IntPtr dst, IntPtr src);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int MapFn(IntPtr ctx, IntPtr res, uint sub, int type, uint flags, out Mapped mapped);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate void UnmapFn(IntPtr ctx, IntPtr res, uint sub);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate void CopySubresourceRegionFn(IntPtr ctx, IntPtr dst, uint dstSub, int x, int y, int z, IntPtr src, uint srcSub, ref Box box);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int SetMultithreadProtectedFn(IntPtr mt, int protect);

        [DllImport("d3d11.dll")]
        static extern int D3D11CreateDevice(IntPtr adapter, int driverType, IntPtr software, uint flags, IntPtr levels,
            uint count, uint sdkVersion, out IntPtr device, out int level, out IntPtr context);

        [DllImport("d3d11.dll")]
        static extern int CreateDirect3D11DeviceFromDXGIDevice(IntPtr dxgiDevice, out IntPtr graphicsDevice);

        [ComImport, Guid("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IGraphicsCaptureItemInterop
        {
            IntPtr CreateForWindow(IntPtr window, ref Guid iid);
            IntPtr CreateForMonitor(IntPtr monitor, ref Guid iid);
        }

        [ComImport, Guid("A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IDirect3DDxgiInterfaceAccess
        {
            IntPtr GetInterface(ref Guid iid);
        }
    }
}
