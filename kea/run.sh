#!/usr/bin/with-contenv bash
# shellcheck shell=bash
# `with-contenv` makes the container environment visible to this script (the s6 init of the
# Home Assistant base image otherwise hides it); this is the standard pattern for apps.
#
# Starts the Web UI. server.py generates the Kea configuration, starts/reloads kea-dhcp4 and
# kea-dhcp6, and stops them when it receives SIGTERM.
set -euo pipefail

# Kea 3.x only reads/writes lease files, pid files and lock files in fixed directories unless
# these variables say otherwise (names per the Kea 3.0 upgrade notes).
export KEA_DHCP_DATA_DIR=/config/kea
export KEA_PIDFILE_DIR=/run/kea
export KEA_LOCKFILE_DIR=/run/kea

mkdir -p /config/kea /run/kea /etc/kea
cd /app
exec /opt/venv/bin/python3 /app/server.py
