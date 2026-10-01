# ZIP manifest audit — Agent Room 0.2.1 pilot

A standalone Python standard-library verifier for Agent Room distribution archives.
Built by the default native CLAUDE_01 + CODEX_EXPERT room in a fresh project.

## Contract

- Implement `verify_archive(path)` in `verify_package.py`. It returns exactly
  `{"version": <manifest version>, "files_checked": <number of payload files>}`.
- Raise `VerificationError` for invalid archives, unreadable inputs, invalid UTF-8/JSON,
  invalid manifests and hash mismatches. Do not expose an unhandled traceback for these inputs.
- The archive has exactly one `agent-room/PACKAGE-MANIFEST.json` and payload files
  under `agent-room/`. Each entry is a regular file, not a directory or symlink.
  Reject duplicate ZIP names. Paths must be canonical relative POSIX paths:
  no absolute path, backslash, empty segment, `.` or `..` segment.
- Manifest JSON must reject duplicate object keys. Its exact top-level keys are
  `version` (nonempty string) and `files_sha256` (object). Each payload path maps to
  a lowercase 64-character SHA-256 string. The manifest cannot list itself.
  The payload set must equal the manifest set; reject missing and extra entries.
- Hash raw file bytes without extracting or executing archive contents. Accept empty payload
  sets. Return the manifest version; this is integrity checking, not publisher authentication.
- Bound input to 256 ZIP entries, 16 MiB declared uncompressed size per entry,
  and 64 MiB total declared uncompressed size; reject oversized inputs before reading payloads.
- CLI: `python3 verify_package.py PATH.zip`. Success exits 0 with exactly one JSON object
  on stdout: `{"ok": true, "version": ..., "files_checked": ...}`. Validation/I/O errors
  exit 2, leave stdout empty, and write one line starting `error:` to stderr.
- Use module-scope imports, Python 3.11+, no dependencies, no network, no input mutation.
  Tests that need files create them under this project and clean up their own fixtures.

## Run

```sh
python3 verify_package.py fixtures/agent-room-0.2.1.zip
python3 -m unittest discover -s tests -v
```

The unchanged reference ZIP contains 28 payload files plus its manifest. SHA-256:
`97be40a793c034810408eb44fb37a261e24f766dd4fe792cb98c362feffd7288`.

The pilot implements only `verify_package.py` and `tests/test_verify_package.py`.
Room state and runtime logs remain local. No publication or production migration is part of this pilot.
