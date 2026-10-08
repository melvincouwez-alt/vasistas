# Architecture de Vasistas

- **VM** : QEMU lancé directement (sans libvirt), KVM, OVMF, carte `virtio-vga`
  (pilote VirtIO GPU DOD), réseau utilisateur. Installation de Windows 11 entièrement
  automatique (`install/autounattend.xml`, `install/specialize.ps1`).
- **Image** : QEMU tourne avec `-display dbus,p2p=yes`. L'hôte s'inscrit comme écouteur
  de l'écran (`host/vasistas/display.py`) et reçoit, en mémoire partagée, les zones
  modifiées. Une seule texture GTK pour l'écran entier ; chaque fenêtre en affiche sa
  portion. Les fenêtres recouvertes dans Windows sont capturées par l'agent (PrintWindow).
- **Agent** (`guest/Vasistas.Agent`, C# .NET Framework 4.8) : suit les fenêtres, transmet
  souris et clavier (SendInput), le survol (WM_NCHITTEST, forme du pointeur), le
  presse-papiers, les icônes des applications, l'échelle et la résolution de l'écran
  hôte. Canal : port virtio-serial `org.vasistas.0`, protocole dans `PROTOCOL.md`.
- **Hôte** (`host/vasistas`, Python + GTK 4) : une fenêtre GTK par fenêtre Windows,
  identifiant Wayland `<APP_ID>.<appli>` (en minuscules) et lanceur `.desktop`
  créé à la première ouverture (icône Windows au gabarit elementary, emblème Vasistas).
- **Fichiers** : Documents et Téléchargements partagés par virtiofs (lecteurs `Z:` et
  `Y:` dans Windows), d'autres dossiers au choix dans l'application compagnon.
- **Ouverture des fichiers** (`host/vasistas/files.py`, page « Fichiers » du compagnon) :
  chaque extension désignée (.docx, .xlsx, .pbix…) est reliée à une application Windows.
  Son lanceur déclare les types MIME et devient l'application par défaut
  (`~/.config/mimeapps.list`, l'ancienne est gardée et revient quand l'extension n'est
  plus désignée). Au double-clic, le chemin Linux devient un chemin Windows par les
  dossiers partagés (`~/Documents/a.docx` → `Z:\a.docx`). Hors partage, l'hôte propose
  d'ouvrir une copie dans Téléchargements ou de partager le dossier (branché à chaud).
  Types absents de la base MIME (.msg, .pbix, .pbit, .pbids) déclarés dans
  `~/.local/share/mime/packages/<APP_ID>.xml`.
- **Dossiers de Windows** (`host/vasistas/folders.py`, page Windows du compagnon) :
  Documents, Téléchargements, Images, Musique, Vidéos et Bureau de Windows peuvent pointer
  sur les dossiers Linux (SHSetKnownFolderPath), partagés au passage s'il le faut ; le chemin
  d'avant est gardé pour revenir en arrière.
- **Échelle** : Windows n'a qu'un écran, donc une seule échelle, celle de l'écran Linux qui
  porte le plus de surface de fenêtres Windows (recalculée quand une fenêtre change d'écran,
  s'ouvre ou se ferme, jamais sur un simple changement de focus). Résolution de l'invité
  fixe : le plus grand écran Linux sur chaque axe, en pixels physiques. L'hôte n'envoie que des paliers acceptés par Windows (100,
  125… 500 %) : 175 % pour un écran à 1,667. Chaque fenêtre annonce son DPI
  (`GetDpiForWindow`, champ `dpi` de window.new/window.update), car Windows ne la redessine
  à la nouvelle échelle qu'une fois posée. Si ce DPI est le palier de l'écran où se trouve
  la fenêtre, l'image est affichée pixel pour pixel par un nœud de texture simple, calé sur
  les pixels de l'écran (`append_scaled_texture` est rendu à la taille logique puis agrandi,
  même en NEAREST : test `tests/test_render.py`). Sinon (fenêtre sur un autre écran), elle
  est ramenée à sa taille logique, réduite en TRILINEAR ou agrandie en LINEAR.
- **Son** : carte HDA émulée par QEMU sur PipeWire (haut-parleurs et micro), flux nommés
  « Vasistas » dans les réglages de son. Désactivable (config `sound`).
- **Barre de titre du bureau** (expérimental, config `native_titlebar`) : l'agent annonce
  la hauteur de la barre de titre dessinée par Windows (champ `nc`, 0 si l'application
  dessine la sienne) ; l'hôte masque ces lignes et met une barre elementary à la place.
- **Gala** mémorise taille et place de chaque fenêtre par application (WindowStateSaver)
  et les réimpose à l'ouverture : pendant 1,5 s après l'affichage, l'hôte redemande la
  taille de Windows au lieu de la lui transmettre.


- **Lecteurs partagés** : brancher un dossier à chaud fait réinitialiser par Windows les
  autres périphériques virtio-fs. L'agent (`ShareGuard.cs`) vérifie les lecteurs toutes les
  30 s et avant d'ouvrir un fichier, et remonte ceux qui ne répondent plus.
- **Veille** (`sleep.py`) : la VM est suspendue (QMP stop) sans usage ; l'heure de Windows
  est remise à jour au réveil. **Mémoire** (`balloon.py`) : ballon virtio piloté d'après
  l'usage réel de Windows.
- **Indicateur du panneau** (`indicator.py`, `vasistas indicator`) : processus à part, sans
  GTK, StatusNotifierItem + menu com.canonical.dbusmenu écrits avec Gio.DBus. État lu par
  `vm.pid()` et la requête `status` du socket de contrôle (qui ne réveille pas Windows) ;
  commandes de la VM partagées avec le compagnon dans `winctl.py`. Option `indicator` de
  config.json, lanceur de session posé par `vasistas desktop`.
- **Puissance** (`power.py`, branché par `SleepManager`) : profil automatique (`resources_auto`) :
  batterie ou mode Économie de power-profiles-daemon -> « battery », secteur -> profil choisi,
  « performance » tant qu'une application de `heavy_apps` est ouverte. Cœurs et mémoire de QEMU
  au démarrage seulement ; à chaud : plafond du ballon, affinité des fils de QEMU (cœurs les plus
  sobres d'après ACPI CPPC sur batterie), cadence de capture de l'agent (message `capture`).
  Arrêt automatique sans fenêtre ouverte (`auto_shutdown_min`) et démarrage en veille
  (`vasistas boot --sleep`) dans `sleep.py`.
- **Clavier** (`KeyboardMixin`, page « Clavier ») : Super et Alt+Tab envoyés à Windows sur demande,
  par l'inhibition des raccourcis du compositeur (zwp_keyboard_shortcuts_inhibit_manager_v1 via
  gdk_toplevel_inhibit_system_shortcuts). Gala demande l'autorisation une fois par fenêtre ;
  Super+Échap rend les raccourcis au bureau. Raccourcis réservés : `reserved_shortcuts`.
- **Lanceurs** : `launcher_suffix` (« Word (Windows) »), `launcher_emblem`, `hidden_apps`
  (NoDisplay gardé quand les lanceurs sont réécrits) ; `desktop.apply_launcher_options()`.
- **Imprimantes** (`printers.py`, page « Imprimantes ») : chaque file CUPS devient une imprimante IPP
  dans Windows (`http://10.0.2.6:631/printers/<file>`). 10.0.2.6 n'existe que dans le réseau user de
  QEMU : `guestfwd=…-cmd:printproxy.py` relaie chaque connexion vers `/run/cups/cups.sock` en
  réécrivant l'en-tête Host (CUPS refuse un Host autre que localhost sur une connexion locale).
  CUPS n'est pas ouvert au réseau, sa configuration n'est pas touchée.
- **Installation** (`winiso.py`, `regional.py`, `install/autounattend.xml`) : ISO officiel
  de Microsoft (versions d'évaluation téléchargées par les liens publics go.microsoft.com de
  l'Evaluation Center ; pour les autres, page officielle ouverte dans le navigateur, puis
  fichier choisi par l'utilisateur), fichier de réponses complété avec la langue de l'ISO, le format,
  le clavier et le fuseau du système Linux, la clé de l'utilisateur ou une clé générique.
- **Applications** (`catalog.py`) : Office par l'outil de déploiement d'Office, le reste par
  winget, lancés dans Windows par le canal de l'agent (`vasistas exec`).
- **Points de restauration** (`restore.py`, `vasistas restore`) : instantanés internes de
  disk.qcow2 nommés `vas-a-…` (automatiques) ou `vas-m-…` (manuels), motif et nom dans
  restore.json. VM en marche : l'agent vide le cache disque (Write-VolumeCache), la VM est mise
  en pause le temps de `blockdev-snapshot-internal-sync`. Retour à un point VM arrêtée seulement
  (`qemu-img snapshot -a`). Points automatiques avant Windows Update et les installations, les
  plus anciens supprimés au-delà de `restore_keep`. L'instantané de l'allègement n'en fait pas partie.
- **Diagnostic** (`diagnose.py`, `vasistas diagnose [--report]`) : vérifications, réparations
  simples, rapport anonymisé (dossier personnel, utilisateur, machine, mot de passe, fichiers
  des dossiers partagés masqués). **Notifications à action** (`notices.py`) :
  org.freedesktop.Notifications par Gio.DBus, repli notify-send.
- **Écrans et place des fenêtres** (`placement.py`) : écran voulu par application (`screens` dans
  config.json : actif, dernier, ou un connecteur), taille et écran mémorisés par configuration
  d'écrans (`windows.json`), fenêtre jamais plus grande que 90 % de son écran. Sous Wayland
  l'application ne place pas ses fenêtres : Gala centre les nouvelles (`center-new-windows`, avec
  un décalage en cascade si une fenêtre occupe déjà la place) mais réimpose sinon la place de la
  n-ième fenêtre de l'appli (WindowStateSaver), sauf pour une fenêtre non redimensionnable au
  moment où elle apparaît : c'est le cas le temps de l'apparition. Changer d'écran = plein écran
  sur l'écran voulu puis retour (Mutter garde la position relative dans l'espace libre, donc le
  centrage). « Réinitialiser les affichages » (requête `reset_windows`, `vasistas reset-windows`,
  automatique au branchement d'un écran) : `windows.reset` à l'agent, taille ramenée à 80 %,
  fenêtre masquée puis réaffichée (Gala la recentre), puis passée sur son écran.
- **Apparence** (`look.py`) : mode sombre, accent elementary et lissage des polices envoyés à
  l'agent (`theme`, `fonts`), à l'arrivée de l'agent et à chaque changement du bureau, sans
  compter comme un usage (Windows n'est pas réveillé).
- **Notifications et zone de notification de Windows** (`winnotify.py`, `wintray.py`) : l'agent
  lit les bannières et les icônes par UI Automation (COM, UIA3) ; chaque bannière devient une
  notification du bureau (clic = bannière ouverte dans Windows), chaque icône un
  StatusNotifierItem du panneau (clics renvoyés à Windows). Repli sans menu sur
  NotifyIconSettings quand la barre des tâches n'est pas lisible.
- **Image** : sans fenêtre visible sous Linux, rien n'est dessiné (l'image entière est refaite
  au retour) ; sans fenêtre Windows active, 10 images par seconde au plus.
