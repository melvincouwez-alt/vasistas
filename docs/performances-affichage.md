# Performances d'affichage sans GPU dédié

Plan d'optimisation du 2026-10-06. Matériel de référence : portable avec iGPU
AMD Radeon 890M seule (sans carte dédiée), elementary OS 9, Gala en session Wayland, GTK 4.22.4,
QEMU 11.1.1 (patch `patches/qemu-11.1.1-pixman-udmabuf.patch`), Windows 11 avec le pilote
d'affichage « Red Hat VirtIO GPU DOD » 100.103.104.30200 en 3440x1800.

Les chiffres viennent du code, de `docs/perf.jsonl`, des lignes « invité : stats » de
`host.log`, des bancs de septembre (dwmrate.ps1, cadence.ps1, `vasistas bench`) et de mesures
faites aujourd'hui sans déranger la VM. Rien n'a été modifié dans le code pour ce document.

## 1. État des lieux

### Deux chemins pour un pixel

Vasistas affiche une fenêtre Windows de deux façons, selon qu'elle est visible ou recouverte
dans l'écran de Windows.

Fenêtre visible dans Windows (le cas courant) : l'application dessine, DWM compose le bureau
en logiciel (WARP : sans pilote WDDM, « Microsoft Basic Display Adapter » rasterise sur le
CPU), puis le pilote DOD reçoit `PresentDisplayOnly` avec les rectangles sales. Pour chacun,
il copie les pixels dans la ressource virtio (RAM de l'invité) et envoie
`TRANSFER_TO_HOST_2D` puis `RESOURCE_FLUSH`. QEMU copie à son tour le rectangle de la
ressource vers son image pixman, qui vit dans un memfd partagé, et appelle le hôte par D-Bus
(`UpdateMap x y w h`). Côté hôte, `display.py` reçoit l'appel dans le fil GTK et le passe à
`app.py` (`_on_damage`), qui accumule les zones jusqu'au prochain battement de l'horloge
d'affichage d'une fenêtre visible. `_flush_damage` construit alors une `GdkDmabufTexture` sur
le dmabuf créé par `/dev/udmabuf` à partir du memfd de QEMU (`display.udmabuf`), avec
`set_update_region` pour ne renvoyer au GPU que les zones modifiées. Chaque `GuestView`
dessine sa portion de cette texture (`do_snapshot`, `append_texture` calé sur les pixels de
l'écran), GSK en GL importe le dmabuf par EGL et l'iGPU l'échantillonne directement en mémoire
système. GTK négocie l'échelle fractionnaire avec Gala (`wp_fractional_scale_manager_v1` et
`wp_viewporter`, tous deux exposés) : la surface est rendue à 1,667, pas à 2 puis réduite.

Fenêtre recouverte dans Windows (deux applications visibles sous Linux, dont une derrière
l'autre dans Windows ; ou une fenêtre hors de l'écran de Windows) : l'agent la capture
lui-même. `Capture.Grab` appelle `PrintWindow(PW_RENDERFULLCONTENT)` dans une DIB, recadre et
force l'alpha dans un tampon `cur`, compare chaque tuile de 64x64 à l'image précédente,
compresse chaque tuile modifiée en deflate (`CompressionLevel.Fastest`) et l'envoie par un
`WriteFile` sur le port virtio-serial. QEMU relaie le flux sur un socket unix. Côté hôte,
`channel.py` lit les trames (`FrameReader`), décompresse (`zlib`), colle les tuiles dans un
`bytearray` par fenêtre, et au message `frame` copie l'image vers une `GdkMemoryTexture`
que la vue affiche.

### Coûts mesurés ou estimés

| Étape | Chemin | Coût | Source |
|---|---|---|---|
| Composition DWM (WARP) | visible | plafond de 28 compositions/s, soit 36 ms par image | dwmrate.ps1, 2026-09-29 |
| Latence touche vers pixel, zone WinUI | visible | 41 ms médiane | `vasistas bench`, 2026-09-28 |
| Latence touche vers pixel, zone GDI | visible | 23 ms (7 à 43) | idem |
| Même latence par le clavier QMP, toutes résolutions | visible | identique | idem |
| Copie du pilote DOD, écran entier | visible | 24,8 Mo par image complète, rectangles sales seulement en pratique | code viogpudo |
| Copie de QEMU vers pixman | visible | 24,8 Mo, 3 à 5 ms par image complète (estimation à 5 à 8 Go/s) | code virtio-gpu |
| Texture hôte sans copie | visible | 0 octet copié, « écran affiché sans copie (dmabuf) » | host.log 2026-10-06 |
| Hôte au repos, Excel ouvert | visible | 0,1 % de CPU, 32 textures en 30 s, 0 gel | perf.jsonl 2026-10-06 |
| Hôte sans fenêtre active | visible | 5,1 réveils/s | mesure /proc 2026-10-06 |
| `PrintWindow` d'une fenêtre recouverte | recouverte | 27 à 75 ms par capture, 267 ms vu une fois sur une grande fenêtre | stats agent, 2026-09-29 au 10-06 |
| Comparaison et envoi des tuiles | recouverte | 1 à 5 ms par capture | idem |
| Cadence des fenêtres recouvertes | recouverte | 2 captures/s (500 ms), jusqu'à 2 s si inchangée ; 250 ms en profil performance | `OccludedMs`, `power.OCCLUDED_MS` |
| Tuile 64x64 deflate | recouverte | 16 Ko bruts, 0,5 à 4 Ko compressés (90 à 209 tuiles/s pour 0,1 à 0,4 Mo/s) | stats agent |
| Image 1920x1088 complète, 510 tuiles | recouverte | deflate 10 ms + copie 1 ms dans l'agent ; décodage hôte 18,9 ms | mesures du 2026-10-05 |
| Écritures sur virtio-serial | recouverte | un `WriteFile` par tuile, jusqu'à 510 par image complète | `Channel.WriteLoop` |
| Agent au repos | les deux | 0,2 % d'un cœur, 22 commutations/s | mesure WMI 2026-10-06 |
| QEMU au repos, Excel ouvert | les deux | 17,3 % d'un cœur hôte dont 16,6 pour les vCPU, 8 429 sorties KVM/s | perf.jsonl 2026-10-06 |
| CPU de l'invité, nouvel Outlook ouvert | les deux | WebView2 85 %, System 32 % (ballon en train de reprendre de la mémoire), dwm 14 %, agent 1,2 % (sur 800) | mesure 2026-10-06 |

Deux essais de septembre bornent aussi le problème : un écran virtuel IddCx à 60 Hz a donné
70 ms de latence au lieu de 41 (rendu WARP de toute façon), et le pilote Yttrium (Venus, build
de débogage) 14 compositions/s avec 8 vCPU à 60 %. Épingler les vCPU sur les cœurs rapides n'a
rien changé (28,5 à 28,7 compositions/s).

### Le facteur limitant

Pour une fenêtre visible, tout ce qui suit DWM coûte quelques millisecondes : les deux copies
de rectangles sales, un appel D-Bus, une importation EGL et un échantillonnage par l'iGPU. La
latence de 41 ms et le plafond de 28 images par seconde viennent de la composition logicielle
dans Windows, et ils ne dépendent ni de la résolution ni du chemin d'entrée. Office, le
défilement et la saisie sont donc bornés par DWM, pas par Vasistas. Power BI ajoute son propre
rendu WebView2 sur le CPU de l'invité (85 % d'un cœur mesurés sur le nouvel Outlook, qui
utilise le même moteur).

Pour une fenêtre recouverte, le facteur limitant est `PrintWindow` : 30 à 75 ms de CPU invité
par capture, d'où la cadence volontairement basse de 2 images par seconde. Ce cas n'est pas
marginal : avec deux écrans, Excel sur l'externe et Outlook sur le portable sont
tous deux visibles sous Linux, mais l'un recouvre l'autre dans l'écran unique de Windows. Le
second se rafraîchit alors toutes les 0,5 à 2 s.

En HiDPI, Windows dessine au palier le plus proche de l'écran de travail (175 % pour 1,667) et
l'hôte affiche pixel pour pixel : aucune remise à l'échelle en régime établi. Le coût est au
changement d'écran de travail : `Display.Apply` change l'échelle globale de Windows
(`DisplayConfigSetDeviceInfo`), toutes les fenêtres se remettent en page et l'hôte affiche une
image mise à l'échelle (floue) jusqu'au `rect` suivant (lignes « dessinée à l'échelle 1.000
sur un écran à 1.667 » du journal).

## 2. Veille

Looking Glass B7 (6 mars 2025) reste la référence en latence : le module KVMFR expose la
mémoire partagée comme dmabuf et le client l'importe sans copie, ce qui est présenté comme
nécessaire pour un hôte sur iGPU. Mais il suppose une carte graphique passée à l'invité, ce que
Vasistas exclut. Son idée transposable, c'est que l'importation dmabuf côté hôte est la bonne
cible, et Vasistas l'a déjà. Sources : https://looking-glass.io/docs/B7/requirements/ ,
https://github.com/gnif/LookingGlass/releases .

WinBoat et WinApps (2025-2026) affichent les applications par FreeRDP et RemoteApp : rendu
dans l'invité, codec bitmap et cache côté client, sans GPU. WinBoat annonce des performances
« correctes pour des applications légères à moyennes » et a ajouté en 2026 un vGPU « Helios »,
un pilote Vulkan invité qui sérialise les appels vers le GPU hôte, autrement dit le même
principe que Venus. Comparaison seulement : RDP ne fait pas mieux que notre chemin dbus pour
une fenêtre visible, et ajoute un codec. Sources : https://github.com/TibixDev/winboat ,
https://www.kstrlworks.com/blog/winboat-helios-vgpu-explained/ .

Côté pilote invité accéléré, Collabora (15 janvier 2025) fait le point sur virglrenderer,
Venus et gfxstream ; Venus couvre Vulkan 1.3 et Mesa 26.0 (2026) lui ajoute les mesh shaders.
Pour Windows, Yttrium (fork de virtio-win) apporte un pilote WDDM D3D9 à 11, Vulkan et OpenGL
par Venus, seul moyen de donner un vrai GPU à DWM sans passthrough. Notre essai du 29 septembre
avec la seule build publiée (débogage) a donné 14 compositions/s. Sources :
https://www.collabora.com/news-and-blog/blog/2025/01/15/the-state-of-gfx-virtualization-using-virglrenderer/ ,
https://www.phoronix.com/news/Venus-Vulkan-Mesh-Shader ,
https://github.com/arehnman/kvm-guest-drivers-windows .

Côté QEMU, la documentation de l'affichage D-Bus dit que `ScanoutDMABUF` est préféré dès que
possible, et celle de virtio-gpu que les ressources blob (`blob=on`, `hostmem`) évitent la
copie entre l'invité et l'hôte. Une série de septembre 2026 (24 correctifs, CVE-2026-66020)
remet d'équerre la propriété des dmabuf et pose `share_handle` sur les blobs udmabuf : la
version suivante de QEMU exportera donc proprement un dmabuf par ressource blob, sans notre
patch des sceaux memfd. Le pilote DOD de virtio-win ne sait pas encore créer de blob (rien dans
`viogpudo.cpp` au 2026-10-06 ; la demande #1111 porte sur les ressources inter-adaptateurs).
Sources : https://www.qemu.org/docs/master/interop/dbus-display.html ,
https://www.qemu.org/docs/master/system/devices/virtio/virtio-gpu.html ,
https://ratatoskr.run/qemu-devel/2026/09/17544434/t ,
https://github.com/virtio-win/kvm-guest-drivers-windows/blob/master/viogpu/viogpudo/viogpudo.cpp .

Pour capturer une fenêtre recouverte, Windows.Graphics.Capture compose la fenêtre à part
(contenu DWM compris : boutons, coins, matériaux), insensible à ce qui la recouvre, et rend
des textures D3D11 ; DXGI Desktop Duplication ne capture que l'écran entier ; `PrintWindow`
manque ce que DWM dessine, ce que l'agent contourne en coupant lui-même les coins arrondis
(`DWMWCP_DONOTROUND`). Un projet de
2026 (win32ui #111) documente cette capture et signale que WGC demande un périphérique D3D11,
WARP accepté. `DwmGetDxSharedSurface` donne la surface partagée d'une fenêtre, mais elle n'est
pas documentée. Sources : https://github.com/va1erian/win32ui/issues/111 ,
https://sageinfinity.github.io/docs/FAQ/dxgiwgc ,
https://learn.microsoft.com/en-us/windows/win32/dwm/dwmdxgetwindowsharedsurface .

GTK a pris l'échelle fractionnaire Wayland en 4.11.1 (5 avril 2023), le rendu Vulkan par défaut
en 4.16 (2024, Vasistas force GL), et 4.24 (2026) retravaille l'horloge d'images pour
présenter à l'heure. Nous sommes en 4.22.4 : `GdkDmabufTextureBuilder` et
`GdkFrameTimings` (heure de présentation prédite et réelle) sont là, l'horloge retravaillée
pas encore. Gala expose `zwp_linux_dmabuf_v1` version 5, `wp_presentation` 2,
`wp_fractional_scale_manager_v1`, `wp_viewporter`, `wp_fifo_manager_v1`,
`wp_commit_timing_manager_v1` et `wp_linux_drm_syncobj_manager_v1` (relevé aujourd'hui).
Sources : https://blog.gtk.org/2023/04/05/gtk-4-11-1/ ,
https://www.phoronix.com/news/GTK-4.24-Released , https://docs.gtk.org/gdk4/class.FrameClock.html .

Sur l'importation d'un udmabuf par le GPU, un rapport NVIDIA de 2026 montre que le GPU peut
lire des données périmées si la mémoire système est en cache write-back et le pilote ne
purge pas ; sur amdgpu avec Mesa, l'importation linéaire fonctionne et c'est ce que nous
voyons. À garder en tête si la NVIDIA revenait un jour dans la boucle. Source :
https://forums.developer.nvidia.com/t/imported-cpu-cached-dma-buf-mapped-non-coherent-gpu-reads-stale-data-egl-dma-buf-import-615-71-09/384944 .

Compression : le banc lzbench de 2024 (ATLAS, CERN) donne zlib niveau 1 à 67,7 Mo/s en
compression et 234 Mo/s en décompression, zstd niveau 1 à 457 et 670 Mo/s à ratio égal, LZ4 au
delà de 1 Go/s en compression et 3,5 Go/s en décompression pour un ratio plus faible. Nos
tuiles sont surtout des aplats et du texte, où LZ4 compresse déjà bien. Sources :
https://hepsoftwarefoundation.org/gsoc/blogs/2024/blog_ATLCompression_IshanDarji.html ,
https://morotti.github.io/lzbench-web/ .

Encodage vidéo : Sunshine encode par AMF sous Windows et VA-API sous Linux avec moins de 10 ms
ajoutées sur un GPU AMD, et SPICE sait diffuser l'écran par GStreamer, avec des plaintes de
latence en 2024. Dans notre cas l'invité n'a pas de GPU, l'encodeur ne pourrait tourner que
sur l'hôte, après la capture, et un codec vidéo dégrade le texte : ces pistes servent à
diffuser vers une autre machine, pas à afficher une fenêtre locale. Sources :
https://docs.lizardbyte.dev/projects/sunshine/v0.23.0/about/advanced_usage.html ,
https://www.mail-archive.com/spice-devel@lists.freedesktop.org/msg52848.html .

## 3. Pistes

Classement par gain attendu rapporté à l'effort et au risque. Les gains sont des estimations
sauf mention contraire ; « mesurer avant » dit ce qui valide ou invalide la piste.

| Piste | Gain attendu | Effort | Risque |
|---|---|---|---|
| A. Écran Windows aussi large que tous les écrans hôte, fenêtres jamais recouvertes | seconde fenêtre visible : de 0,5 à 2 s à 36 ms ; 6 à 15 % d'un cœur invité en moins | 2 jours | moyen (cadence DWM à mesurer) |
| B. Capture des fenêtres recouvertes sur événement | rafraîchissement sous 100 ms après un changement au lieu de 2 s, sans coût au repos | 0,5 jour | faible |
| C. Windows.Graphics.Capture à la place de PrintWindow | 30 à 75 ms ramenés à 3 à 10 ms par capture, contenu DWM complet | 3 jours | moyen (WinRT depuis .NET Framework, WARP) |
| D. LZ4 et une écriture par image sur le canal | agent 10 ms, hôte 19 ms ramenés à 3 et 4 ms par image complète ; 510 sorties VM ramenées à 10 | 1 jour | faible |
| E. Rendu Vulkan de GTK sur radv | synchronisation explicite, quelques dixièmes de ms par image | 0,1 jour | faible, à surveiller |
| F. Yttrium avec une build de production | DWM à 60 Hz, latence vers 25 ms, Office et WebView2 accélérés | 5 jours plus l'attente d'une build | élevé |
| G. Blob virtio-gpu et dmabuf de bout en bout | 3 à 5 ms de QEMU en moins par image complète | pilote DOD à écrire | hors de portée |

### A. Un écran Windows qui contient tous les écrans de l'hôte

Mécanisme : aujourd'hui `vm.SCREEN` et `app._apply_resolution` prennent le plus grand écran
hôte (3440x1800). Si l'écran de Windows fait la somme des largeurs (3440 + 2880 = 6320 sur
1800 avec les deux écrans actuels), le hôte peut poser chaque fenêtre (`window.place`, déjà
dans le protocole) dans la bande qui correspond à son écran Linux, et plus rien ne se recouvre
dans Windows. Le chemin `PrintWindow` ne servirait plus qu'aux fenêtres hors écran ou cachées
par un menu. Fichiers : `vm.py` (`SCREEN`), `app.py` (`_apply_resolution`,
`placement_prepare`, `_on_surface_changed` pour reposer la fenêtre quand elle change
d'écran), `Agent.cs` (`Fit`, `EnsureOnScreen`, `clampNew` : bornes par bande et non par
écran), `Display.cs` (`SetResolution` : mode personnalisé 6320x1800 ; la ressource de 45,5 Mo
tient dans la limite `max_hostmem` de virtio-vga, 256 Mo par défaut).

Mesurer avant : cadence DWM (`dwmrate.ps1`) en 3440x1800 puis en 6320x1800. Si elle reste au
dessus de 25 compositions/s, la piste tient ; si elle tombe vers 15, WARP paie la surface et
la piste est abandonnée. Risque : le DOD peut refuser le mode (il a accepté 3440x1800, qui
n'est pas standard non plus) ; les applications qui se centrent sur l'écran de Windows
apparaîtraient au milieu de la bande double, ce que `placement_prepare` corrige déjà.

### B. Capture sur événement pour les fenêtres encore recouvertes

Mécanisme : l'agent reçoit déjà `EVENT_OBJECT_LOCATIONCHANGE`, `NAMECHANGE` et `REORDER`
(`OnWinEvent`) et demande un balayage. Pour une fenêtre recouverte, poser `NextCapture = 0`
et `captureWake.Set()` sur ces événements, avec un garde-fou de 100 ms entre deux captures,
donne un rafraîchissement immédiat au changement, sans toucher à la cadence de repos
(500 ms doublée jusqu'à 2 s). Fichiers : `Agent.cs` (`OnWinEvent`, `CaptureLoop`). Mesurer :
délai entre une frappe dans la fenêtre recouverte et la tuile reçue (à ajouter au banc
`bench`, qui ne mesure que la fenêtre active). Risque faible : les événements d'une fenêtre
très active déclencheraient au plus 10 captures/s, soit 300 à 750 ms de CPU invité par
seconde dans le pire cas ; le garde-fou peut monter à 250 ms sur batterie.

### C. Windows.Graphics.Capture

Mécanisme : `GraphicsCaptureItem.CreateForWindow`, `Direct3D11CaptureFramePool` sur un
périphérique WARP, `FrameArrived` avec les rectangles sales fournis par DWM, lecture par
`Map` d'une texture staging. Le contenu comprend ce que DWM dessine (coins, boutons, Mica), ce
qui rend inutile `DWMWCP_DONOTROUND`. Depuis .NET Framework 4.8 il faut les interop WinRT
(`IGraphicsCaptureItemInterop`, `IDirect3DDxgiInterfaceAccess`), soit par références au
`Windows.winmd` du SDK, soit par un petit assistant C++ ; le projet est en `net48` avec
`app.manifest`, à compléter. Mesurer avant : WGC sur Microsoft Basic Render Driver dans
cette VM (test de 50 lignes), coût de `Map` pour 3440x1410 (estimation 3 à 5 ms), et si les
rectangles sales arrivent. Risque : WGC refuse les fenêtres de certains processus protégés,
dessine une bordure jaune sur certaines versions (retirable avec `IsBorderRequired` sur les
versions récentes), et chaque fenêtre capturée tient un pool de textures.

### D. Canal : LZ4 et regroupement des écritures

Mécanisme : remplacer `DeflateStream` par LZ4 (`K4os.Compression.LZ4`, managé, pour l'agent ;
module `lz4` de Python, paquet `python3-lz4`, pour l'hôte), avec `enc = 2` dans
`PROTOCOL.md` et repli deflate pour un agent ancien. Regrouper les tuiles d'une capture dans
un seul tampon, écrit par `WriteFile` en morceaux de 64 Ko au lieu d'un par tuile. Fichiers :
`Capture.cs` (`SendTile`), `Channel.cs` (`SendTile`, `WriteLoop`), `protocol.py`
(`unpack_tile`, `ENC_LZ4`), `channel.py`. Mesurer : `bench.py` (tuiles/s, Mo/s, décodage
hôte) avant et après. Gain réel : cette voie ne sert qu'aux fenêtres recouvertes, et
`PrintWindow` reste 5 fois plus cher que la compression. À faire après A et B, ou si C arrive
et que la capture tombe sous 10 ms, moment où la compression deviendrait le poste principal.

### E. Rendu Vulkan de GTK

Mécanisme : `GSK_RENDERER=vulkan` avec `VK_DRIVER_FILES` déjà pointé sur radv dans
`__main__.py`. GTK 4.22 importe les dmabuf en Vulkan et sait utiliser la synchronisation
explicite (`wp_linux_drm_syncobj_manager_v1`, exposé par Gala), ce qui évite une attente
implicite par image. L'interdiction de septembre venait de la NVIDIA prise par Vulkan ; avec
l'ICD forcé ce n'est plus le cas. Mesurer : compteur de gels (`stats` du socket de contrôle),
`GdkFrameTimings` d'une fenêtre, CPU hôte pendant un défilement. Gain faible, essai gratuit.

### F. Yttrium

À ne tenter qu'avec une build de production du pilote, sur une couche qcow2 jetable comme en
septembre, avec `-display dbus,gl=on` et un écouteur `ScanoutDMABUF` / `UpdateDMABUF` dans
`display.py` (les méthodes existent déjà dans `LISTENER_XML`, sans corps). C'est la seule
piste qui déplace le plafond de 28 images par seconde, et aussi la seule qui peut tout
casser : Secure Boot coupé, pilote signé en test, DWM figé si l'affichage ne consomme pas les
images (constaté avec egl-headless). Critère d'arrêt : moins de 50 compositions/s au premier
essai, on ferme.

### G. Blob et zéro copie de bout en bout

Sans blob dans le pilote DOD, QEMU copie forcément la ressource vers pixman. Le gain (3 à 5 ms
par image complète dans la boucle principale de QEMU, qui sert aussi le canal série) ne
justifie pas d'écrire ou de porter un pilote. À revoir si virtio-win ajoute les blobs au DOD ;
notre patch QEMU disparaîtrait en même temps.

### Ce qui est déjà en place ou n'en vaut pas la peine

Le rafraîchissement à l'horloge d'affichage, le regroupement des zones, l'absence de dessin
sans fenêtre visible, l'arrêt de la capture d'une fenêtre réduite ou cachée, la réduction des
réveils (hôte 5/s, agent 22 commutations/s) et l'échelle fractionnaire sans rendu 2x sont déjà
faits ; les retravailler ne rend rien de mesurable.

Un codec vidéo (Sunshine, SPICE, VA-API sur l'iGPU) ajoute au moins une image de latence et
abîme le texte, pour un affichage local qui n'a pas de bande passante à économiser. Looking
Glass demande un GPU dans l'invité. Un écran virtuel IddCx à 60 Hz a été mesuré à 70 ms de
latence. L'épinglage des vCPU n'a rien donné. Des tuiles plus grandes que 64x64 enverraient
plus de pixels inchangés pour gagner moins d'une milliseconde par image. QOI et les formats
DXT/BC n'apportent rien ici : QOI compresse moins vite que LZ4 sur des aplats, et DXT est
avec perte. Changer `_flush_damage` pour garder une seule texture n'est pas possible, les
textures GTK sont immuables, et l'importation EGL coûte quelques dixièmes de milliseconde.

## 4. Recommandation

Quatre étapes, chacune testable seule avec `vasistas bench` complété d'une mesure de la
seconde fenêtre. Les critères sont à comparer à la référence prise à l'étape 1.

1. Référence et banc élargi (un jour). Ajouter au banc une mesure « fenêtre recouverte » :
   deux fenêtres Bloc-notes, frappe dans celle du dessous, délai jusqu'à la tuile reçue ; et
   la cadence DWM lue par `DwmFlush` depuis l'agent, remontée dans `stats`. Enregistrer dans
   `perf.jsonl` : latence active (attendu 41 ms), latence recouverte (attendu 500 à 2 000 ms),
   compositions/s (attendu 28), captures/s et « copie » de l'agent. Mesurer aussi la cadence
   DWM en 6320x1800 pour décider de l'étape 2. Critère : les quatre nombres sont dans le
   fichier, reproductibles à 10 % près sur trois passes.

2. Écran Windows sans recouvrement (piste A), si la cadence DWM en 6320x1800 reste au dessus
   de 25 compositions/s. Critère : avec Excel sur l'écran externe et Outlook sur le portable,
   `vasistas windows` n'affiche plus « [recouverte] », la latence de la seconde fenêtre est
   égale à celle de la première (au plus 45 ms), et les stats de l'agent passent à
   0 capture/s. Si DWM s'effondre, passer à l'étape 3 directement.

3. Fenêtres recouvertes restantes (pistes B puis C). B d'abord, une demi-journée : critère,
   latence recouverte sous 150 ms après une frappe, cadence de repos inchangée (2 captures/s
   au plus, 0 après 2 s sans changement). C ensuite, si WGC fonctionne sur WARP dans la VM :
   critère, « copie » de l'agent sous 10 ms par capture et coins arrondis rendus.

4. Canal (piste D), seulement si l'étape 3 a fait tomber la capture sous 10 ms ou si
   `bench.py` montre plus de 5 % de CPU hôte pendant une sélection à la souris. Critère :
   décodage hôte sous 5 ms par image complète, « diff+envoi » sous 3 ms, bande passante et
   images/s de `bench.py` inchangées ou meilleures.

La piste E se glisse entre deux étapes, un soir, puisqu'elle ne coûte qu'une variable
d'environnement et une mesure. La piste F attend une build de production d'Yttrium ; sans
elle, le plafond de 28 images par seconde et les 41 ms restent ceux de Windows en logiciel, et
aucune optimisation côté hôte ne les déplacera.

## 5. Résultats (exécution du plan, 2026-10-06)

Mesures faites sur une VM de travail en cours d'utilisation, donc sans clavier ni prise de
focus : seules celles qui ne touchent pas au bureau Linux sont là pour l'instant. Les lignes
correspondantes sont dans `docs/perf.jsonl`.

Étape 1, banc élargi. `vasistas bench` sait maintenant mesurer la cadence de composition de
DWM (`--dwm S`, `DwmFlush` pendant qu'une fenêtre bouge) et la latence d'une fenêtre recouverte
(`--occluded N` : console classique posée derrière le Bloc-notes, caractère posté par l'agent
sans premier plan, message `bench.post`, image reçue par les tuiles). Une requête de contrôle
`resolution` impose une résolution d'essai à Windows jusqu'à sa levée.

| Mesure | Valeur | Conditions |
|---|---|---|
| Compositions DWM par seconde, 3440x1800 | 57,4 à 58,5 | fenêtre 900x600 en mouvement, 4 passes |
| Compositions DWM par seconde, 5120x2160 | 43,8 à 46,9 | même fenêtre, résolution d'essai (11,1 Mpx, proche de 6320x1800) |
| Compositions DWM par seconde, 3440x1800 après retour | 46,0 à 47,4 | Power BI en cours d'utilisation à ce moment |
| PrintWindow, Power BI recouverte 3440x1410 | 89 à 122 ms par capture | stats de l'agent, 1 capture/s |
| Windows.Graphics.Capture sur WARP, même fenêtre | 20 images en 3,1 s, première après 104 ms, copie 32 ms (SoftwareBitmap) | fenêtre en cours d'utilisation |
| Windows.Graphics.Capture, copie D3D11 staging et Map, 3440x1800 | 21 à 27 ms par image | bureau, 3 passes |

La cadence de DWM n'est plus à 28 compositions/s comme en septembre mais à 58 : l'allègement
de Windows ou le changement de pilote entre temps y est pour quelque chose, la méthode est la
même (`dwmrate.ps1`). À 11 Mpx elle tombe à 44 à 47, au dessus du seuil de 25 fixé pour
l'étape 2 : un écran de Windows aussi large que les deux écrans de l'hôte reste possible.

Étape 3, capture sur événement : faite et déployée. Les événements d'accessibilité du contenu
d'une fenêtre recouverte (curseur texte, défilement, ascenseurs, console) avancent sa capture
à 50 ms au plus après la précédente (100 ms en équilibré, 200 ms sur batterie) ; au repos rien
ne change. La fenêtre recouverte passe de 205 ms (125 à 217) à 31 ms (15 à 56) de latence,
sous le critère de 150 ms, et la fenêtre active ne bouge pas (38 à 40 ms dans toutes les
passes GL). Windows.Graphics.Capture fonctionne dans cette VM sur WARP, capture une fenêtre
recouverte et ne livre d'image que quand son contenu change, mais la copie vers le CPU coûte
21 à 27 ms pour 6,2 Mpx par le chemin D3D11 direct et 32 ms par SoftwareBitmap : au dessus du
critère de 10 ms, même si c'est quatre fois moins que `PrintWindow` sur la même fenêtre (89 à
122 ms). Pas intégré ; c'est la suite naturelle pour les grandes fenêtres recouvertes, environ
une journée (copie par rectangles sales, repli PrintWindow, pool de textures par fenêtre).

Étape 2, écran de Windows aussi large que les écrans de l'hôte : non faite, par choix. DWM
tient 44 à 47 compositions/s à 11 Mpx, au dessus du seuil de 25, mais c'est 20 % de moins que
les 58 d'aujourd'hui pour toutes les fenêtres, visibles comprises, soit 3 à 5 ms de latence en
plus sur la fenêtre active : le critère « aucune régression de la première » ne tiendrait pas.
Et depuis l'étape 3 la fenêtre recouverte est à 31 ms, l'écart qu'il s'agissait de combler a
disparu ; reste le coût CPU de `PrintWindow` dans l'invité, que l'étape WGC traiterait mieux.
Le mode 6320x1800 demanderait de plus un redémarrage de la VM (`xres` de QEMU, le pilote DOD
n'offre que les modes de sa liste et le préféré de l'EDID).

Étape 4 (compression) : conditionnée à une capture sous 10 ms, non atteinte, donc non faite.
Si elle revient à l'ordre du jour, zstd est disponible sans paquet : `compression.zstd` dans la
bibliothèque standard de Python 3.14 côté hôte, ZstdSharp (managé) côté agent.

Faite le 2026-10-06, sans attendre la condition. Mesure sur 2 948 tuiles
64x64 de vraies captures (Python, bibliothèques natives) : deflate 1 donne un taux de 10,9 à
157 Mo/s (décompression 369 Mo/s), zstd 1 un taux de 11,4 à 389 Mo/s (617 Mo/s). ZstdSharp
écarté : managé, sans SIMD sous .NET Framework 4.8, il ne battrait pas DeflateStream (zlib
natif) et ajouterait quatre DLL. L'agent embarque la `libzstd.dll` officielle 1.5.7 (win64,
gzip dans l'exe, posée dans `%LOCALAPPDATA%\Vasistas\zstd-1.5.7`), niveau 1, un contexte par
fil. L'hôte annonce `zstd: true` dans son hello ; sinon, ou DLL refusée, deflate comme avant.
Journal de l'agent : « zstd … prêt » ou « zstd écarté, deflate : … ». Pas encore vu en VM.

Étape 5, rendu Vulkan de GTK (radv, journal « Using renderer GskVulkanRenderer ») : deux passes
sur trois donnent 24 à 26 ms sur la fenêtre active au lieu de 38 à 40, la troisième 40 ms, et
la fenêtre recouverte ne change pas. Le gain, quand il est là, vaut une image de DWM (17 ms),
ce qui ressemble à un effet de phase entre la touche et la composition plutôt qu'à un coût de
rendu ; trop instable pour changer le réglage par défaut. GL reste en place ; à revalider sur
une trentaine de passes avant de conclure.

Constat fait en passant : un changement de résolution fait reprendre à Windows l'échelle
mémorisée pour ce mode (125 % après 5120x2160, 200 % en septembre), et l'hôte ne renvoie
l'échelle qu'à son propre changement. Corrigé dans l'agent (`lastScalePercent` réappliqué après
`SetResolution`) ; l'échelle de la VM d'essai a été remise à 100 % entre-temps.

## 6. Mise en service (2026-10-06, sans phase d'essais)

Windows.Graphics.Capture intégré dans l'agent (`Wgc.cs`) pour toutes les captures qui
passaient par `PrintWindow` : une session par fenêtre, ouverte à sa première capture et fermée
avec ses tampons (`Release`). Sans nouvelle image de DWM, la capture ne coûte plus rien ; chaque
nouvelle image avance la capture suivante comme un événement d'accessibilité (`CaptureSoon`).
Replis sur `PrintWindow` : WGC absent, capture sans liseré refusée (le liseré jaune se verrait
dans l'écran lu par l'hôte), session refusée pour une fenêtre, image d'une autre taille que la
fenêtre (redimensionnement en cours), première image pas encore livrée. Journal de l'agent :
« WGC prêt », « WGC écarté : … », « WGC refusé pour … ».

GTK passe en Vulkan par défaut (radv, `VK_DRIVER_FILES` déjà posé) ; `GSK_RENDERER=gl` dans
l'environnement revient au rendu GL. Attention : le Terminal d'elementary exporte
`GSK_RENDERER=gl`, un hôte relancé depuis un terminal reste donc en GL (`env -u GSK_RENDERER`).

Zones changées (2026-10-06, sans essai en VM) : SDK Contracts 10.0.26100 (Windows 11 24H2),
`DirtyRegionMode = ReportOnly`. Les zones de chaque image, images sautées comprises, sont
cumulées ; seules elles sont copiées dans la texture de transfert (`CopySubresourceRegion`),
puis seules les tuiles qu'elles touchent sont comparées et envoyées (`Capture.GrabRegions`).
Image entière comme avant à la première image, après un redimensionnement, au-delà de 64 zones
en attente, ou si Windows ne donne pas de zones (avant 24H2).
