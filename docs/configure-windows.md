# Configurer Windows pour Vasistas

`install/configure-windows.ps1` prépare une machine Windows 10 ou 11 (VM QEMU/KVM) installée
sans l'assistant de Vasistas, ou répare une installation existante. Le script est idempotent :
ce qui est déjà en place n'est pas touché, et il ne redémarre jamais Windows lui-même.

## Lancer le script

Depuis l'hôte, Windows démarré et l'agent en marche :

```
script=~/.local/opt/vasistas/current/install/configure-windows.ps1
vasistas exec "@$script"                                            # applique
vasistas exec "$(printf '$Check = $true\n'; cat "$script")"        # contrôle seul
```

`vasistas exec` envoie le texte du script : les paramètres se posent en variables avant lui
(`$Check = $true`, `$User = 'nom'`, `$AutoLogonPassword = '…'`, `$AgentSource = '…'`,
`$NoDownload = $true`, `$SkipDrivers = $true`).

Dans Windows, depuis un PowerShell ouvert en administrateur (le script est aussi sur le CD de
partage de Vasistas) :

```
powershell -ExecutionPolicy Bypass -File configure-windows.ps1 -Check
powershell -ExecutionPolicy Bypass -File configure-windows.ps1 -User nom -AutoLogonPassword '…'
```

| Paramètre | Effet |
|---|---|
| `-Check` | contrôle seul : rapporte ce qui manque, ne modifie rien |
| `-User NOM` | compte qui ouvre la session Windows (défaut : l'utilisateur courant) |
| `-AutoLogonPassword MDP` | mot de passe de ce compte, pour l'ouverture de session automatique |
| `-AgentSource SOURCE` | agent Vasistas : dossier, `.zip` ou URL (défaut : CD de partage) |
| `-NoDownload` | aucun téléchargement : CD virtio et MSI WinFsp locaux seulement |
| `-SkipDrivers` | ne vérifie ni n'installe les pilotes virtio |

Chaque étape s'affiche avec son état (déjà fait, fait, à faire, attention, ignoré, échec). La
dernière ligne est un objet JSON `{"ok", "ready", "check", "reboot", "steps"}` que l'hôte peut
lire. Le code de sortie vaut 1 si une étape a échoué, 2 pour un paramètre inconnu.

## Les étapes

1. Droits administrateur, puis résolution du compte cible (compte local d'abord).
2. Pilotes virtio : `virtio-win-gt-x64.msi` cherché sur les lecteurs (CD virtio-win), sinon
   téléchargé chez Fedora. Pendant leur installation, le canal de l'agent peut se couper
   quelques secondes ; un redémarrage est ensuite demandé.
3. WinFsp, nécessaire aux dossiers partagés : MSI local, sinon winget (`WinFsp.WinFsp`), sinon
   la dernière version publiée sur GitHub.
4. Service VirtIO-FS lancé par WinFsp (clé `HKLM\SOFTWARE\WOW6432Node\WinFsp\Services\virtiofs`,
   comme `boot.ps1`). Le service `VirtioFsSvc` de virtio-win repasse en démarrage manuel s'il
   était automatique : il monterait un dossier en concurrence avec WinFsp.
5. Agent Vasistas dans `C:\Program Files\Vasistas\Agent`, `boot.ps1`, marqueur `installed`
   (sans lui, `boot.ps1` éteint Windows à l'ouverture de session) et tâche planifiée
   « Vasistas » à l'ouverture de session du compte, avec privilèges élevés. Un fichier
   installé plus récent que la source (agent mis à jour à chaud) est gardé. Si l'agent tourne,
   ses fichiers sont renommés et la nouvelle version part à son prochain lancement : le
   processus n'est jamais arrêté.
6. Réglages de la machine (ceux de `specialize.ps1`) : ni veille ni hibernation sur secteur,
   pas d'écran de verrouillage, invite UAC sur le bureau courant, pas de redémarrage
   automatique des mises à jour pendant une session.
7. Réglages de la session (ceux de `boot.ps1`) : animations et transparence coupées, fond noir,
   bannières de notification coupées (l'agent les rétablit pour les relayer au bureau si
   l'option est active), Office sans accélération matérielle, barre des tâches masquée. Si la
   session du compte n'est pas ouverte, `boot.ps1` les appliquera à la prochaine ouverture. Le
   fond d'écran et la barre des tâches changent à la prochaine ouverture de session :
   l'Explorateur n'est pas relancé.
8. Ouverture de session automatique : sans elle, Windows attend à l'écran de connexion et
   Vasistas ne voit aucune fenêtre. Avec `-AutoLogonPassword`, le mot de passe est vérifié,
   puis rangé en secret LSA (pas en clair dans le registre).

## Revenir en arrière

- Agent : supprimer la tâche planifiée « Vasistas » (`Unregister-ScheduledTask Vasistas`) et le
  dossier `C:\Program Files\Vasistas`.
- WinFsp et pilotes virtio : Paramètres › Applications › Applications installées.
- Réglages de la machine et de la session : valeurs de registre listées dans le script, à
  supprimer ; `powercfg /restoredefaultschemes` pour l'alimentation.
- Ouverture de session automatique : `AutoAdminLogon` à `0` dans
  `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon`.

## Limites

- Le mot de passe passé par `vasistas exec` transite par le canal de l'agent et un fichier
  temporaire de Windows, effacé après l'exécution.
- Le contrôle ne sait pas lire le secret LSA : une ouverture de session automatique active est
  jugée correcte d'après le registre seulement.
- Lancé sous le compte SYSTEM, le script ne peut pas deviner le compte cible : passer `-User`.
