#!/bin/sh
# macOS-only distribution layout; stage a prebuilt executable, never install it.
set -eu
umask 022
fail() { printf '%s\n' "$1" >&2; exit 2; }

[ "$(/usr/bin/uname -s)" = Darwin ] || fail 'Stage this bundle on macOS with Command Line Tools.'
[ "$#" -eq 3 ] || fail 'Usage: stage-core.sh (arm64|x86_64) BINARY NEW_DESTINATION_FOLDER'
case "$1" in
    arm64|x86_64) expected=$1 ;;
    *) fail 'Choose arm64 or x86_64; use separate bundles, not a universal binary.' ;;
esac
case "$2" in /*) binary=$2 ;; *) binary=./$2 ;; esac
case "$3" in /*) destination=$3 ;; *) destination=./$3 ;; esac
[ -f "$binary" ] && [ -x "$binary" ] && [ ! -L "$binary" ] || fail 'Expected a regular release executable.'
archs=$(/usr/bin/lipo -archs "$binary")
[ "$archs" = "$expected" ] || fail 'Mach-O architecture does not match the selected bundle.'
description=$(/usr/bin/file -b "$binary")
case "$description" in
    'Mach-O 64-bit executable '*) ;;
    *) fail 'Expected a thin Mach-O executable, not a library or object.' ;;
esac

case "$0" in
    */*) script_dir=${0%/*} ;;
    *) script_dir=. ;;
esac
case "$script_dir" in /*) ;; *) script_dir=./$script_dir ;; esac
script_dir=$(CDPATH='' cd -P "$script_dir" && pwd -P)

# An existing destination, including a symlink, must not be replaced.
mkdir "$destination" || fail 'Use a new folder under an existing, trusted local parent directory.'
trap 'printf "%s\n" "Staging incomplete; discard this folder before distribution." >&2' EXIT
mkdir "$destination/bin"
# cp preserves the input bytes; no signing or stripping, no data/config bundled.
/bin/cp "$binary" "$destination/bin/local-message-hub-rs"
/bin/cp "$script_dir/run-core.sh" "$destination/run-core.sh"
/bin/cp "$script_dir/../README.md" "$destination/README.md"
chmod 0755 "$destination/bin/local-message-hub-rs" "$destination/run-core.sh"
chmod 0644 "$destination/README.md"
trap - EXIT
