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
1 deflate brut (sans en-tête zlib). Les coordonnées sont relatives au coin haut gauche
de la zone visible de la fenêtre (bornes DWM, sans les bordures invisibles).
Toutes les coordonnées et tailles sont en pixels physiques de l'invité.

## Poignée de main

L'hôte envoie `hello` à la connexion puis toutes les secondes tant qu'il n'a pas reçu
de `hello` de l'invité. À chaque `hello` reçu, l'invité renvoie son `hello` puis
`window.new` pour chaque fenêtre suivie et une image complète de chacune.

## Invité -> hôte

- `hello {version, screen:[w,h], dpi}`
- `window.new {id, title, rect:[x,y,w,h], kind, owner, maximized}`
  `kind` : `normal` | `dialog` | `popup`. `owner` : id ou 0.
- `window.update {id, title?, rect?, maximized?, minimized?}`
- `window.close {id}`
- `window.focus {id}` : fenêtre au premier plan dans l'invité
- `frame {id, w, h}` : fin d'une série de tuiles, l'image est cohérente
- `hover {id, hit, cursor}` : résultat de WM_NCHITTEST et curseur sous la souris,
  envoyé seulement quand l'une des deux valeurs change
- `window.request {id, action}` : l'utilisateur a demandé `maximize` ou `minimize` dans
  l'invité (bouton de la barre de titre). L'agent a déjà rétabli la fenêtre à l'état normal,
  l'hôte applique l'action à sa propre fenêtre.
- `launched {req, ok, error?}`
- `log {msg}`

## Hôte -> invité

- `hello {version, scale}` : `scale` = échelle de l'écran hôte (1.0, 1.25, 1.6667...).
  L'agent règle l'échelle de Windows sur la valeur la plus proche (100, 125, 150, 175 %...).
- `display {scale}` : l'échelle de l'écran hôte a changé
- `launch {req, cmd, args}`
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
