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
- **Installation** (`winiso.py`, `regional.py`, `install/autounattend.xml`) : ISO officiel
  téléchargé chez Microsoft, fichier de réponses complété avec la langue de l'ISO, le format,
  le clavier et le fuseau du système Linux, la clé de l'utilisateur ou une clé générique.
- **Applications** (`catalog.py`) : Office par l'outil de déploiement d'Office, le reste par
  winget, lancés dans Windows par le canal de l'agent (`vasistas exec`).
