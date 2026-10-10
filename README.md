<p align="center">
  <img src="data/icons/hicolor/128x128/apps/io.github.melvincouwez.Vasistas.svg" width="128" height="128" alt="Logo de Vasistas">
</p>

<h1 align="center">Vasistas</h1>

<p align="center">Les applications Windows sur le bureau Linux, fenêtre par fenêtre.<br>
Une machine virtuelle légère et maîtrisée, assez fluide pour les outils de travail, sans bureau à distance.</p>

<p align="center">
  <img src="docs/screenshot.png" alt="Microsoft Word, ouvert dans la machine virtuelle Windows, à côté de l'application Vasistas, sur un fond d'écran d'elementary OS">
  <br>
  <sub>Word, ouvert dans la machine virtuelle Windows, à côté de l'application Vasistas (capture d'une version antérieure).</sub>
</p>

Vasistas fait tourner Windows dans une machine virtuelle et affiche ses applications sur le
bureau Linux comme des applications ordinaires. Word, Excel ou Power BI s'ouvrent depuis le menu
Applications et ont leur propre icône dans le dock. Leurs fenêtres se déplacent, se
redimensionnent et s'alternent comme les autres fenêtres. Le bureau de Windows reste caché : seules les
applications utilisées sont visibles.

Vasistas n'utilise ni session distante ni flux vidéo. Windows tourne sur votre ordinateur,
dans une machine virtuelle que Vasistas contrôle, aussi légère que possible et assez fluide
pour les outils de travail du quotidien.

Vasistas est conçu pour elementary OS et devrait fonctionner sur d'autres systèmes basés sur
Debian ou Ubuntu avec GTK 4 et Granite. La version 0.9 est une bêta : attendez-vous à des
défauts, et gardez une copie de vos documents importants.

## Notre approche

Maîtrisée, parce que nous choisissons et contrôlons chaque élément : la machine virtuelle est
lancée directement avec QEMU, l'écran, le clavier, la souris et le presse-papiers passent par
notre propre canal, et un petit agent dans Windows s'occupe des fenêtres. Windows est installé
depuis le support de Microsoft avec des réglages adaptés à cet usage, par exemple sans écran de
verrouillage et avec des mises à jour installées au moment que vous choisissez.

Légère, parce qu'aucun flux vidéo n'est encodé ni décodé : l'image de l'écran est lue dans une
mémoire partagée avec la machine virtuelle. Windows se met en pause quand vous ne l'utilisez
pas, et la mémoire dont Windows n'a pas besoin revient à Linux au fur et à mesure. Une étape
facultative retire la télémétrie et des services inutiles pour cet usage. Sur notre machine, avec Outlook ouvert, la
machine virtuelle utilise environ 4 Go sur les 8 Go qui lui sont attribués.

Assez fluide pour travailler : la saisie, le défilement et le passage entre Word, Excel,
Outlook ou Power BI se font sans saccade. Dans nos mesures, une touche apparaît à l'écran en 25 à
40 millisecondes environ. Vasistas n'est pas réglé pour la 3D ni les jeux (voir plus bas).

## Ce que nous visons

- Chaque fenêtre d'une application Windows est une vraie fenêtre du bureau, avec sa place dans
  le dock et dans le sélecteur de fenêtres. Les menus, les boîtes de dialogue et les
  infobulles apparaissent à l'emplacement attendu.
- Les dossiers Documents et Téléchargements apparaissent comme des lecteurs dans Windows, et
  les dossiers Documents, Images ou Téléchargements de Windows peuvent pointer vers eux. Un
  double-clic sur un fichier `.docx`, `.xlsx` ou `.pbix` dans Fichiers l'ouvre dans
  l'application Windows correspondante.
- Le texte, le texte mis en forme et les images se copient et se collent dans les deux sens.
- Windows joue le son par PipeWire et peut utiliser le micro. Vasistas lit le micro seulement
  lorsqu'une application Windows l'a ouvert. Le son peut être coupé dans l'application Vasistas.
- Windows prend l'échelle de l'écran qui contient la plupart de ses fenêtres, et les fenêtres
  de cet écran sont affichées pixel pour pixel, donc le texte reste net. Cliquer d'un écran à
  l'autre ne change pas l'échelle. Déplacer des fenêtres d'un écran à l'autre peut la changer,
  une fois le déplacement terminé.
- Windows se met en pause quand vous ne l'utilisez pas et reprend au clic suivant. La mémoire
  dont Windows n'a pas besoin revient à Linux.
- Un assistant d'installation prépare Windows dans la langue choisie. Les versions
  d'évaluation de 90 jours se téléchargent directement par les liens publics de Microsoft.
  Pour les autres versions, l'assistant ouvre la page officielle de Microsoft, puis vous
  sélectionnez le fichier ISO téléchargé. L'installation se fait sans intervention, avec
  un compte local, puis l'assistant installe Microsoft Office et d'autres applications
  courantes.
- Vous choisissez l'écran où s'ouvre chaque application (l'écran actif, le dernier utilisé ou
  un écran précis). Vasistas retient la taille et l'écran de chaque application pour chaque
  combinaison d'écrans, n'ouvre jamais une fenêtre plus grande que son écran, et
  « Réinitialiser les affichages » (application Vasistas, indicateur du panneau ou
  `vasistas reset-windows`) remet chaque fenêtre en place, centrée, à une taille raisonnable.
  Vasistas fait aussi cette réinitialisation automatiquement quand un écran est branché ou
  débranché.
- Windows suit le bureau : mode sombre, couleur d'accent et lissage des polices (niveaux de
  gris ou ClearType). Les notifications de Windows deviennent des notifications du bureau, et
  les icônes de la zone de notification de Windows (OneDrive, Teams…) apparaissent dans le
  panneau.
- Les imprimantes de Linux sont disponibles dans Windows, sans ouvrir CUPS au réseau.
- Un indicateur dans le panneau montre si Windows est démarré, ouvre les applications
  récentes, et démarre, met en veille ou arrête Windows.
- Un seul curseur règle l'affichage de Windows : Optimisé pour le courrier et les documents,
  Équilibré, ou Fluide pour Power BI, la vidéo et les longs défilements. Une application peut
  garder son propre mode tant qu'elle est au premier plan.
- La puissance attribuée à Windows s'adapte à la situation : moins sur batterie, plus quand une
  application lourde comme Power BI est ouverte. Windows peut être préparé à l'ouverture de session, démarré en veille,
  et arrêté après un délai sans fenêtre ouverte.
- Vasistas crée des points de restauration du disque de Windows avant l'allègement et, si vous
  activez l'option, avant Windows Update et avant chaque installation. Vous pouvez revenir à
  l'un d'eux en un clic.
- L'application Vasistas a un tableau de bord. Elle démarre ou arrête Windows, choisit les
  applications qui apparaissent dans le menu et les types de fichiers qui s'ouvrent dans
  Windows, allège Windows et vérifie les mises à jour. Elle lance aussi un diagnostic, qui
  répare ce qu'il peut et produit un rapport sans données personnelles.
- L'interface est disponible en français et en anglais.
- Expérimental : les fenêtres dont la barre de titre est dessinée par Windows (Explorateur de
  fichiers, anciennes boîtes de dialogue) peuvent recevoir la barre de titre du bureau, avec
  ses coins arrondis et son ombre. Office, Edge et les applications qui dessinent leur propre
  barre de titre gardent la leur. L'option s'active dans l'application Vasistas, dans
  Préférences, section Expérimental.
- Expérimental : un pilote d'affichage modifié donne à Windows le rythme d'un vrai écran à
  60 Hz. Ce pilote n'est pas fourni compilé : vous le compilez et l'installez séparément. Il
  demande le mode de signature de test de Windows et se désactive sur batterie. Voir
  [docs/pilote-maison.md](docs/pilote-maison.md).

## Fonctionnement

Windows tourne dans une machine virtuelle QEMU/KVM sur votre ordinateur. Au lieu de diffuser un
bureau à distance, Vasistas lit l'écran de Windows directement dans la mémoire partagée de QEMU
(affichage D-Bus) : rien n'est encodé ni envoyé sur un réseau. Un petit agent dans Windows
indique où se trouve chaque fenêtre et reçoit la souris, le clavier et le presse-papiers par un
canal virtio. Quand une fenêtre est recouverte par une autre dans Windows, l'agent la capture
(Windows.Graphics.Capture) et envoie ses zones modifiées, compressées, par ce même canal.
Côté Linux, chaque fenêtre Windows devient une fenêtre GTK 4 qui affiche sa partie de l'écran.
Cette fenêtre porte l'identité de l'application, pour que le bureau puisse la regrouper et la
décorer correctement. Les dossiers sont partagés par virtio-fs.

Les détails techniques sont dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) et le protocole
entre l'hôte et l'agent dans [PROTOCOL.md](PROTOCOL.md).

## Ce que Vasistas n'est pas

Vasistas est conçu pour les applications de bureau et n'est pas adapté aux jeux. Sans carte
graphique attribuée à la machine virtuelle, Windows fait son rendu avec le processeur. Les
applications de bureautique et de gestion restent réactives ; la 3D, la vidéo exigeante et les
jeux ne le sont pas. Par ailleurs, les systèmes anti-triche refusent en général les machines
virtuelles. Attribuer une carte
graphique dédiée à Windows est possible mais expérimental.

La webcam et le passage direct de périphériques USB ne sont pas encore pris en charge.

### Netteté sur plusieurs écrans

Windows n'a qu'un écran, donc une seule échelle. Si vos écrans ont des échelles différentes,
par exemple un écran externe à 100 % et l'écran d'un portable à 200 %, seules les fenêtres de
l'un d'eux peuvent être dessinées par Windows à la bonne taille. Les fenêtres de l'autre écran
sont redimensionnées par le bureau et peuvent paraître floues. Ce flou vient de la façon dont
l'image est rendue, et non de l'application. Ramener les fenêtres sur le premier écran règle
le problème.

Pour limiter cet effet :

- préférez les échelles entières (100 %, 200 %) : Windows a alors un palier exact, et une
  fenêtre sur l'autre écran est réduite exactement de moitié, ce qui reste lisible ;
- les échelles fractionnaires comme 167 % fonctionnent, mais Windows prend son palier le plus
  proche (175 %) et les fenêtres déplacées sur un autre écran perdent plus de détails ;
- gardez si possible les applications Windows utilisées ensemble sur le même écran.

## Configuration requise

- elementary OS 8 ou 9, ou un autre système basé sur Debian ou Ubuntu (Ubuntu 24.04 ou
  ultérieur) avec GTK 4 et Granite 7 ; seul le bureau Pantheon est testé pour l'instant
- un processeur avec la virtualisation matérielle activée (KVM), 16 Go de mémoire conseillés
  et environ 100 Go d'espace disque libre
- votre propre licence Windows, ou une version d'évaluation de 90 jours que l'assistant peut
  télécharger ; les licences ou abonnements d'Office et des autres logiciels payants

Pour installer une version sous licence sans saisir de clé tout de suite, l'assistant utilise
une clé générique publiée par Microsoft. Cette clé choisit seulement l'édition pendant
l'installation : elle n'active pas Windows et ne remplace pas une licence. Windows reste à
activer avec votre propre clé (Paramètres, Système, Activation).

## Installation

Téléchargez `vasistas-<version>.tar.gz` depuis la dernière version publiée, puis lancez :

```
tar xf vasistas-0.9.3.tar.gz
cd vasistas-0.9.3
./install.sh
```

Le script installe Vasistas dans votre dossier personnel, sans droits d'administrateur. S'il
manque des paquets du système, le script affiche la commande `sudo apt install …` à lancer.
Ouvrez ensuite « Vasistas » depuis le menu Applications et suivez l'assistant. L'application
Vasistas propose les mises à jour.

Pour préparer une installation de Windows faite par un autre moyen, voir
[docs/configure-windows.md](docs/configure-windows.md).

## Ligne de commande

La commande `vasistas` (dans `~/.local/bin`) fonctionne aussi depuis un terminal :

```
vasistas vm start|stop|status          # la machine virtuelle
vasistas launch-app winword            # une application connue
vasistas open ~/Documents/rapport.docx # un fichier, dans son application Windows
vasistas files list|set csv excel      # types de fichiers ouverts dans Windows
vasistas folders list|link|unlink      # dossiers de Windows qui pointent vers des dossiers Linux
vasistas exec 'Get-Process'            # un script PowerShell dans Windows
vasistas companion                     # l'application Vasistas
```

## Avec Lucarne

[Lucarne](https://github.com/melvincouwez-alt/lucarne) est un projet distinct qui ouvre les
applications web de Microsoft 365 dans des fenêtres du bureau. Les deux projets ne partagent
pas de code et fonctionnent chacun seul. Quand les deux sont installés, ils communiquent
uniquement par leurs commandes, cherchées dans `PATH` au besoin :

- Lucarne ouvre un document SharePoint ou OneDrive cliqué dans Office, dans la machine
  virtuelle, en lançant `vasistas launch "ms-word:ofe|u|<adresse du fichier>"`,
  `vasistas launch-app <id> [URL]` ou `vasistas open <fichier>`.
- L'application Vasistas affiche une section « Navigateur » dans ses Préférences quand la
  commande `lucarne` existe.
  L'application lit `lucarne status` et `lucarne config get`, et enregistre le choix avec
  `lucarne config set <appli> target vm|web`. La variable `VASISTAS_LUCARNE` remplace la
  commande.
- Les lanceurs d'Office dans la machine virtuelle utilisent les icônes `lucarne-<appli>` de
  Lucarne quand le thème d'icônes les contient, sinon les icônes reprises de Windows.

## Développement

L'hôte est écrit en Python avec GTK 4 et Granite (`host/vasistas`), l'agent Windows en C# pour
.NET Framework 4.8 (`guest/Vasistas.Agent`, compilé avec `dotnet build -c Release`).
`./check.sh` lance les tests et compile l'agent ; `tools/make-release.sh` prépare l'archive
d'une version publiée.

## Licence

Vasistas est publié sous licence MIT (voir [LICENSE](LICENSE)).

Vasistas contient [pycdlib](https://github.com/clalancette/pycdlib) (LGPL 2.1, dans `host/vendor`),
qui sert à construire le CD d'installation. L'agent Windows embarque les types d'interopérabilité
UI Automation d'[Interop.UIAutomationClient](https://github.com/FlaUI/UIAutomation-Interop)
(MIT) et la version Windows officielle de [zstd](https://github.com/facebook/zstd) 1.5.7
(`libzstd.dll`, BSD). Les correctifs du dossier `patches/` modifient QEMU et gardent sa licence
(GPL 2.0 ou ultérieure) ; `guest/viogpudo/vsync.patch` modifie le pilote d'affichage de
virtio-win et garde sa licence (BSD 3 clauses). Textes des licences et détails :
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Windows, Office, Power BI et les autres logiciels cités appartiennent à leurs éditeurs.
Vasistas ne contient aucun logiciel ni aucune licence de Microsoft : les images et
installateurs de Microsoft se téléchargent depuis ses sites officiels, et leur utilisation
reste soumise à leurs propres conditions de licence. Vasistas est un projet indépendant, sans
lien avec Microsoft ni approbation de sa part.
