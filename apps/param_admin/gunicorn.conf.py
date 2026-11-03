"""Gunicorn settings, read from the app's folder.

Databricks Apps says which port to listen on through DATABRICKS_APP_PORT. Locally and in Docker
the port is PORT, or 8050.
"""

import os

bind = f"0.0.0.0:{os.environ.get('DATABRICKS_APP_PORT') or os.environ.get('PORT') or '8050'}"

# One worker with threads: the idle tracker behind /idle lives in the process, so every
# request has to reach the same one.
workers = 1
threads = 4
