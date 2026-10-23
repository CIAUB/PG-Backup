# Security

Report vulnerabilities privately via GitHub Security Advisories on CIAUB/PG-Backup (not public issues).

## Release checklist (maintainer)
1. Bump `PGB_VERSION`, `COMPONENT_RAW_BASE` tag in `pg_backup.py` and `DEFAULT_TAG` in `install.sh` (a test enforces they match).
2. `python3 tools/build_manifest.py` (regenerates `pgb-manifest.json` and the embedded manifest hash).
3. `sha256sum pg_backup.py > pg_backup.py.sha256`, `sha256sum install.sh > install.sh.sha256` (a test fails if stale).
4. Run `python3 -m unittest discover -s tests`, commit, then **create the git tag `vX.Y.Z`** — installs and component downloads are served from that tag.
5. Publish the two hashes somewhere other than the repo too (release notes / channel) so users can use `install.sh --sha256 <hash>`.

## Design notes
- Panel listens on 127.0.0.1 only; Caddy terminates TLS; everything lives under a random secret path.
- Login: scrypt, per-user + per-IP exponential lockout, TOTP (secret encrypted at rest) with replay protection, single-use recovery codes, CSRF header, HttpOnly/SameSite=Strict cookies, logout revokes the session, password change invalidates all sessions.
- Secrets (bot tokens, SSH credentials, TOTP) are Fernet-encrypted with a 0600 key next to the database. Whoever is root on the box can read both; this protects against copying the DB alone.
- Panel code is downloaded on demand and each file is checked against a manifest whose hash is embedded in `pg_backup.py`.
- Known limits: SSH transfer trusts the host key on first use unless you set the fingerprint; database-engine conversion was tested against a real PostgreSQL but not against real Timescale/MySQL/MariaDB containers; the panel runs as root because it manages docker/systemd.
