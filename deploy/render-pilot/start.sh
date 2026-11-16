#!/bin/sh
set -eu
umask 077
# Only the mount's top directory is assigned; never recursively rewrite state.
python /opt/pilot/pilot.py check-mount
if [ "$(id -u)" = "0" ]; then
  chmod 700 /state
  chown 10001:10001 /state
  exec setpriv --reuid=10001 --regid=10001 --init-groups python /opt/pilot/pilot.py serve
fi
exec python /opt/pilot/pilot.py serve
