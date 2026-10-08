using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace Vasistas.Agent
{
    /// <summary>
    /// Presse-papiers partagé : texte, HTML (mise en forme d'Office) et image PNG.
    /// Tourne sur la boucle principale (fil STA, requis par l'API du presse-papiers).
    /// </summary>
    sealed class ClipboardSync
    {
        const int MaxPngBytes = 24 << 20;
        [DllImport("user32.dll")] static extern uint GetClipboardSequenceNumber();

        readonly Action<Dictionary<string, object>> send;
        uint lastSeq;
        uint ownSeq;   // séquence produite par notre propre écriture : pas d'écho vers l'hôte
        long retryAt;

        /// <summary>Prochain essai quand le presse-papiers était verrouillé par une autre application.</summary>
        public long RetryAt => retryAt;

        public ClipboardSync(Action<Dictionary<string, object>> send)
        {
            this.send = send;
            lastSeq = GetClipboardSequenceNumber();
        }

        /// <summary>À chaque tour de la boucle principale (réveillée par WM_CLIPBOARDUPDATE) : envoie
        /// le contenu quand Windows signale une copie.</summary>
        public void Poll(long now)
        {
            uint seq = GetClipboardSequenceNumber();
            if (seq == lastSeq || now < retryAt) return;
            if (seq == ownSeq) { lastSeq = seq; return; }
            try
            {
                var msg = Read();
                lastSeq = seq;
                if (msg != null) send(msg);
            }
            catch (ExternalException)
            {
                retryAt = now + 100; // presse-papiers verrouillé par une autre application
            }
            catch (Exception e)
            {
                lastSeq = seq; // contenu illisible : pas relu deux fois par seconde
                Log.Write("presse-papiers : " + e.Message);
            }
        }

        Dictionary<string, object> Read()
        {
            var data = Clipboard.GetDataObject();
            if (data == null) return null;
            var msg = new Dictionary<string, object> { { "t", "clipboard" } };
            if (data.GetDataPresent(DataFormats.UnicodeText))
                msg["text"] = data.GetData(DataFormats.UnicodeText) as string;
            if (data.GetDataPresent(DataFormats.Html))
            {
                string cf = data.GetData(DataFormats.Html) as string;
                string html = HtmlFromCfHtml(cf);
                if (html != null) msg["html"] = html;
            }
            if (Clipboard.ContainsImage())
            {
                using (var img = Clipboard.GetImage())
                using (var ms = new MemoryStream())
                {
                    if (img != null)
                    {
                        img.Save(ms, ImageFormat.Png);
                        if (ms.Length <= MaxPngBytes) msg["png"] = Convert.ToBase64String(ms.ToArray());
                    }
                }
            }
            return msg.Count > 1 ? msg : null;
        }

        /// <summary>Contenu envoyé par l'hôte (copie faite sous Linux).</summary>
        public void Apply(Dictionary<string, object> m)
        {
            var obj = new DataObject();
            bool any = false;
            if (m.TryGetValue("text", out var t) && t is string text && text.Length > 0)
            {
                obj.SetData(DataFormats.UnicodeText, text);
                any = true;
            }
            if (m.TryGetValue("html", out var h) && h is string html && html.Length > 0)
            {
                obj.SetData(DataFormats.Html, CfHtml(html));
                any = true;
            }
            Image image = null;
            if (m.TryGetValue("png", out var p) && p is string png && png.Length > 0)
            {
                image = Image.FromStream(new MemoryStream(Convert.FromBase64String(png)));
                obj.SetImage(image);
                any = true;
            }
            if (!any) return;
            for (int i = 0; i < 5; i++)
            {
                try
                {
                    Clipboard.SetDataObject(obj, true);
                    ownSeq = lastSeq = GetClipboardSequenceNumber();
                    return;
                }
                catch (ExternalException) { System.Threading.Thread.Sleep(50); }
            }
            Log.Write("presse-papiers : écriture impossible (verrouillé)");
        }

        // Format CF_HTML : en-tête avec positions en octets UTF-8 du fragment

        static string HtmlFromCfHtml(string cf)
        {
            if (string.IsNullOrEmpty(cf)) return null;
            byte[] bytes = Encoding.UTF8.GetBytes(cf);
            int start = Offset(cf, "StartHTML:"), end = Offset(cf, "EndHTML:");
            if (start < 0 || end <= start || end > bytes.Length)
            {
                int i = cf.IndexOf("<html", StringComparison.OrdinalIgnoreCase);
                return i >= 0 ? cf.Substring(i) : null;
            }
            return Encoding.UTF8.GetString(bytes, start, end - start);
        }

        static int Offset(string cf, string key)
        {
            int i = cf.IndexOf(key, StringComparison.Ordinal);
            if (i < 0) return -1;
            i += key.Length;
            int j = i;
            while (j < cf.Length && char.IsDigit(cf[j])) j++;
            return int.TryParse(cf.Substring(i, j - i), out int v) ? v : -1;
        }

        static string CfHtml(string html)
        {
            const string header = "Version:0.9\r\nStartHTML:{0:D10}\r\nEndHTML:{1:D10}\r\nStartFragment:{2:D10}\r\nEndFragment:{3:D10}\r\n";
            string body = html.IndexOf("<html", StringComparison.OrdinalIgnoreCase) >= 0
                ? html : "<html><body><!--StartFragment-->" + html + "<!--EndFragment--></body></html>";
            int headerLen = Encoding.UTF8.GetByteCount(string.Format(header, 0, 0, 0, 0));
            int startHtml = headerLen;
            int endHtml = startHtml + Encoding.UTF8.GetByteCount(body);
            int fs = body.IndexOf("<!--StartFragment-->", StringComparison.Ordinal);
            int fe = body.IndexOf("<!--EndFragment-->", StringComparison.Ordinal);
            int startFrag = fs >= 0 ? startHtml + Encoding.UTF8.GetByteCount(body.Substring(0, fs + 20)) : startHtml;
            int endFrag = fe >= 0 ? startHtml + Encoding.UTF8.GetByteCount(body.Substring(0, fe)) : endHtml;
            return string.Format(header, startHtml, endHtml, startFrag, endFrag) + body;
        }
    }
}
