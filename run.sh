#!/usr/bin/env bash
# MXR CLIP ENGINE launcher for macOS and Linux.
cd "$(dirname "$0")" || exit 1
exec python3 run.py "$@"
