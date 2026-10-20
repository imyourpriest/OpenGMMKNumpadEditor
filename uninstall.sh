#!/bin/sh
# Remove unchanged managed application files; preserve local user files.
set -eu
gmmk_uninstall_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 -B "$gmmk_uninstall_source/installation.py" uninstall
