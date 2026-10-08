"""Page « Clavier » de l'application compagnon : touche Super, Alt+Tab, raccourcis réservés au
bureau (réglages lus par KeyboardMixin, window.py, à chaque activation d'une fenêtre)."""

from .companion_common import Section, card, clear, dim, row  # versions de Gtk et Granite fixées

from gi.repository import Granite, Gtk  # noqa: E402

from . import keymap, vm  # noqa: E402
from .i18n import _  # noqa: E402


def _save(key, value):
    c = vm.load_config()
    c[key] = value
    vm.save_config(c)


def shortcut_label(text):
    """« <Super>Left » -> « Super+Gauche » (libellé de GTK), le texte tel quel sinon."""
    ok, keyval, mods = Gtk.accelerator_parse(text)
    if ok and keyval:
        return Gtk.accelerator_get_label(keyval, mods)
    return text


class KeyboardPage(Section):
    __gtype_name__ = "VasistasKeyboardPage"

    def __init__(self, win):
        super().__init__("preferences-desktop-keyboard", _("Clavier"),
                         _("Répartition des raccourcis clavier entre le bureau et Windows quand une fenêtre "
                           "Windows est active."))
        self.win = win
        sup, alt_tab, self.reserved = keymap.keyboard_settings(vm.load_config())

        self.header(_("Raccourcis du système"))
        sw = Gtk.Switch(active=sup)
        sw.connect("notify::active", lambda s, _p: _save("super_to_windows", s.get_active()))
        self.add(row(_("Touche Super envoyée à Windows"),
                     _("Dans une fenêtre Windows, la touche Super ouvre le menu Démarrer de Windows et ses "
                       "raccourcis (Super+E, Super+V…) sont envoyés à Windows. Désactivé : la touche Super reste "
                       "au bureau Linux."), sw))
        at = Gtk.Switch(active=alt_tab)
        at.connect("notify::active", lambda s, _p: _save("alt_tab_windows", s.get_active()))
        self.add(row(_("Alt+Tab dans Windows"),
                     _("Dans une fenêtre Windows, Alt+Tab passe d'une fenêtre Windows à l'autre. Désactivé : "
                       "Alt+Tab est géré par le bureau et parcourt toutes les fenêtres."), at))
        self.add(dim(_("Le bureau demande votre autorisation la première fois, pour chaque fenêtre "
                       "Windows. Super+Échap rend à tout moment les raccourcis au bureau. S'applique "
                       "à la prochaine fenêtre Windows activée.")))

        self.header(_("Raccourcis réservés au bureau"))
        self.add(dim(_("Jamais envoyés à Windows. Quand Windows reçoit les raccourcis du système, le premier appui "
                       "rend les raccourcis au bureau et le second déclenche le raccourci.")))
        self.list = self.add(card())
        add_box = Gtk.Box(spacing=6)
        self.entry = Gtk.Entry(placeholder_text="<Super>Left", hexpand=True)
        self.entry.set_tooltip_text(_("Notation de GTK : <Control>, <Shift>, <Alt>, <Super>, puis la touche"))
        self.entry.connect("activate", lambda *_: self.on_add())
        add_btn = Gtk.Button(label=_("Ajouter"))
        add_btn.connect("clicked", lambda *_: self.on_add())
        reset = Gtk.Button(label=_("Liste par défaut"))
        reset.connect("clicked", lambda *_: self.set_reserved(list(keymap.DEFAULT_RESERVED)))
        for w in (self.entry, add_btn, reset):
            add_box.append(w)
        self.add(add_box)
        self.fill()

    def fill(self):
        clear(self.list)
        if not self.reserved:
            self.list.append(Gtk.Label(label=_("Aucun raccourci réservé"), margin_top=8, margin_bottom=8))
        for s in self.reserved:
            box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
            box.append(Gtk.Label(label=shortcut_label(s), xalign=0, hexpand=True))
            rm = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text=_("Retirer"))
            rm.add_css_class(Granite.STYLE_CLASS_FLAT)
            rm.connect("clicked", lambda _b, s=s: self.set_reserved([x for x in self.reserved if x != s]))
            box.append(rm)
            self.list.append(Gtk.ListBoxRow(activatable=False, child=box))

    def set_reserved(self, items):
        self.reserved = items
        _save("reserved_shortcuts", items)
        self.fill()

    def on_add(self):
        text = self.entry.get_text().strip()
        if keymap.parse_shortcut(text) is None:
            self.win.notify(_("Raccourci non reconnu. Exemple de format valide : <Super>Left"))
            return
        if text not in self.reserved:
            self.set_reserved(self.reserved + [text])
        self.entry.set_text("")
