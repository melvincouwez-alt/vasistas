using System;
using System.Runtime.InteropServices;
using System.Text;

namespace Vasistas.Agent
{
    [StructLayout(LayoutKind.Sequential)]
    struct RECT
    {
        public int Left, Top, Right, Bottom;
        public int Width => Right - Left;
        public int Height => Bottom - Top;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    struct CURSORINFO
    {
        public int cbSize;
        public int flags;
        public IntPtr hCursor;
        public POINT pt;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct MSG
    {
        public IntPtr hwnd;
        public uint message;
        public IntPtr wParam, lParam;
        public uint time;
        public POINT pt;
        public uint lPrivate;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct BITMAPINFOHEADER
    {
        public int biSize, biWidth, biHeight;
        public short biPlanes, biBitCount;
        public int biCompression, biSizeImage, biXPelsPerMeter, biYPelsPerMeter, biClrUsed, biClrImportant;
    }

    // INPUT en x64 : type (4) + remplissage (4) + union de 32 octets
    [StructLayout(LayoutKind.Explicit, Size = 40)]
    struct INPUT
    {
        [FieldOffset(0)] public int type;
        [FieldOffset(8)] public int mouseDx;
        [FieldOffset(12)] public int mouseDy;
        [FieldOffset(16)] public int mouseData;
        [FieldOffset(20)] public uint mouseFlags;
        [FieldOffset(8)] public ushort keyVk;
        [FieldOffset(10)] public ushort keyScan;
        [FieldOffset(12)] public uint keyFlags;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct ICONINFO
    {
        public bool fIcon;
        public int xHotspot, yHotspot;
        public IntPtr hbmMask, hbmColor;
    }

    delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

    static class Native
    {
        public const int GWL_STYLE = -16, GWL_EXSTYLE = -20;
        public const long WS_POPUP = 0x80000000L, WS_CAPTION = 0x00C00000L, WS_THICKFRAME = 0x00040000L;
        public const long WS_EX_TOOLWINDOW = 0x80, WS_EX_TRANSPARENT = 0x20;
        public const uint GW_OWNER = 4, GA_ROOT = 2, GA_ROOTOWNER = 3;
        public const int DWMWA_EXTENDED_FRAME_BOUNDS = 9, DWMWA_CLOAKED = 14, DWMWA_WINDOW_CORNER_PREFERENCE = 33;
        public const int DWMWCP_DONOTROUND = 1;
        public const int DWMWA_BORDER_COLOR = 34;
        public const int DWMWA_COLOR_NONE = unchecked((int)0xFFFFFFFE);
        public const uint PW_RENDERFULLCONTENT = 2;
        public const int SRCCOPY = 0x00CC0020, CAPTUREBLT = 0x40000000;
        public const uint SWP_NOSIZE = 1, SWP_NOMOVE = 2, SWP_NOZORDER = 4, SWP_NOACTIVATE = 0x10;
        public static readonly IntPtr HWND_TOP = IntPtr.Zero;
        public const int SW_RESTORE = 9, SW_SHOWNOACTIVATE = 4;
        public const uint WM_CLOSE = 0x10, WM_NCHITTEST = 0x84, WM_CHAR = 0x102;
        public const uint SMTO_ABORTIFHUNG = 2;
        public const int INPUT_MOUSE = 0, INPUT_KEYBOARD = 1;
        public const uint MOUSEEVENTF_MOVE = 1, MOUSEEVENTF_LEFTDOWN = 2, MOUSEEVENTF_LEFTUP = 4,
            MOUSEEVENTF_RIGHTDOWN = 8, MOUSEEVENTF_RIGHTUP = 0x10, MOUSEEVENTF_MIDDLEDOWN = 0x20,
            MOUSEEVENTF_MIDDLEUP = 0x40, MOUSEEVENTF_XDOWN = 0x80, MOUSEEVENTF_XUP = 0x100,
            MOUSEEVENTF_WHEEL = 0x800, MOUSEEVENTF_HWHEEL = 0x1000, MOUSEEVENTF_VIRTUALDESK = 0x4000,
            MOUSEEVENTF_ABSOLUTE = 0x8000;
        public const uint KEYEVENTF_EXTENDEDKEY = 1, KEYEVENTF_KEYUP = 2, KEYEVENTF_SCANCODE = 8;
        public const int SM_CXSCREEN = 0, SM_CYSCREEN = 1, SM_XVIRTUALSCREEN = 76, SM_YVIRTUALSCREEN = 77,
            SM_CXVIRTUALSCREEN = 78, SM_CYVIRTUALSCREEN = 79;
        public const uint PM_REMOVE = 1, QS_ALLINPUT = 0x04FF;

        [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc proc, IntPtr lParam);
        [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hwnd, StringBuilder sb, int max);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder sb, int max);
        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] public static extern IntPtr GetWindowLongPtr(IntPtr hwnd, int index);
        [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr hwnd, uint cmd);
        [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int cmd);
        // fenêtre d'un autre fil : sans attendre une application qui ne répond plus
        [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hwnd, int cmd);
        [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);
        [DllImport("user32.dll")] public static extern IntPtr SendMessageTimeout(IntPtr hwnd, uint msg, IntPtr w, IntPtr l, uint flags, uint timeout, out IntPtr result);
        [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT pt);
        [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hwnd, out RECT rect);
        [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hwnd, ref POINT pt);
        [DllImport("user32.dll")] public static extern bool GetCursorInfo(ref CURSORINFO info);
        [DllImport("user32.dll")] public static extern IntPtr LoadCursor(IntPtr hInstance, IntPtr name);
        [DllImport("user32.dll")] public static extern uint SendInput(uint count, INPUT[] inputs, int size);
        [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
        [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool attach);
        [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
        [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hwnd, IntPtr hdc);
        [DllImport("user32.dll")] public static extern uint GetDpiForSystem();
        [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern IntPtr CreateWindowEx(uint exStyle, string cls, string name, uint style, int x, int y, int w, int h,
            IntPtr parent, IntPtr menu, IntPtr instance, IntPtr param);
        [DllImport("user32.dll", SetLastError = true)] public static extern bool AddClipboardFormatListener(IntPtr hwnd);
        [DllImport("user32.dll")] public static extern bool PeekMessage(out MSG msg, IntPtr hwnd, uint min, uint max, uint remove);
        [DllImport("user32.dll")] public static extern bool TranslateMessage(ref MSG msg);
        [DllImport("user32.dll")] public static extern IntPtr DispatchMessage(ref MSG msg);
        [DllImport("user32.dll")] public static extern uint MsgWaitForMultipleObjects(uint count, IntPtr[] handles, bool waitAll, uint ms, uint mask);

        [DllImport("gdi32.dll")] public static extern IntPtr CreateCompatibleDC(IntPtr hdc);
        [DllImport("gdi32.dll")] public static extern bool DeleteDC(IntPtr hdc);
        [DllImport("gdi32.dll")] public static extern IntPtr SelectObject(IntPtr hdc, IntPtr obj);
        [DllImport("gdi32.dll")] public static extern bool DeleteObject(IntPtr obj);
        [DllImport("gdi32.dll")] public static extern IntPtr CreateDIBSection(IntPtr hdc, ref BITMAPINFOHEADER bmi, uint usage, out IntPtr bits, IntPtr section, uint offset);
        [DllImport("gdi32.dll")] public static extern bool BitBlt(IntPtr dst, int x, int y, int w, int h, IntPtr src, int sx, int sy, int rop);

        [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attr, out RECT value, int size);
        [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attr, out int value, int size);
        [DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

        [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
        [DllImport("user32.dll")] public static extern bool GetIconInfo(IntPtr icon, out ICONINFO info);
        [DllImport("user32.dll")] public static extern IntPtr CreateCursor(IntPtr inst, int hotX, int hotY, int w, int h, byte[] andMask, byte[] xorMask);
        [DllImport("user32.dll")] public static extern bool SetSystemCursor(IntPtr cursor, uint id);
        [DllImport("user32.dll")] public static extern bool SystemParametersInfo(uint action, uint param, IntPtr pv, uint winIni);
        public const uint SPI_SETCURSORS = 0x57;
        [DllImport("user32.dll", EntryPoint = "SystemParametersInfoW")] public static extern bool SystemParametersInfo(uint action, uint param, out RECT pv, uint winIni);
        [DllImport("user32.dll", EntryPoint = "SystemParametersInfoW")] public static extern bool SystemParametersInfo(uint action, uint param, out int pv, uint winIni);
        public const uint SPI_GETWORKAREA = 0x30, SPI_GETFONTSMOOTHING = 0x4A, SPI_SETFONTSMOOTHING = 0x4B,
            SPI_GETFONTSMOOTHINGTYPE = 0x200A, SPI_SETFONTSMOOTHINGTYPE = 0x200B;
        public const uint SPIF_UPDATEINIFILE = 1, SPIF_SENDCHANGE = 2;
        public const int FE_FONTSMOOTHINGSTANDARD = 1, FE_FONTSMOOTHINGCLEARTYPE = 2;
        public const uint WM_SETTINGCHANGE = 0x1A;
        public static readonly IntPtr HWND_BROADCAST = new IntPtr(0xFFFF);
        [DllImport("user32.dll", EntryPoint = "SendMessageTimeoutW", CharSet = CharSet.Unicode)]
        public static extern IntPtr SendMessageTimeout(IntPtr hwnd, uint msg, IntPtr w, string l, uint flags, uint timeout, out IntPtr result);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindow(string cls, string title);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindowEx(IntPtr parent, IntPtr after, string cls, string title);

        // Événements d'accessibilité (SetWinEventHook, hors processus) : la liste des fenêtres
        // suit Windows sans sondage.
        public delegate void WinEventProc(IntPtr hook, uint ev, IntPtr hwnd, int idObject, int idChild, uint thread, uint time);
        [DllImport("user32.dll")]
        public static extern IntPtr SetWinEventHook(uint min, uint max, IntPtr module, WinEventProc proc, uint pid, uint tid, uint flags);
        public const uint WINEVENT_OUTOFCONTEXT = 0, WINEVENT_SKIPOWNPROCESS = 2;
        public const uint EVENT_SYSTEM_FOREGROUND = 0x0003, EVENT_SYSTEM_MENUPOPUPSTART = 0x0006, EVENT_SYSTEM_MENUPOPUPEND = 0x0007,
            EVENT_SYSTEM_MOVESIZEEND = 0x000B, EVENT_SYSTEM_SCROLLINGSTART = 0x0012, EVENT_SYSTEM_SCROLLINGEND = 0x0013,
            EVENT_SYSTEM_MINIMIZESTART = 0x0016, EVENT_SYSTEM_MINIMIZEEND = 0x0017,
            EVENT_CONSOLE_CARET = 0x4001, EVENT_CONSOLE_UPDATE_SCROLL = 0x4004, EVENT_OBJECT_VALUECHANGE = 0x800E,
            EVENT_OBJECT_CREATE = 0x8000, EVENT_OBJECT_SHOW = 0x8002, EVENT_OBJECT_REORDER = 0x8004, EVENT_OBJECT_LOCATIONCHANGE = 0x800B,
            EVENT_OBJECT_NAMECHANGE = 0x800C, EVENT_OBJECT_CLOAKED = 0x8017, EVENT_OBJECT_UNCLOAKED = 0x8018;
        public const int OBJID_WINDOW = 0;

        public static long Style(IntPtr h) => GetWindowLongPtr(h, GWL_STYLE).ToInt64();
        public static long ExStyle(IntPtr h) => GetWindowLongPtr(h, GWL_EXSTYLE).ToInt64();

        public static string ClassName(IntPtr h)
        {
            var sb = new StringBuilder(256);
            GetClassName(h, sb, sb.Capacity);
            return sb.ToString();
        }

        public static string Title(IntPtr h)
        {
            var sb = new StringBuilder(512);
            GetWindowText(h, sb, sb.Capacity);
            return sb.ToString();
        }

        public static bool Cloaked(IntPtr h)
        {
            return DwmGetWindowAttribute(h, DWMWA_CLOAKED, out int v, 4) == 0 && v != 0;
        }

        /// <summary>Zone visible de la fenêtre (sans les bordures invisibles de redimensionnement).</summary>
        public static RECT Bounds(IntPtr h)
        {
            if (DwmGetWindowAttribute(h, DWMWA_EXTENDED_FRAME_BOUNDS, out RECT r, Marshal.SizeOf<RECT>()) == 0 && r.Width > 0)
                return r;
            GetWindowRect(h, out r);
            return r;
        }

        /// <summary>
        /// Hauteur de la barre de titre dessinée par Windows (zone non cliente au-dessus de la zone
        /// cliente), en pixels. 0 si l'application dessine elle-même sa barre (Office, WinUI, Edge :
        /// la zone cliente couvre alors toute la fenêtre) ou si elle n'a pas de légende.
        /// </summary>
        public static int NativeCaption(IntPtr h)
        {
            if ((Style(h) & 0x00C00000) != 0x00C00000) return 0; // WS_CAPTION
            if (!GetClientRect(h, out RECT c)) return 0;
            var pt = new POINT { X = 0, Y = 0 };
            if (!ClientToScreen(h, ref pt)) return 0;
            int nc = pt.Y - Bounds(h).Top;
            return nc >= 16 && nc < 200 ? nc : 0;
        }

        /// <summary>Premier plan forcé, en s'attachant au fil de la fenêtre active.</summary>
        public static void ForceForeground(IntPtr h)
        {
            var fg = GetForegroundWindow();
            if (fg == h) return;
            uint me = GetCurrentThreadId();
            uint other = fg == IntPtr.Zero ? 0 : GetWindowThreadProcessId(fg, out _);
            bool attached = other != 0 && other != me && AttachThreadInput(me, other, true);
            SetWindowPos(h, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
            BringWindowToTop(h);
            SetForegroundWindow(h);
            if (attached) AttachThreadInput(me, other, false);
        }
    }
}
