# Lu automatiquement par gunicorn (Render : « gunicorn config.wsgi:application »).
import os
import shutil

# Imports de milliers de producteurs / polygones : la base Neon est distante, on laisse du temps.
timeout = 120
workers = 2

# Supervision : compteurs Prometheus partagés entre les processus (lus par Grafana)
PROM_DIR = os.environ.setdefault('PROMETHEUS_MULTIPROC_DIR', '/tmp/geocollect-prometheus')


def on_starting(server):
    shutil.rmtree(PROM_DIR, ignore_errors=True)
    os.makedirs(PROM_DIR, exist_ok=True)


def child_exit(server, worker):
    from prometheus_client import multiprocess
    multiprocess.mark_process_dead(worker.pid)
