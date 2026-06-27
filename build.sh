#!/usr/bin/env bash
# Script de build pour Render.com (backend Django)
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
# Crée les comptes de démo (admin/coop/agent) si absents
python manage.py seed_data || true
