# Supervision GeoCollect EUDR — Grafana

Ce que l'API Django expose (application `apps/monitoring`) :

| Adresse | Accès | Contenu |
|---|---|---|
| `/api/v1/monitoring/metrics/` | jeton `MONITORING_TOKEN` (Bearer, ou Basic avec le jeton en mot de passe). Sans jeton valide : 404 | mesures Prometheus |
| `/api/v1/monitoring/health/` | public | état de la base, version déployée, durée de fonctionnement (pour les sondes de disponibilité) |
| `/api/v1/monitoring/client-error/` | public | erreurs JavaScript de l'application web (seulement comptées) |

Mesures disponibles :
- **Technique** : requêtes par route et par code de réponse, latence (histogramme), exceptions 500, connexions réussies et échouées, latence de la base Neon, erreurs JavaScript.
- **Métier**, sans aucune donnée personnelle : clients par statut, coopératives, comptes par rôle, producteurs, parcelles, hectares, statut EUDR, parcelles et hectares par client, traitements et polygones traités par outil, rapports, connexions et utilisateurs actifs sur 24 h, abonnements qui expirent dans 7 jours.

Le dossier `grafana/` contient :
- 2 tableaux de bord : `dashboards/geocollect-tech.json` (santé technique) et `dashboards/geocollect-business.json` (activité métier). Ils sont générés par `python monitoring/build_dashboards.py`.
- 6 règles d'alerte : API indisponible, erreurs 5xx supérieures à 5 %, API lente, base lente, attaque par mot de passe, abonnement bientôt expiré. Elles se trouvent dans `provisioning/alerting/geocollect-alerts.yml`.

## Production : Grafana Cloud (gratuit)

1. **Jeton.** Générez un jeton long et aléatoire, par exemple avec `python -c "import secrets; print(secrets.token_urlsafe(40))"`. Sur **Render**, ajoutez la variable d'environnement `MONITORING_TOKEN=<jeton>`. Render redéploie le service.
2. **Compte.** Créez un compte sur https://grafana.com (offre *Free* : 10 000 séries, 14 jours d'historique, alertes incluses).
3. **Collecte.** Dans Grafana Cloud, ouvrez **Connections → Add new connection → Metrics Endpoint**, puis renseignez :
   - URL : `https://geocollect-backend.onrender.com/api/v1/monitoring/metrics/`
   - Authentification : **Bearer**, avec le jeton de l'étape 1
   - Nom du job : `geocollect-api` (les tableaux de bord et les alertes filtrent sur ce nom)
   - Intervalle : 1 minute

   La collecte toutes les minutes garde aussi le serveur Render gratuit éveillé : il n'y a plus d'attente de 50 s au premier chargement. Un seul service tourne 24 h/24 dans les 750 h gratuites par mois.
4. **Tableaux de bord.** Ouvrez **Dashboards → New → Import** et importez les deux fichiers JSON du dossier `grafana/dashboards/`. Choisissez la source Prometheus de votre compte, nommée `grafanacloud-…-prom`.
5. **Alertes.** Ouvrez **Alerting → Alert rules → New alert rule** et recopiez les requêtes de `provisioning/alerting/geocollect-alerts.yml`. Vous pouvez aussi l'importer avec `grafana-cli` ou l'API de provisioning. Pour le point de contact, utilisez votre e-mail, ou WhatsApp et Telegram par webhook.
6. **Disponibilité vue de l'extérieur** (facultatif). Ouvrez **Testing & synthetics → Synthetic Monitoring → Add check → HTTP**, avec l'URL `https://geocollect-backend.onrender.com/api/v1/monitoring/health/`, toutes les 1 à 5 min, depuis Paris ou Francfort.

## Essai local (Docker)

```bash
# 1. API avec gunicorn (2 processus, comme Render) et un jeton de test
cd geocollect-backend && source venv/bin/activate
MONITORING_TOKEN=tok-local gunicorn config.wsgi:application -c gunicorn.conf.py -b 0.0.0.0:8001

# 2. Prometheus + Grafana
cd monitoring && cp .env.example .env    # définir GRAFANA_ADMIN_PASSWORD
printf 'tok-local' > prometheus/token
docker compose up -d
```

Grafana est alors sur http://localhost:3001 (identifiant `admin`) et Prometheus sur http://localhost:9091. Les tableaux de bord et les alertes sont déjà chargés.

## Application web sur Vercel

Le site React peut être hébergé sur Vercel avec `geocollect-frontend/vercel.json`. Ce fichier reprend les en-têtes de sécurité de Netlify, le cache et les routes de l'application. L'API Django reste sur Render : ses traitements lourds (déforestation, imports de 10 000 polygones) dépassent les limites des fonctions Vercel. Sur Vercel, le site active aussi **Web Analytics** et **Speed Insights**, qui mesurent les temps de chargement réels des utilisateurs (onglets du projet Vercel).
