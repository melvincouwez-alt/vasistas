# Protocole Vasistas

Canal : port virtio-serial `org.vasistas.0`. Côté hôte, QEMU expose un socket unix
serveur (`~/.local/share/vasistas/serial.sock`) ; l'hôte s'y connecte en client.
Côté invité : `\\.\Global\org.vasistas.0`.

## Trames

```
"VS"    marqueur, pour se recaler si le flux reprend au milieu d'une trame
u32 LE  longueur (octets qui suivent, type compris)
u8      type
...     charge utile
```

| type | sens | charge utile |
|------|------|--------------|
| 1 | les deux | JSON UTF-8, objet avec un champ `t` |
| 2 | invité -> hôte | tuile : `u32 id, u16 x, u16 y, u16 w, u16 h, u8 enc`, puis pixels |

Tuile : pixels BGRA 8 bits, alpha à 255, `w*4` octets par ligne. `enc` = 0 brut,
1 deflate brut (sans en-tête zlib), 2 zstd (trame complète, niveau 1), seulement si le
`hello` de l'hôte porte `zstd: true`. Les coordonnées sont relatives au coin haut gauche
de la zone visible de la fenêtre (bornes DWM, sans les bordures invisibles).
Toutes les coordonnées et tailles sont en pixels physiques de l'invité.

## Poignée de main

L'hôte envoie `hello` à la connexion puis toutes les secondes tant qu'il n'a pas reçu
de `hello` de l'invité. À chaque `hello` reçu, l'invité renvoie son `hello` puis
`window.new` pour chaque fenêtre suivie et une image complète de chacune.

## Invité -> hôte

- `hello {version, agentVersion, screen:[w,h], dpi}` : `version` = version du protocole (1),
  `agentVersion` = version de Vasistas pour laquelle l'agent est construit (« 0.9.0 », égale à
  `VERSION` de `host/vasistas/version.py`) ; l'hôte la compare avec `version.newer()` et propose
  une mise à jour de l'agent si celui-ci est plus ancien. Champ absent : agent antérieur à la 0.6.
- `window.new {id, title, rect:[x,y,w,h], kind, owner, maximized, dpi, nc, sizable}` (`sizable` faux : pas de bord redimensionnable, écran d'accueil)
  `kind` : `normal` | `dialog` | `popup`. `owner` : id ou 0. `dpi` : DPI de la fenêtre dans
  Windows (GetDpiForWindow), qui prend la nouvelle échelle seulement une fois le déplacement de la fenêtre terminé.
  `nc` : hauteur de la barre de titre dessinée par Windows, 0 si l'application dessine la sienne.
- `window.update {id, title?, rect?, maximized?, minimized?, dpi?, nc?}`
- `window.close {id}`
- `window.focus {id}` : fenêtre au premier plan dans l'invité
- `frame {id, w, h}` : fin d'une série de tuiles, l'image est cohérente
- `hover {id, hit, cursor}` : résultat de WM_NCHITTEST et curseur sous la souris,
  envoyé seulement quand l'une des deux valeurs change
- `window.request {id, action}` : l'utilisateur a demandé `maximize` ou `minimize` dans
  l'invité (bouton de la barre de titre). L'agent a déjà rétabli la fenêtre à l'état normal,
  l'hôte applique l'action à sa propre fenêtre.
- `launched {req, ok, error?}`
- `bench.posted {id, i}` : réponse à `bench.post` (banc de l'hôte)
- `log {msg}`

## Hôte -> invité

- `hello {version, zstd, scale}` : `zstd` = l'hôte décode les tuiles zstd ; `scale` = échelle de l'écran hôte (1.0, 1.25, 1.6667...).
  L'agent règle l'échelle de Windows sur la valeur la plus proche (100, 125, 150, 175 %...).
- `display {scale}` : l'échelle de l'écran hôte a changé

Champs facultatifs de `hello` et `display`, gardés jusqu'au prochain message qui les porte :

- `clamp` (vrai par défaut) : garde-fou à la création des fenêtres, voir « Placement des fenêtres ».
- `notifications` (faux par défaut) : bannières de Windows relayées à l'hôte, voir « Notifications ».
  Vrai : l'agent autorise les bannières dans Windows (`ToastEnabled` = 1), les lit et les déplace
  hors de l'écran. Faux : l'agent les désactive (`ToastEnabled` = 0, comme boot.ps1).
- `tray` (faux par défaut) : icônes de la zone de notification relayées, voir « Zone de notification ».
- `launch {req, cmd, args}`
- `bench.post {id, i}` : banc de l'hôte, un caractère posté à la fenêtre (WM_CHAR, sans la mettre
  au premier plan) ; l'agent renvoie `bench.posted` aussitôt, avant les tuiles qui en résultent
- `key {sc, ext, down}` : scancode jeu 1, `ext` pour le préfixe E0 ; `vk` à la place
  de `sc` pour les touches sans scancode simple (Pause)
- `mouse.move {id, x, y}`
- `mouse.button {id, x, y, button, down}` : 1 gauche, 2 milieu, 3 droit, 8/9 X1/X2
- `mouse.wheel {id, x, y, dx, dy}` : en unités WHEEL_DELTA (120 par cran)
- `window.activate {id}` : restaurer si réduite et passer au premier plan
- `window.deactivate {id}` : l'hôte a perdu le focus, fermer menus et popups
- `window.close {id}` : WM_CLOSE
- `window.resize {id, w, h}` : nouvelle taille de la zone visible
- `window.state {id, state}` : `maximize` | `restore` | `minimize`

## Apparence (hôte -> invité)

- `theme {dark, accent?}` : `dark` booléen, `accent` couleur `"#rrggbb"` ou absent (accent
  inchangé). Mode sombre : `AppsUseLightTheme` et `SystemUsesLightTheme` de
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize`. Accent :
  `HKCU\Software\Microsoft\Windows\DWM` `AccentColor` (ABGR, alpha FF), `ColorizationColor` et
  `ColorizationAfterglow` (ARGB, alpha C4) ; `...\Explorer\Accent` `AccentColorMenu` (ABGR),
  `StartColorMenu` (nuance foncée, ABGR) et `AccentPalette` (8 couleurs R, G, B, 0 du plus clair
  au plus foncé, dérivées de l'accent par la luminosité ; la 8e est gardée) ;
  `HKCU\Control Panel\Desktop` `AutoColorization` = 0. Les valeurs déjà justes ne sont pas
  réécrites ; s'il y a eu un changement, l'agent diffuse `WM_SETTINGCHANGE` « ImmersiveColorSet »
  (SendMessageTimeout, HWND_BROADCAST, SMTO_ABORTIFHUNG) pour que les applications suivent à chaud.
- `fonts {smoothing}` : `grayscale` (FE_FONTSMOOTHINGSTANDARD), `cleartype`
  (FE_FONTSMOOTHINGCLEARTYPE) ou `none` (lissage coupé). SystemParametersInfo
  SPI_SETFONTSMOOTHING / SPI_SETFONTSMOOTHINGTYPE avec SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
  seulement si le réglage change.

## Placement des fenêtres

Rectangles en pixels physiques de l'écran de Windows, sur la zone visible de la fenêtre (bornes
DWM, comme `rect` de window.new), l'agent ajoute lui-même les bordures invisibles.

- `window.place {id, x, y, w, h}` (hôte -> invité) : la fenêtre de premier niveau (pas un popup)
  est restaurée si elle est agrandie ou réduite dans Windows, sans être activée, puis posée à ce
  rectangle, ramené dans l'écran (hors de l'écran, SendInput ne pourrait plus l'atteindre). L'agent
  renvoie le `window.update` habituel.
- `windows.reset {max}` (hôte -> invité, `max` = 0.8 par défaut, borné entre 0.2 et 1) : pour chaque
  fenêtre suivie hors popups et hors fenêtres réduites : restaurée si agrandie, taille ramenée à
  au plus `max` fois la zone de travail de Windows sur chaque axe, puis centrée. La taille
  minimale de l'application est respectée : Windows l'impose pendant SetWindowPos
  (WM_WINDOWPOSCHANGING, WM_GETMINMAXINFO), l'agent relit la taille obtenue avant de centrer.
  Un `window.update` par fenêtre, puis :
- `windows.reset.done {count}` (invité -> hôte) : nombre de fenêtres replacées.

Garde-fou à la création : une nouvelle fenêtre de premier niveau (normale ou dialogue) plus
grande que l'écran de Windows ou qui en sort est ramenée dedans par la même règle, avec
`max` = 0.9, avant son premier `window.new`, sauf si elle a été créée agrandie. Désactivé par
`clamp: false` dans `hello` ou `display` (la fenêtre est alors seulement décalée pour tenir à
l'écran, comme avant).

## Notifications

Actives seulement avec `notifications: true` dans `hello`/`display`.

- `notify {id, app, appName, title, body}` (invité -> hôte) : bannière de Windows. `id` entier
  propre à l'agent ; `app` identifiant d'application comme dans window.new (`""` si l'application
  n'a pas de fenêtre suivie) ; `appName` nom affiché dans l'en-tête de la bannière (`""` s'il n'y
  en a pas) ; `title` ; `body` (lignes séparées par `\n`).
- `notify.activate {id}` (hôte -> invité) : clic sur la notification de l'hôte. L'agent invoque
  la bannière par UI Automation si elle existe encore (l'application s'ouvre comme sur un clic
  dans Windows), sinon ne fait rien.

Lecture : fenêtre de classe `Windows.UI.Core.CoreWindow` de ShellExperienceHost.exe dont le titre
contient « notification » (« Nouvelle notification », « New notification ») ou son équivalent
allemand, italien, néerlandais ou polonais, repérée par les événements Windows (affichage). Ses
textes sont lus par UI Automation COM (UIA3, vue brute ; l'API managée UIA2 ne voit pas le
contenu XAML). À l'apparition, la fenêtre ne contient qu'un ScrollViewer vide : elle est relue
toutes les 100 ms et à chaque événement UIA StructureChanged, pendant 30 s au plus tant qu'elle
est visible. Chaque élément cliquable portant au moins deux textes (ou un nom sur plusieurs
lignes) est une bannière ; textes dans l'ordre : nom de l'application, titre, corps (deux
textes : titre et corps). Une bannière n'est envoyée qu'après deux lectures identiques. L'agent
déplace la fenêtre hors de l'écran (-32000, -32000), sans la fermer, une fois la bannière lue
ou au bout de 2,5 s. Journal : l'arbre UIA de la première bannière lue, ou d'une bannière encore
vide après 3 s. UserNotificationListener, l'API prévue pour cet usage, exige une identité de paquet (MSIX),
que l'agent .NET Framework n'a pas.

## Zone de notification

Active seulement avec `tray: true` dans `hello`/`display`.

- `tray {items:[{key, tooltip, png, exe, source}]}` (invité -> hôte) : liste complète des icônes, envoyée
  à l'activation, à chaque `hello` et à chaque changement (relue toutes les 3 s). `key` : clé
  stable (`nis:<entrée de NotifyIconSettings>`, ou `tip:<infobulle>` faute de correspondance) ;
  `tooltip` : infobulle actuelle ; `png` : base64 (instantané `IconSnapshot` de NotifyIconSettings
  s'il est en PNG, sinon icône de l'exécutable en 64 px au plus, `""` si aucune) ; `exe` : chemin
  de l'exécutable (`""` si inconnu) ; `source` : `uia` (icône lue dans la barre des tâches) ou
  `registry` (repli, voir plus bas).
- `tray.click {key, button, x, y}` (hôte -> invité) : `button` `left` ou `right`. Gauche :
  InvokePattern de l'icône, sans bouger le pointeur. Droit (ou gauche sans Invoke) : l'agent
  amène le pointeur au bas de l'écran de Windows pour faire apparaître la barre des tâches masquée,
  puis clique au centre de l'icône ; le menu s'ouvre près de l'icône, en bas à droite de l'écran
  de Windows, et arrive comme une fenêtre `popup`. `x`, `y` (position du clic chez l'hôte) sont
  acceptés mais pas utilisés : l'application place elle-même son menu.

Icônes lues par UI Automation : boutons `NotifyItemIcon` de la barre des tâches XAML (Windows 11
22H2 et suivants) et de `TopLevelWindowForOverflowXamlIsland`, ou boutons des barres d'outils de
`SysPager` et `NotifyIconOverflowWindow` (ancienne zone Win32). Pour que les icônes cachées soient
lisibles sans ouvrir la fenêtre de dépassement (qui prendrait le premier plan), l'agent les
promeut toutes dans la zone visible : `IsPromoted` = 1 dans chaque entrée de
`HKCU\Control Panel\NotifyIconSettings`, et `EnableAutoTray` = 0. La barre des tâches est
masquée automatiquement (boot.ps1), pas supprimée. Lecture par UI Automation COM (UIA3) dans la
vue brute, depuis `Shell_TrayWnd` puis, si rien n'y est trouvé, depuis chaque fenêtre
`Windows.UI.Composition.DesktopWindowContentBridge` (île XAML) de la barre.

Repli si UI Automation ne trouve aucune icône : entrées de NotifyIconSettings dont l'exécutable
est en cours d'exécution (`source: "registry"`, infobulle = `InitialTooltip` ou description du fichier). Pas de
menu dans ce cas : `tray.click` gauche met au premier plan la fenêtre principale du programme,
ou le relance s'il n'en a pas ; droit ne fait rien. Le passage d'une source à l'autre et, la
première fois que UIA ne trouve rien, l'arbre brut de la barre des tâches sont écrits au journal.
- `capture {occluded_ms}` : délai de base entre deux captures d'une fenêtre recouverte
  (profil de puissance de l'hôte : 1000 sur batterie, 500, 250 en performances ; borné à
  100..5000). Un agent plus ancien l'ignore. `timer_ms` (0 sur batterie, 1 sinon) : l'agent maintient la
  minuterie de Windows à 1 ms tant que l'hôte est connecté et qu'au moins une fenêtre est suivie. Il écrit
  `GlobalTimerResolutionRequests=1` au démarrage (effectif au démarrage suivant de Windows) ; sans
  cette valeur, la demande vaudrait seulement pour l'agent.
