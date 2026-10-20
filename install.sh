#!/bin/sh
# Install for the current user; USB permissions are a separate one-time step.
set -eu
gmmk_install_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 -B "$gmmk_install_source/installation.py" install --source "$gmmk_install_source"
