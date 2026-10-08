# Le pilote d'écran maison de Vasistas

Cette notice explique ce que fait le pilote d'écran que Vasistas pose dans Windows, pourquoi il existe, comment le régler et comment revenir en arrière.

## Le problème de départ

Windows tourne dans une machine virtuelle sans carte graphique à lui. Il affiche sur une carte virtuelle VirtIO GPU, avec le pilote « Red Hat VirtIO GPU DOD » (viogpudo). DOD veut dire *Display Only Driver* : le pilote sait poser une image à l'écran, rien de plus. Tout le dessin se fait au processeur, avec WARP, le moteur de rendu logiciel de Windows.

Ce pilote d'origine ne donne pas de fréquence d'écran à Windows. Un vrai écran envoie un signal à chaque rafraîchissement (le *vsync*, 60 fois par seconde sur un écran à 60 Hz), et le compositeur de Windows (DWM) se cale dessus pour assembler les fenêtres. Sans ce signal, DWM se cale sur la minuterie système, qui avance par pas de 15,6 ms. Les applications et DWM ne tombent pas en phase et une partie des images se perd.

Mesuré le 6 octobre 2026 : environ 41 images par seconde sortaient de la VM, quelle que soit la taille de la fenêtre. Edge plafonnait à 64 images par seconde tout rond, le signe d'une cadence imposée par la minuterie et non par un écran.

## Ce que fait le pilote maison

C'est le pilote viogpudo d'origine, recompilé avec un correctif (`guest/viogpudo/vsync.patch`). Il fait croire à Windows qu'il pilote un écran à 60 Hz :

1. Les modes d'affichage sont déclarés à 60 Hz. Sans fréquence déclarée, Windows refuse le signal de rafraîchissement.
2. Le pilote accepte que Windows active ou coupe ce signal (fonction `DxgkDdiControlInterrupt`). Il répond aussi quand Windows demande où en est le balayage de l'écran (`DxgkDdiGetScanLine`) : la position est calculée d'après le temps écoulé depuis le dernier signal.
3. Quand Windows active le signal, le pilote arme une minuterie haute résolution réglée sur 16,67 ms exactement, au lieu du pas habituel de 15,6 ms.
4. À chaque tour de minuterie, il prévient Windows qu'un rafraîchissement vient d'avoir lieu (`DXGK_INTERRUPT_DISPLAYONLY_VSYNC`). DWM compose alors au rythme d'un vrai écran.

Le reste du pilote ne change pas : l'image part toujours vers Linux de la même façon.

## Les modes

Le mode est un nombre rangé dans la clé du service du pilote, `HKLM\SYSTEM\CurrentControlSet\Services\VioGpuDod`, valeur `VasistasVsync`. Chaque bit active une partie du mécanisme :

| Valeur | Ce qui est actif | Usage |
|---|---|---|
| 0 | rien, le pilote se comporte comme l'original | économie, ou retour arrière rapide |
| 1 | modes à 60 Hz et fonctions de réponse, sans minuterie | diagnostic |
| 3 | idem, plus la minuterie, sans signal envoyé à Windows | diagnostic |
| 7 | tout : le vrai vsync à 60 Hz | réglage normal |

Le pilote ne lit cette valeur qu'à son chargement : un changement prend effet au **prochain démarrage de Windows**. La page Performances de Vasistas affiche alors un bouton « Redémarrer Windows ».

Une seconde valeur, `VasistasVsyncOnce`, sert aux essais : elle est lue puis effacée au chargement. Si un essai fait planter Windows, il ne se répète pas au démarrage suivant.

## Où le régler dans Vasistas

Page **Performances** de l'application compagnon :

- **De base** : le curseur « Fluidité ou autonomie ». Autonomie coupe la synchronisation (mode 0), les trois autres crans la mettent en mode 7.
- **Avancé**, section Affichage : « Synchronisation verticale » choisit l'un des quatre modes.

Vasistas écrit le mode dans Windows à chaque démarrage et quand le réglage change. Si le pilote maison n'est pas posé, il ne touche à rien et le réglage est grisé.

Les autres réglages de la section Affichage vont avec le pilote :

- **Minuterie précise (1 ms)** : l'agent demande à Windows une minuterie à 1 ms. Les applications se réveillent plus souvent et livrent leurs images plus régulièrement. Avec le pilote maison, les deux gains s'ajoutent.
- **Fenêtres recouvertes** : délai entre deux captures d'une fenêtre cachée derrière une autre.
- **Rendu des fenêtres sous Linux** : Vulkan ou OpenGL pour dessiner les fenêtres côté Linux.

## Ce que ça change, en chiffres

Images par seconde sorties de la VM, mesurées le 6 octobre 2026 :

| Configuration | Grande fenêtre (2184x861) | Petite fenêtre |
|---|---|---|
| pilote d'origine | 42,6 | 42,6 |
| pilote maison (mode 7) | 45 | 50 à 51 |
| pilote maison + minuterie 1 ms | 45 | 53 à 55 |

Edge passe de 64 à 60,0 images par seconde pile : Windows suit bien un écran à 60 Hz. Les grandes fenêtres restent limitées par le dessin au processeur (WARP et DWM), pas par la cadence.

Coût : au repos, la VM prend environ 4 points de processeur de plus en mode 7 (23,8 % contre 19,6 %). Vasistas coupe donc le vsync (mode 0) sur le profil batterie : ordinateur sur batterie, mode Optimisé ou performance de la machine en Économie. Le pilote ne relit ce réglage qu'à son chargement, le changement vaut donc pour le démarrage suivant de Windows.

## Comment il est installé

Le pilote est compilé sous Linux par `guest/viogpudo/build.sh` (compilation croisée avec clang-cl et le kit de pilotes Windows), puis posé par `guest/viogpudo/install.ps1` :

- un certificat « CN=Vasistas test » est créé dans Windows et sert à signer le pilote ;
- le fichier va dans `C:\Windows\System32\drivers\viogpudo-vasistas.sys`, à côté du pilote d'origine, qui n'est pas effacé ;
- le service du pilote pointe vers le nouveau fichier ; l'ancien chemin est gardé dans `ImagePathVasistasOrig` ;
- Windows passe en mode *test-signing*, nécessaire pour charger un pilote signé par un certificat maison.

Ce mode de signature de test affaiblit un peu la protection de Windows contre les pilotes non approuvés. Pour une VM de travail sans accès aux autres machines, c'est un compromis acceptable, mais il faut le savoir.

## Revenir en arrière

Trois niveaux, du plus léger au plus complet :

1. **Couper le vsync** : mode 0 dans la page Performances, puis redémarrer Windows. Le pilote maison reste chargé mais se comporte comme l'original.
2. **Remettre le pilote d'origine** : lancer `install.ps1` avec `$Restore = $true`. Le service repointe vers le fichier d'origine au prochain démarrage.
3. **Point de restauration** : créer un point avant la pose (onglet Restauration ou `vasistas restore create avant-pilote`), puis y revenir en cas de problème.

## Dépannage

- **Écran noir ou « aucun écran » dans Windows** : le pilote annonce des fonctions sans les modes à 60 Hz, ou l'inverse. Revenir au mode 0 avec `VasistasVsyncOnce`, ou restaurer.
- **Code 43 sur la carte graphique** : le pilote déclare le signal sans répondre à la position de balayage. Ça arrive seulement avec un pilote recompilé à la main.
- **Écran bleu** : QEMU arrête la VM. Pour garder l'état et l'examiner, passer `set-action panic=pause` par QMP.
- **Le lissage des polices est repassé en niveaux de gris** après la pose : Windows remet ce réglage quand le pilote d'écran change. Page Écrans, « Lissage des polices » sur ClearType : Vasistas l'impose ensuite à chaque démarrage.
