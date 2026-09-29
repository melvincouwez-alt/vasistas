# Vasistas

Vasistas affiche les applications d'une machine virtuelle Windows sur le bureau Linux, une
fenêtre à la fois. Word, Excel, Outlook ou Power BI s'ouvrent depuis le menu Applications,
ont leur icône dans le dock, se déplacent et se redimensionnent comme les autres fenêtres. Le
bureau de Windows, lui, n'apparaît jamais.

Conçu pour elementary OS (bureau Pantheon, Wayland). Version 0.5, expérimentale.

## Pourquoi ne pas passer par RDP

Les outils existants (WinApps, WinBoat, LinOffice, Cassowary…) reposent sur le bureau à
distance de Windows (RDP et RemoteApp) : Windows encode chaque fenêtre en flux vidéo, FreeRDP
le décode côté Linux, comme pour un serveur situé à l'autre bout du réseau. Ça marche, mais
la machine virtuelle tourne sur le même ordinateur, et ce détour se paie :

- l'édition Famille de Windows n'a pas de serveur RDP, il faut une édition Professionnelle
  ou Entreprise ;
- chaque image passe par un encodage et un décodage, avec la latence et le travail du
  processeur qui vont avec ;
- les fenêtres affichées par FreeRDP s'intègrent mal au bureau : icônes génériques, fenêtres
  regroupées sous le même programme, menus et bulles décalés ;
- la session RDP a sa propre vie (déconnexions, reconnexions, session verrouillée).

Vasistas n'utilise pas RDP. QEMU partage l'écran de Windows en mémoire avec l'application
Linux (affichage D-Bus de QEMU, sans encodage ni réseau) ; un petit agent dans Windows
indique où se trouve chaque fenêtre et reçoit souris, clavier et presse-papiers par un canal
virtio. Chaque fenêtre Windows devient une vraie fenêtre GTK 4 du bureau, avec l'identifiant
et l'icône de son application.

## Ce que ça apporte

- Toutes les éditions de Windows 10 et 11, Famille comprise.
- Une fenêtre du bureau par fenêtre Windows, une icône par application dans le dock, l'icône
  de chaque application reprise au gabarit des icônes d'elementary.
- Un double-clic sur un .docx, un .xlsx ou un .pbix dans Fichiers l'ouvre dans l'application
  Windows ; les types concernés se choisissent.
- Les dossiers Linux (Documents, Téléchargements…) sont des lecteurs dans Windows, sans
  partage réseau ; les dossiers Documents, Images… de Windows peuvent pointer dessus.
- Presse-papiers commun : texte, texte mis en forme, images.
- Windows se met en veille tout seul quand il ne sert pas et se réveille au premier clic ; la
  mémoire qu'il n'utilise pas revient à Linux.
- Installation guidée : l'ISO de Windows est téléchargée chez Microsoft dans la langue voulue,
  Windows s'installe sans intervention (compte local, pas de compte Microsoft), puis Office
  (offre et langue au choix) et d'autres applications courantes.
- Une application compagnon pour démarrer ou arrêter Windows, régler sa puissance, choisir
  les applications du menu, alléger Windows et suivre les mises à jour.

## Ce que Vasistas n'est pas

Vasistas n'est pas fait pour les jeux. Sans carte graphique prêtée à la machine virtuelle,
Windows dessine en logiciel : les applications de bureau restent fluides, mais la 3D, les
vidéos exigeantes et les jeux ne le sont pas, et les anti-triche refusent souvent les
machines virtuelles. Pour jouer, Steam et Proton font bien mieux. Le prêt de la carte
graphique dédiée existe, mais il reste expérimental.

Pas de son, de webcam ni de périphériques USB pour l'instant : les visioconférences se font
mieux depuis Linux.

## Prérequis

- elementary OS 8 ou 9 (ou un système à base d'Ubuntu 24.04 ou plus récent avec GTK 4 et
  Granite 7 ; seul Pantheon est testé) ;
- processeur avec virtualisation matérielle activée (KVM), 16 Go de mémoire conseillés,
  100 Go libres sur le disque ;
- une licence Windows, ou une version d'évaluation de 90 jours téléchargée par l'assistant ;
  une licence ou un abonnement pour Office et les logiciels payants.

## Installation

Téléchargez l'archive `vasistas-<version>.tar.gz` de la dernière version publiée, puis :

```
tar xf vasistas-0.5.0.tar.gz
cd vasistas-0.5.0
./install.sh
```

Le script installe Vasistas dans votre dossier personnel, sans droits administrateur. S'il
manque des paquets du système, il donne la commande `sudo apt install …` à lancer. Ouvrez
ensuite « Vasistas » dans le menu Applications : l'assistant prend la suite. Les mises à
jour se font depuis l'application (menu, « Rechercher des mises à jour »).

Pour préparer un Windows déjà installé autrement : `install/configure-windows.ps1`, voir
[docs/configure-windows.md](docs/configure-windows.md).

## Commandes

La commande `vasistas` (dans `~/.local/bin`) pilote aussi tout depuis un terminal :

```
vasistas vm start|stop|status          # machine virtuelle
vasistas launch-app winword            # une application connue
vasistas open ~/Documents/rapport.docx # un fichier, dans l'application désignée
vasistas files list|set csv excel      # types de fichiers ouverts dans Windows
vasistas folders list|link|unlink      # dossiers de Windows reliés à Linux
vasistas exec 'Get-Process'            # script PowerShell dans Windows
vasistas companion                     # application compagnon
```

## Développement

Le code de l'hôte est en Python avec GTK 4 et Granite (`host/vasistas`), l'agent Windows en
C# .NET Framework 4.8 (`guest/Vasistas.Agent`, `dotnet build -c Release`). `./check.sh` lance
les tests et compile l'agent ; `tools/make-release.sh` prépare l'archive d'une version.
L'architecture est décrite dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), le protocole
entre l'hôte et l'agent dans [PROTOCOL.md](PROTOCOL.md).

## Licence

Vasistas est distribué sous licence MIT (fichier [LICENSE](LICENSE)).

Windows, Office, Power BI et les autres logiciels cités appartiennent à leurs éditeurs ;
Vasistas ne fournit ni licence ni logiciel Microsoft, il télécharge les installateurs
officiels.
