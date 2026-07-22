# backup-monitor — Surveillance des courriels de backup Macrium & Retrospect

Outil local qui lit **en lecture seule** les courriels de résultats de
sauvegarde (Macrium Reflect et Retrospect), les classe (succès / avertissement
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
   - lance l'**assistant** qui **scanne les boîtes et dossiers visibles dans
     Outlook** (lecture seule), suggère ceux dont le nom contient
     « macrium »/« retrospect », fait choisir la boîte puis les dossiers à
     surveiller pour chaque produit, et enregistre le tout dans `config.yaml` ;
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
chaque courriel. Le tableau affiche alors : une **vue par client** (pire état
+ comptes d'erreurs/manquants/avertissements/succès, triée du client le plus
en difficulté au plus sain, cliquable pour filtrer), une **colonne Client**
dans les deux tableaux, et un **filtre par client**. Les tâches attendues
(`expected_jobs`) acceptent un champ `client:` explicite ; sinon le client est
déduit du dernier courriel correspondant.

## Détection des backups manquants

La section `expected_jobs` déclare chaque tâche attendue avec sa fréquence
(`every_hours`) et une tolérance (`grace_hours`). Si aucun courriel
correspondant (`match`, regex sur sujet + machine + tâche) n'est reçu dans la
fenêtre, la tâche passe à « Manquant » — un backup qui n'envoie plus rien est
aussi grave qu'un backup en erreur.

## Modes de repli : EWS / IMAP (accès serveur direct, sans Outlook)

Pour exécuter l'outil ailleurs que sur le poste (ex. un serveur Linux) :
`pip install -r requirements-exchange.txt`, puis dans `config.yaml` :
`method: ews` (ou `imap`), `exchange.email`, `exchange.username`, `ews_url`.
Mot de passe : `python -m backup_monitor set-password` (trousseau système).
Sous Linux, `install.sh` et `systemd/installer-timer.sh` couvrent ce scénario.

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

## Dépannage

| Symptôme | Piste |
|---|---|
| `aucun dossier à surveiller n'est défini` | Configuration pas encore faite (pas une panne) : lancer `lancer.bat setup` — au double-clic, `lancer.bat` et `install.bat` enchaînent désormais l'assistant automatiquement |
| `Dossier Outlook introuvable` | `python -m backup_monitor folders` pour les chemins exacts (attention aux noms français : « Boîte de réception ») |
| La boîte n'est pas celle par défaut | Renseigner `outlook.store` avec le nom exact du magasin affiché dans Outlook |
| Fenêtre de sécurité Outlook au lancement | Normal si un antivirus restreint l'accès programmatique ; l'outil ne fait que lire — autoriser l'accès |
| La tâche planifiée ne tourne pas | Elle exige une session ouverte (Outlook COM) : vérifier dans le Planificateur de tâches |
| Rien ne s'affiche pour un produit | Vérifier le chemin du dossier et la fenêtre `analysis.days_back` |
