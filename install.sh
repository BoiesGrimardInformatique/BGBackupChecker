#!/usr/bin/env bash
# Installation de backup-monitor : environnement virtuel + dépendances + config.
set -euo pipefail
cd "$(dirname "$0")"

echo "== Création de l'environnement virtuel =="
python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -r requirements-exchange.txt -q

if [[ ! -f config.yaml ]]; then
  cp config.example.yaml config.yaml
  chmod 600 config.yaml
  echo "== config.yaml créé (permissions 600) — À ADAPTER avant la première exécution =="
else
  echo "== config.yaml existant conservé =="
fi

cat <<'EOF'

Prochaines étapes :
  1. Éditer config.yaml (serveur, compte, dossiers Outlook, tâches attendues)
  2. Enregistrer le mot de passe dans le trousseau :
       ./venv/bin/python -m backup_monitor set-password
  3. Tester la connexion :
       ./venv/bin/python -m backup_monitor test
  4. Lister les dossiers pour vérifier les chemins :
       ./venv/bin/python -m backup_monitor folders
  5. Générer le tableau :
       ./venv/bin/python -m backup_monitor run
  6. (Optionnel) Activer l'actualisation automatique :
       ./systemd/installer-timer.sh
EOF
