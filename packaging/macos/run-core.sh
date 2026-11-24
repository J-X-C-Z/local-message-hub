#!/bin/sh
# Foreground launch of the portable macOS core.
set -eu

fail() { printf '%s\n' "$1" >&2; exit 2; }

[ "$(/usr/bin/uname -s)" = Darwin ] || fail 'This bundle requires macOS.'
if [ "$#" -lt 3 ] || [ "$#" -gt 5 ]; then
    fail 'Usage: run-core.sh (snapshot|live) ARCHIVE GRANTS [--port PORT]'
fi
case "$1" in
    snapshot) archive_mode=--snapshot; [ -f "$2" ] || fail 'Expected a snapshot file.' ;;
    live) archive_mode=--live; [ -d "$2" ] || fail 'Expected a native archive directory.' ;;
    *) fail 'Only snapshot and live core modes are supported (no guarded, media or desktop supervisor mode).' ;;
esac
[ ! -L "$2" ] || fail 'Expected a non-symlink archive path.'
[ -f "$3" ] && [ ! -L "$3" ] || fail 'Expected a regular, private plugin grants file.'
if [ "$#" -gt 3 ]; then
    [ "$#" -eq 5 ] && [ "$4" = --port ] || fail 'Use --port PORT or omit both arguments.'
fi
port=${5-0}
case "$port" in
    ''|*[!0-9]*) fail 'Port must be a decimal number from 0 to 65535.' ;;
esac
[ "${#port}" -le 5 ] && [ "$port" -le 65535 ] || fail 'Port must be from 0 to 65535.'

case "$0" in
    */*) launcher_dir=${0%/*} ;;
    *) launcher_dir=. ;;
esac
case "$launcher_dir" in
    /*) ;;
    *) launcher_dir=./$launcher_dir ;;
esac
bundle_dir=$(CDPATH='' cd -P "$launcher_dir" && pwd -P)
exe=$bundle_dir/bin/local-message-hub-rs
[ -f "$exe" ] && [ -x "$exe" ] && [ ! -L "$exe" ] || fail 'Core executable is missing.'
exec "$exe" serve "$archive_mode" "$2" --grants "$3" --port "$port"
