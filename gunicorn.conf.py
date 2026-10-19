"""API process settings shared by local containers and Railway."""

import os

bind = "0.0.0.0:" + os.getenv("PORT", "8000")
worker_class = "gthread"
workers = 1
threads = 8
timeout = 30
preload_app = False
accesslog = "-"
# PATH_INFO excludes OAuth callback queries and request headers.
access_log_format = '"%(m)s %(U)s %(H)s" %(s)s %(b)s %(L)s'
errorlog = "-"
# The platform manages the process; nothing uses gunicornc. Its default socket
# lives under $HOME, which the container's nobody user cannot create.
control_socket_disable = True
