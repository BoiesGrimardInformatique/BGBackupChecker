# backup-monitor — Surveillance des courriels de résultats de sauvegarde

Outil local qui lit **en lecture seule** les courriels de résultats de
sauvegarde — Macrium Reflect, Retrospect, SQL Server Agent, Proxmox Backup
Server et scripts personnalisés —, les classe (succès / avertissement
/ erreur), détecte les **backups manquants** et génère un **tableau de bord
HTML local** auto-actualisé.

Mode principal : exécution sur le **poste Windows 11** dont le Outlook local a
déjà la boîte — l'outil lit Outlook directement (COM/MAPI), **sans aucun mot de
passe à configurer**. Des modes EWS/IMAP existent en repli pour un accès
serveur direct (voir plus bas).

## Garanties

- **Aucune modification dans Outlook** : pas de déplacement, pas de
  suppression, pas de marquage « lu ». Seuls le sujet, l'expéditeur, la date et
  le corps sont lus.
- **Aucune exposition web** : le tableau est un simple fichier
  `tableau-backups.html` ouvert localement (`file://`), sans serveur, sans
  requête sortante depuis la page.
- **Aucun secret stocké** en mode Outlook. (Modes ews/imap : mot de passe dans
  le trousseau système via `keyring`, jamais dans les fichiers.)

## Installation (Windows 11, mode Outlook)

1. Installer Python 3.10+ depuis https://www.python.org (cocher « Add python.exe
   to PATH »).
2. Copier ce dossier sur le poste (ex. `C:\Outils\backup-monitor`).
3. Double-cliquer `install.bat`. **Il déroule toute l'installation, dans
   l'ordre**, pour terminer sur un outil déjà configuré :
   - crée l'environnement Python et installe les dépendances (hors-ligne depuis
     `wheels\` si le dossier est présent) ;
   - lance l'**autotest hors-ligne** (`selftest`) : analyseurs, verrous des
     pièces jointes et génération du tableau, sans toucher Outlook ni le réseau ;
   - lance l'**assistant** : il liste d'abord les **boîtes** du profil Outlook
     et vous en fait **choisir une** ; il ne **scanne alors que les dossiers de
     cette boîte** (lecture seule) — pas tout le profil, pour ne pas figer
     Outlook — suggère ceux dont le nom contient « macrium »/« retrospect »,
     vous fait choisir les dossiers à surveiller, et enregistre dans
     `config.yaml` ;
   - fait une **première analyse** et génère `tableau-backups.html`.

   > Si Outlook n'est pas encore prêt sur le poste, l'installation s'arrête
   > proprement après l'autotest et invite à terminer la configuration plus
   > tard avec `lancer.bat setup` (ou
   > `venv\Scripts\python -m backup_monitor setup`) — **aucune erreur bloquante**.

4. **Bilan de calibrage** (recommandé après la première analyse) :
   `venv\Scripts\python -m backup_monitor diagnose` — comptes par produit et par
   état, taux d'extraction machine/tâche/client, et liste des courriels non
   reconnus avec extrait, pour ajuster les motifs (`parsers`) rapidement.
5. Déclarer les tâches attendues (`expected_jobs`) et les clients (`clients`)
   dans `config.yaml` (détection des backups manquants et suivi par client).
6. Ouvrir `tableau-backups.html` (déjà généré par l'installation ; `lancer.bat`
   le rouvre à chaque exécution).

L'assistant `setup` peut être relancé n'importe quand (changement de dossier,
nouvelle boîte) : `venv\Scripts\python -m backup_monitor setup` ou
`lancer.bat setup`. Il ne touche que la boîte choisie et la liste des dossiers,
le reste de `config.yaml` est préservé.

## Utilisation depuis une clé USB

Copier ce dossier sur la clé, puis sur n'importe quel poste :
**double-cliquer `lancer.bat`** — sans argument il analyse puis ouvre le
tableau ; avec un argument il le transmet (`lancer.bat setup`,
`lancer.bat diagnose`, `lancer.bat selftest`…).

**Première utilisation sur un poste** : si aucun dossier n'est encore choisi,
`lancer.bat` (sans argument) crée l'environnement puis **enchaîne directement
sur l'assistant de configuration** avant de relancer l'analyse — pas de message
d'erreur à déchiffrer, on est guidé vers le choix des dossiers.

- **Tout ce qui vous appartient reste sur la clé** : le code, `config.yaml`,
  le tableau `tableau-backups.html` et le journal `backup-monitor.log`. La
  lettre de lecteur n'a aucune importance.
- **L'environnement Python est créé par poste** (dans
  `%LOCALAPPDATA%\backup-monitor\venv`, automatiquement à la première
  utilisation) — un environnement Python n'est pas portable d'un poste à
  l'autre. Prérequis unique sur le poste : Python 3.10+ installé.
- **Postes sans internet** : lancer une fois `preparer-cle.bat` sur un poste
  connecté — il télécharge les paquets Python dans `wheels\` sur la clé, et
  `lancer.bat` s'en servira hors-ligne. (Préparer avec la même version
  majeure de Python que les postes cibles.)
- La **tâche planifiée** (`windows\installer-tache.ps1`) est réservée aux
  installations fixes : elle échouerait dès que la clé est absente. Depuis la
  clé, utiliser `lancer.bat` ponctuellement ou `lancer.bat run --watch 300`
  pendant une intervention.
- Comme la clé peut se perdre : le mode `outlook` n'y stocke **aucun mot de
  passe** ; `config.yaml` ne contient que des chemins de dossiers et des noms
  de clients. Si cette information est sensible pour vous, chiffrez la clé
  (BitLocker To Go).

## Temps réel

- **Tâche planifiée (recommandé)** :
  `powershell -ExecutionPolicy Bypass -File windows\installer-tache.ps1`
  → régénère le tableau toutes les 5 min tant que la session est ouverte
  (nécessaire pour lire Outlook) ; la page ouverte se recharge d'elle-même
  (`report.refresh_seconds`).
- **Mode continu ponctuel** :
  `venv\Scripts\python -m backup_monitor run --watch 300`

## Recherche par mots-clés et options pratiques

**Ressortir les courriels contenant un mot** — deux façons :

- **Commande `find`** : `lancer.bat find VSS` (ou
  `python -m backup_monitor find VSS`) liste tous les courriels de la fenêtre
  d'analyse contenant le mot — dans le **sujet, le corps, les pièces jointes
  analysées, le dossier, le client ou l'expéditeur** — avec la date, le
  dossier et un extrait autour du mot. Plusieurs mots = tous requis
  (`find VSS "Comptable Plus"` cible un mot chez un client) ; insensible à la
  casse. Combiner avec `--days 60` pour chercher plus loin que la fenêtre
  courante.
- **Recherche du tableau** : le champ de recherche du tableau HTML couvre
  maintenant aussi l'**extrait du contenu** de chaque courriel (500 premiers
  caractères) et la note des pièces jointes — taper « VSS » y ressort donc
  aussi les courriels qui n'ont le mot que dans leur corps.

Les deux recherches sont **insensibles à la casse et aux accents**
(« echec » trouve « Échec ») et acceptent **plusieurs mots — tous requis**
(« vss comptable » cible le mot chez un client).

**Options de ligne de commande** (utilisables aussi via `lancer.bat`, qui
transmet les arguments) :

| Option | Effet |
|---|---|
| `--days N` | Fenêtre d'analyse ponctuelle (jours) pour CETTE exécution, sans modifier `config.yaml` — et `--days 0` = **tout le dossier, sans limite de date** |
| `--open` | Ouvre le tableau dans le navigateur après l'analyse (`run`) |
| `--no-cache` | Ignore le cache de collecte et relit tout depuis Outlook (le cache est reconstruit) — utile si un contenu semble périmé |
| `--fail-on-warning` | Avec `--fail-on-error` : les avertissements comptent aussi comme un problème (code 2) |

Le **titre du tableau** est personnalisable par site ou par entreprise :
`report.title` dans `config.yaml` (en-tête et onglet du navigateur).

## Performance de collecte (cache local, mode Outlook)

Le principal poste de coût d'un cycle est la lecture des corps de courriels
via COM : chaque corps force Outlook à charger l'élément complet, et la boîte
en contient vite plus d'un millier dans la fenêtre d'analyse. L'outil garde
donc un **cache par poste** du contenu déjà lu : au cycle suivant, un courriel
déjà vu ne coûte que trois propriétés légères — seuls les **nouveaux**
courriels sont lus en entier.

- Le fichier (`cache-courriels-*.json`) vit dans le **profil du poste**
  (`%LOCALAPPDATA%\backup-monitor` sous Windows, `~/.cache/backup-monitor`
  ailleurs) — **jamais sur la clé USB**, qui continue de ne porter aucun
  contenu de courriel.
- Il contient sujet/expéditeur/corps des courriels de la fenêtre d'analyse :
  si même cela est trop sensible, `cache.enabled: false` dans `config.yaml`
  (le fichier existant est alors supprimé au prochain lancement). Le
  supprimer à la main est toujours sans risque : il se reconstruit.
- Un courriel supprimé ou déplacé disparaît du cache au cycle suivant ;
  changer la section `attachments` vide le cache (le texte extrait en
  dépend) ; recalibrer `parsers` n'exige **pas** de le vider — le classement
  se refait à chaque exécution sur le contenu brut.
- L'analyse elle-même est optimisée pour les grosses boîtes : les motifs de
  détection sont **compilés une seule fois par exécution** (plus de
  recompilation à chaque courriel) et la préparation des extraits est bornée
  même sur de très gros corps. Le poste de coût principal d'un cycle reste
  la lecture Outlook, couverte par le cache ci-dessus.

## Pièces jointes (optionnel, désactivé par défaut)

Les produits joignent souvent leur rapport en `.txt`/`.log`, `.html` ou
`.pdf`. L'outil peut les analyser **sans jamais les exécuter, les ouvrir ni
les rendre** — jamais écrits sur disque. Quatre verrous, tous obligatoires
(`attachments` dans `config.yaml`) :

1. `enabled: true` — fonction explicitement activée ;
2. `allowed_senders` — seuls les expéditeurs de confiance (adresse exacte ou
   `"@domaine.local"`) sont traités ; tout autre expéditeur est ignoré et
   signalé dans le panneau de détail du tableau ;
3. `allowed_extensions` — texte, HTML et PDF ; jamais d'exécutable,
   d'archive ni de document Office ;
4. inspection du contenu — taille plafonnée (`max_kb`), rejet de tout binaire
   renommé en `.txt`/`.html` (signatures MZ, PK, octets nuls…), et un `.pdf`
   doit réellement commencer par `%PDF`.

Traitement par type, toujours sans exécution :

- **texte** : décodé en mémoire, parcouru par regex ;
- **HTML** : *jamais rendu dans un navigateur* — scripts et balises supprimés,
  seul le texte restant est analysé ;
- **PDF** : *jamais ouvert dans un lecteur PDF* (là où vivent les exploits) —
  texte extrait par `pypdf` (Python pur, sans moteur de rendu ni JavaScript)
  dans un **sous-processus à délai maximal (20 s)** : un PDF piégé qui ferait
  boucler ou planter l'analyseur est simplement abandonné et marqué
  « ignoré », sans affecter l'outil.

Le panneau de détail (▸) indique pour chaque courriel quelles pièces jointes
ont été analysées ou ignorées, et pourquoi. Tout contenu affiché dans le
tableau est échappé (protection contre l'injection HTML par un courriel piégé).

## Calibrage des motifs de détection

Les expressions régulières de `config.yaml` (section `parsers`) déterminent le
classement erreur/avertissement/succès et l'extraction machine/tâche. Les
valeurs par défaut couvrent les formats usuels de Macrium et Retrospect
(anglais et français), **mais doivent être validées avec de vrais courriels** :
après la première exécution, vérifier dans le tableau que les statuts sont
justes et qu'aucun courriel ne reste « Inconnu ». Ordre d'évaluation :
erreur > avertissement > succès.

## Suivi par client

La section `clients` de `config.yaml` lie les postes/serveurs à un client :

```yaml
clients:
  - name: "Clinique Vertika"
    machines: ["VERTIKA-.*", "SRV-VERT"]   # regex, insensibles à la casse
```

Les motifs sont testés sur la machine extraite, le sujet et le nom de tâche de
chaque courriel.

**Nomenclature Retrospect « ProActive »** : les sujets de la forme
`ProActive - Remote - <Compagnie> - N erreurs` sont reconnus nativement —
le **client** est le nom de compagnie extrait du sujet (traits d'union
conservés, préfixes RE:/TR: tolérés) et le **compte d'erreurs donne le
statut** (`0 erreurs` = succès, `1` et plus = erreur). Aucun réglage requis ;
priorité d'attribution du client : dossier (`client_folders`) > sujet >
section `clients`. Ajustable au besoin via `parsers.retrospect.extract.client`. Le tableau affiche alors : une **vue par client** (pire état
+ comptes d'erreurs/manquants/avertissements/succès, triée du client le plus
en difficulté au plus sain, cliquable pour filtrer), une **colonne Client**
dans les deux tableaux, et un **filtre par client**. Les tâches attendues
(`expected_jobs`) acceptent un champ `client:` explicite ; sinon le client est
déduit du dernier courriel correspondant.

### Un client par sous-dossier (`client_folders`)

Si votre boîte range les alertes par client — arborescence
`Sauvegardes/<Client>` — indiquez simplement le(s) dossier(s) **parent** :

```yaml
client_folders:
  - "Boîte de réception/Sauvegardes"
```

L'outil explore alors chaque sous-dossier : le **client = nom du sous-dossier**
(repris tel quel, sans regex à maintenir) et le **produit est détecté
automatiquement** dans le contenu — un dossier peut donc mélanger plusieurs
systèmes. **Ajouter un client revient à créer un sous-dossier** dans Outlook,
sans toucher à `config.yaml`. L'assistant `setup` propose ce mode (« un dossier
parent dont chaque sous-dossier est un client »). Se combine au besoin avec les
dossiers par produit (`folders`) et avec la section `clients` (qui ne
s'applique qu'aux courriels sans client déjà déterminé par le dossier).

Au-delà de Macrium et Retrospect, ce mode reconnaît automatiquement trois
autres systèmes fréquemment reçus dans les mêmes boîtes :

| Produit | Détecté grâce à | État |
|---|---|---|
| **SQL Server Agent** | `SQL Server Job System` (modèle standard Microsoft) | Succès confirmé sur de vrais courriels ; échec déduit du modèle symétrique (`[The job failed.]`) |
| **Proxmox Backup Server** | `vzdump`, `Garbage Collect Datastore`, `Pruning datastore`, `Sync remote … datastore` | Succès confirmé ; échec déduit (`backup failed`, `TASK ERROR`) |
| **Script personnalisé** | Sujet à convention `[Success]` / `[Failed]` / `[Warning]` | Succès confirmé ; échec/avertissement déduits de la même convention |

Les motifs de **succès** ci-dessus ont été validés avec de vrais courriels ; les
motifs d'**échec/avertissement** sont déduits du modèle standard de chaque
système (pas encore vus en pratique) — à confirmer avec `diagnose` dès qu'un
vrai cas d'échec se présente, et à ajuster via `parsers.sqlagent` /
`parsers.pbs` / `parsers.script` dans `config.yaml` si la formulation diffère.
N'importe quel autre système inconnu reste classé par mots-clés génériques
(`successful`, `succeeded`, `job failed`…) même sans reconnaissance de produit
dédiée, avant de retomber en « Inconnu ».

## Détection des backups manquants

La section `expected_jobs` déclare chaque tâche attendue avec sa fréquence
(`every_hours`) et une tolérance (`grace_hours`). Si aucun courriel
correspondant (`match`, regex sur sujet + machine + tâche) n'est reçu dans la
fenêtre, la tâche passe à « Manquant » — un backup qui n'envoie plus rien est
aussi grave qu'un backup en erreur.

Pour ne pas écrire ce bloc à la main : `lancer.bat suggest-jobs` (ou
`python -m backup_monitor suggest-jobs`) regroupe les courriels observés par
machine/tâche, estime la fréquence d'envoi et imprime un bloc `expected_jobs`
prêt à coller dans `config.yaml` — il reste à ajuster noms et tolérances.

## Historique et tendances

Le tableau ne montre que la fenêtre d'analyse (`days_back`) : sans mémoire,
impossible de voir qu'une tâche échoue une nuit sur trois ou qu'un client se
dégrade depuis une semaine. Chaque exécution de `run` mémorise donc le **pire
état de chaque jour** par tâche suivie dans `historique.json` (fichier local,
à côté de `config.yaml`). Le tableau affiche alors une section **Historique** :
une bande des derniers jours par tâche (`history.show_days`, 14 par défaut),
le nombre de jours en échec et le **taux de réussite sur 30 jours** — les
tâches en difficulté remontent en premier.

- Avec `expected_jobs` : chaque courriel correspondant compte sur **son** jour
  de réception — le premier passage remplit donc l'historique sur toute la
  fenêtre d'analyse — et l'état courant (y compris « Manquant », qui n'a pas
  de courriel) compte sur le jour de l'exécution.
- Sans `expected_jobs` : chaque couple machine/tâche observé est suivi de la
  même façon.
- Rétention : `history.keep_days` (90 jours par défaut) ; désactivable avec
  `history.enabled: false`. Seule la commande `run` écrit l'historique —
  `diagnose` et `test` n'y touchent pas.

## Notifications (optionnel, désactivé par défaut)

Par défaut l'outil n'alerte pas : il faut regarder le tableau. La section
`notifications` de `config.yaml` active des alertes **sur transition d'état
uniquement** (une tâche qui passe en erreur/manquant, ou qui se rétablit) —
jamais une répétition à chaque cycle de 5 minutes. L'état du dernier passage
est mémorisé dans `dernier-etat.json`.

- `toast` : notification Windows native, aucune dépendance, rien ne sort du
  poste.
- `webhook` : POST HTTP vers `webhook_url` (`text` pour ntfy, `json` pour
  Teams/Slack). C'est le **seul** trafic réseau sortant possible de l'outil,
  activé explicitement et vers une URL que vous choisissez.

## Codes de sortie (supervision RMM / Planificateur)

Avec `run --fail-on-error` (utilisé par la tâche planifiée et l'unité
systemd) : `0` = tout va bien, `1` = panne de l'outil, `2` = backups en
erreur ou manquants, `3` = pas encore configuré, `4` = collecte partielle
(dossiers ou courriels illisibles). Le « Dernier résultat » de la tâche
planifiée devient donc directement exploitable par un RMM. Ajouter
`--fail-on-unknown` pour que les courriels non reconnus comptent aussi, et
`--fail-on-warning` pour que les avertissements comptent également.

## Modes de repli : EWS / IMAP (accès serveur direct, sans Outlook)

Pour exécuter l'outil ailleurs que sur le poste (ex. un serveur Linux) :
`pip install -r requirements-exchange.txt`, puis dans `config.yaml` :
`method: ews` (ou `imap`), `exchange.email`, `exchange.username`, `ews_url`.
Mot de passe : `python -m backup_monitor set-password` (trousseau système).
Sous Linux, `install.sh` et `systemd/installer-timer.sh` couvrent ce scénario.

## Ne rien rater : profondeur d'analyse et affichage

Quatre garanties pour qu'un problème ne passe jamais sous le radar :

- **Sous-dossiers inclus** : chaque dossier choisi (`folders.*`) est lu avec
  **tous ses sous-dossiers** (`analysis.include_subfolders`, actif par
  défaut) — un tri par machine ou par année ne cache rien. (Le mode
  `client_folders` était déjà récursif.)
- **Fenêtre au choix, jusqu'à l'illimité** : `analysis.days_back: 0` (ou
  ponctuellement `lancer.bat run --days 0`) analyse **tout le dossier, sans
  limite de date**. Premier passage long sur une grosse boîte ; le cache
  absorbe les suivants.
- **Lecture robuste au tri** : la collecte Outlook ne s'arrête plus au
  « premier courriel trop vieux » — cette coupe dépendait du tri COM, qui
  peut échouer silencieusement et faire paraître un dossier **vide sans
  aucune erreur**. Le filtrage par date se fait côté MAPI (`Restrict`) avec
  repli sur un parcours complet du dossier.
- **Les problèmes ne sont jamais tronqués à l'affichage** : une erreur ou un
  avertissement plus ancien que les `report.max_rows` courriels les plus
  récents reste listé ; et sans `expected_jobs`, les tuiles et la vue par
  client reflètent le **dernier état connu de chaque tâche observée** — un
  échec vieux de 3 jours sans courriel plus récent reste compté (l'ancienne
  base « dernières 24 h » pouvait le masquer).

## Robustesse et journal

- **Collecte tolérante aux pannes** : un dossier illisible (renommé, déplacé,
  boîte momentanément indisponible) n'interrompt pas l'analyse des autres ;
  l'erreur apparaît dans un bandeau « Collecte partielle » en tête du tableau
  et dans la sortie console.
- **Journal** : chaque exécution écrit une ligne dans `backup-monitor.log`
  (résumé ou erreur complète) — c'est le premier endroit à regarder si la
  tâche planifiée, qui tourne sans console, semble ne plus produire de
  tableau. Le fichier est tronqué automatiquement au-delà de 1 Mo.
- **`selftest`** : autotest hors-ligne rejouable à tout moment (après une mise
  à jour de Python, par exemple).
- **Phase de rodage** : tant que `autotest.on_each_run` vaut `true` (le
  défaut), l'autotest complet tourne à **chaque** utilisation de l'outil et
  journalise son résultat dans `autotest.log` (une ligne par usage, détail
  des échecs). Une régression se voit donc dès la commande suivante. La même
  batterie tourne en CI GitHub Actions à chaque poussée. À la version
  finale, passer `on_each_run` à `false`.

## Envoyer un diagnostic (rapport-diagnostic.bat)

Pour faire ajuster l'outil (motifs mal calibrés, courriels non reconnus…)
sans accès au poste : **double-cliquer `rapport-diagnostic.bat`** (ou
`lancer.bat rapport`). Il génère `rapport-diagnostic.txt` — environnement,
résultat de l'autotest, configuration surveillée, comptes par produit/état,
taux d'extraction, exemples par produit, cas d'échec des systèmes aux motifs
encore déduits (SQL Server Agent, Proxmox, scripts), liste des courriels
**non reconnus** avec extraits, état des tâches attendues et queue des
journaux — puis l'ouvre dans le Bloc-notes.

**Aucun envoi automatique** : le fichier reste local. Il contient des noms
de clients/machines et des extraits de courriels de sauvegarde — **le relire
avant de le transmettre** (il n'est jamais versionné : `.gitignore`).

## Mise à jour depuis le ZIP du dépôt

Si votre méthode de mise à jour est « supprimer le dossier puis re-extraire
le ZIP », **préservez d'abord ces fichiers locaux** (ils sont à la racine du
dossier et ne sont pas dans le ZIP) :

| Fichier | Rôle | Si perdu |
|---|---|---|
| `config.yaml` | Toute votre configuration | À refaire avec `setup` + réglages manuels |
| `historique.json` | Historique quotidien des tâches | La section Historique repart de la fenêtre d'analyse |
| `dernier-etat.json` | Mémoire des notifications | Risque d'une salve de notifications au run suivant |

Le plus simple : copier ces trois fichiers ailleurs, re-extraire le ZIP,
puis les remettre. (`tableau-backups.html` et les journaux se régénèrent
seuls ; le cache de collecte vit dans le profil du poste et survit à la
mise à jour.) Après chaque mise à jour, l'autotest tourne à la première
commande — une régression se verrait immédiatement.

## Dépannage

| Symptôme | Piste |
|---|---|
| `aucun dossier à surveiller n'est défini` | Configuration pas encore faite (pas une panne) : lancer `lancer.bat setup` — au double-clic, `lancer.bat` et `install.bat` enchaînent désormais l'assistant automatiquement |
| `No time zone found` / `No module named 'tzdata'` | Base des fuseaux horaires absente (fréquent sous Windows) : relancer `install.bat` (il installe/répare `tzdata` automatiquement) ou `venv\Scripts\python -m pip install tzdata` |
| Outlook se fige pendant `setup` | `setup` fait maintenant choisir la boîte d'abord et ne scanne que celle-ci (sans compter les éléments). Alternative sans scan : renseigner directement `folders:` dans `config.yaml` (chemins séparés par `/`, ex. `Boîte de réception/Backups/Macrium`) puis lancer `run` |
| `Dossier Outlook introuvable` | `python -m backup_monitor folders` pour les chemins exacts (attention aux noms français : « Boîte de réception ») |
| La boîte n'est pas celle par défaut | Renseigner `outlook.store` avec le nom exact du magasin affiché dans Outlook |
| Fenêtre de sécurité Outlook au lancement | Normal si un antivirus restreint l'accès programmatique ; l'outil ne fait que lire — autoriser l'accès |
| La tâche planifiée ne tourne pas | Elle exige une session ouverte (Outlook COM) : vérifier dans le Planificateur de tâches |
| Rien ne s'affiche pour un produit | Vérifier le chemin du dossier et la fenêtre `analysis.days_back` |
