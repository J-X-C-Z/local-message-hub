The database and cached-media readers in vendor/ are adapted from:
https://github.com/J-X-C-Z/local-message-hub
Pinned commit: b30a90da7a67fb0b30d7e34886f312843d20ba11

Upstream original: fanyuantaier/wechatauto-replica 1.2.6,
commit d6e61bea7f9c75906faa8b71d37ef3572fba2454 (Apache-2.0).
Original license is retained in vendor/LICENSE.

Local modifications are in reader.py: Windows DPAPI key storage, fixed account
selection, inbox event mapping, bounded local media copies and stdin transport.
No send module, UI automation or remote media download is included.

Python, cryptography, zstandard and their dependencies are bundled by PyInstaller.
See licenses/ in the accompanying runtime directory.
