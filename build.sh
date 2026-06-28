#!/usr/bin/env bash
# Script de build pour Render.com (backend Django)
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
# Crée les comptes de démo (admin/coop/agent) si absents
python manage.py seed_data || true

# Garantit l'accès admin (admin/admin123) même si le seed a échoué
python manage.py shell -c "
from apps.accounts.models import User
u, _ = User.objects.get_or_create(username='admin', defaults={'full_name':'Super Administrateur','role':'super_admin','is_staff':True,'is_superuser':True})
u.role='super_admin'; u.is_active=True; u.is_staff=True; u.is_superuser=True
u.set_password('admin123'); u.save()
print('✓ admin réinitialisé')
" || true
