#!/usr/bin/env bash
# Script de build pour Render.com (backend Django)
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate

# Comptes de démonstration (admin/coop/agent) : UNIQUEMENT si SEED_DEMO_DATA=True.
# Sécurité : aucun mot de passe n'est plus réinitialisé automatiquement à chaque déploiement
# (l'ancien script remettait admin/admin123, affiché publiquement : accès super admin pour tous).
if [ "${SEED_DEMO_DATA:-False}" = "True" ]; then
  python manage.py seed_data || true
fi
