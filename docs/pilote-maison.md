# Le pilote d'écran maison de Vasistas

Cette notice explique ce que fait le pilote d'écran modifié qui peut être installé dans Windows pour Vasistas, pourquoi il existe, comment le régler et comment revenir en arrière. Ce pilote est expérimental : Vasistas ne l'installe pas automatiquement et ne le fournit pas compilé.

## Le problème de départ

Windows tourne dans une machine virtuelle sans carte graphique dédiée. Windows affiche son image sur une carte virtuelle VirtIO GPU, avec le pilote « Red Hat VirtIO GPU DOD » (viogpudo). DOD signifie *Display Only Driver* : le pilote affiche une image à l'écran et ne fait rien d'autre. Tout le dessin se fait avec le processeur, avec WARP, le moteur de rendu logiciel de Windows.

Ce pilote d'origine ne donne pas de fréquence d'écran à Windows. Un vrai écran envoie un signal à chaque rafraîchissement (le *vsync*, 60 fois par seconde sur un écran à 60 Hz), et le compositeur de Windows (DWM) se synchronise sur ce signal pour assembler les fenêtres. Sans ce signal, DWM se synchronise sur la minuterie système, qui avance par pas de 15,6 ms. Les applications et DWM ne sont pas en phase, et une partie des images est perdue.

Mesure du 6 octobre 2026 : la VM produisait environ 41 images par seconde, quelle que soit la taille de la fenêtre. Edge plafonnait à exactement 64 images par seconde, ce qui indique une cadence imposée par la minuterie et non par un écran.

## Ce que fait le pilote maison

Le pilote maison est le pilote viogpudo d'origine, recompilé avec un correctif (`guest/viogpudo/vsync.patch`). Il présente à Windows un écran à 60 Hz :

1. Les modes d'affichage sont déclarés à 60 Hz. Sans fréquence déclarée, Windows refuse le signal de rafraîchissement.
2. Le pilote accepte que Windows active ou désactive ce signal (fonction `DxgkDdiControlInterrupt`). Il répond aussi quand Windows demande où en est le balayage de l'écran (`DxgkDdiGetScanLine`) : la position est calculée d'après le temps écoulé depuis le dernier signal.
3. Quand Windows active le signal, le pilote arme une minuterie haute résolution réglée sur 16,67 ms exactement, au lieu du pas habituel de 15,6 ms.
4. À chaque échéance de la minuterie, le pilote signale à Windows qu'un rafraîchissement vient d'avoir lieu (`DXGK_INTERRUPT_DISPLAYONLY_VSYNC`). DWM compose alors au rythme d'un vrai écran.

Le reste du pilote ne change pas : l'image est transmise à Linux de la même façon qu'avant.

## Les modes

Le mode est un nombre enregistré dans la clé du service du pilote, `HKLM\SYSTEM\CurrentControlSet\Services\VioGpuDod`, valeur `VasistasVsync`. Chaque bit active une partie du mécanisme :

| Valeur | Ce qui est actif | Usage |
|---|---|---|
| 0 | rien, le pilote se comporte comme l'original | économie d'énergie, ou retour arrière rapide |
| 1 | modes à 60 Hz et fonctions de réponse, sans minuterie | diagnostic |
| 3 | idem, plus la minuterie, sans signal envoyé à Windows | diagnostic |
| 7 | tout : le vrai vsync à 60 Hz | réglage normal |

Le pilote lit cette valeur seulement à son chargement : un changement prend effet au **prochain démarrage de Windows**. La page Affichage de l'application Vasistas affiche alors un bandeau avec un bouton « Redémarrer Windows ».

Une seconde valeur, `VasistasVsyncOnce`, sert aux essais : le pilote la lit puis l'efface au chargement. Si un essai fait planter Windows, l'essai n'est pas répété au démarrage suivant.

## Où le régler dans Vasistas

Page **Affichage** de l'application Vasistas :

- le curseur « Performance d'affichage » : Optimisé désactive la synchronisation (mode 0), Équilibré et Fluide la règlent sur le mode 7 ;
- **Options avancées**, en bas de la page, section Images : « Synchronisation verticale » choisit l'un des quatre modes.

Vasistas écrit le mode dans Windows à chaque démarrage et quand le réglage change. Si le pilote n'est pas installé, Vasistas ne modifie rien dans Windows et le réglage reste sans effet. La section Expérimental des Préférences indique si le pilote est installé.

D'autres réglages de la page Affichage complètent le pilote :

- **Minuterie précise (1 ms)** (Options avancées, section Images) : l'agent demande à Windows une minuterie à 1 ms. Les applications se réveillent plus souvent et livrent leurs images plus régulièrement. Avec le pilote modifié, les deux gains s'ajoutent.
- **Fenêtres recouvertes** (même section) : délai entre deux captures d'une fenêtre cachée derrière une autre.
- **Moteur de rendu** (carte « Veille et options ») : Vulkan ou OpenGL pour dessiner les fenêtres côté Linux.

## Effets mesurés

Images par seconde produites par la VM, mesurées le 6 octobre 2026 :

| Configuration | Grande fenêtre (2184x861) | Petite fenêtre |
|---|---|---|
| pilote d'origine | 42,6 | 42,6 |
| pilote maison (mode 7) | 45 | 50 à 51 |
| pilote maison + minuterie 1 ms | 45 | 53 à 55 |

Edge passe de 64 à exactement 60,0 images par seconde : Windows suit bien un écran à 60 Hz. Les grandes fenêtres restent limitées par le dessin au processeur (WARP et DWM), pas par la cadence.

Coût : au repos, la VM utilise environ 4 points de processeur de plus en mode 7 (23,8 % contre 19,6 %). Vasistas désactive donc le vsync (mode 0) sur le profil batterie, c'est-à-dire ordinateur sur batterie, mode Optimisé ou profil de performance de la machine sur Économie. Le pilote relit ce réglage seulement à son chargement ; le changement s'applique donc au démarrage suivant de Windows.

## Comment il est installé

Le pilote est compilé sous Linux par `guest/viogpudo/build.sh` (compilation croisée avec clang-cl et le kit de pilotes Windows), puis installé par `guest/viogpudo/install.ps1` :

- un certificat « CN=Vasistas test » est créé dans Windows et sert à signer le pilote ;
- le fichier est copié dans `C:\Windows\System32\drivers\viogpudo-vasistas.sys`, à côté du pilote d'origine, qui n'est pas supprimé ;
- le service du pilote pointe vers le nouveau fichier ; l'ancien chemin est conservé dans `ImagePathVasistasOrig` ;
- Windows passe en mode *test-signing*, nécessaire pour charger un pilote signé par un certificat maison.

Ce mode de signature de test affaiblit légèrement la protection de Windows contre les pilotes non approuvés. Pour une VM de travail sans accès aux autres machines, ce compromis reste acceptable, à condition d'en être informé.

## Revenir en arrière

Trois niveaux, du plus léger au plus complet :

1. **Désactiver le vsync** : mode 0 dans la page Affichage (Options avancées), puis redémarrer Windows. Le pilote maison reste chargé mais se comporte comme l'original.
2. **Remettre le pilote d'origine** : lancer `install.ps1` avec `$Restore = $true`. Le service pointe de nouveau vers le fichier d'origine au prochain démarrage.
3. **Point de restauration** : créer un point avant l'installation du pilote (onglet Restauration ou `vasistas restore create avant-pilote`), puis y revenir en cas de problème.

## Dépannage

- **Écran noir ou « aucun écran » dans Windows** : le pilote annonce des fonctions sans les modes à 60 Hz, ou l'inverse. Revenir au mode 0 avec `VasistasVsyncOnce`, ou revenir à un point de restauration.
- **Code 43 sur la carte graphique** : le pilote déclare le signal sans répondre à la position de balayage. Ce cas se produit seulement avec un pilote compilé sans le correctif complet.
- **Écran bleu** : QEMU arrête la VM. Pour garder l'état et l'examiner, passer `set-action panic=pause` par QMP.
- **Le lissage des polices est repassé en niveaux de gris** après l'installation : Windows réinitialise ce réglage quand le pilote d'écran change. Dans la page Windows, section Intégration au bureau, réglez « Lissage des polices » sur ClearType : Vasistas applique ensuite ce réglage à chaque démarrage.
