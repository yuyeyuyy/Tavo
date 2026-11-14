#!/bin/sh
set -eu
umask 077
case "${1:-api}" in
  init) exec python -m app.cli init ;;
  api) exec python -m app.cli serve --host 0.0.0.0 --container-local --no-worker ;;
  worker) exec python -m app.cli worker ;;
  *) exec "$@" ;;
esac
