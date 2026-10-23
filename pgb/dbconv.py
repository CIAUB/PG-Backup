"""PasarGuard database-engine conversion for PG-Backup archives.

Takes a backup zip made by PG-Backup and writes a NEW zip whose database dump,
docker-compose.yml and manifest are converted to another database engine.

    TimescaleDB  <->  PostgreSQL        (same SQL family, TimescaleDB objects removed/added)
    MariaDB      <->  MySQL             (same SQL family, collations / version comments fixed)

Hard rules:
  * The source zip is only read, never modified.
  * Nothing is restored, nothing on the running server is touched, no docker call is made.
  * Streaming: dumps are processed line by line, so multi-GB dumps use almost no RAM.
  * Standard library only.

Cross-family conversion (SQLite <-> PostgreSQL <-> MySQL) is intentionally NOT offered:
PasarGuard creates its own schema with alembic at start-up, and rewriting a schema by
guesswork would risk silent data loss.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from typing import Callable, Dict, Iterable, List, Optional, Tuple

PG_FAMILY = ("postgresql", "timescaledb")
MY_FAMILY = ("mysql", "mariadb")
SUPPORTED: Dict[str, Tuple[str, ...]] = {
    "timescaledb": ("postgresql",),
    "postgresql": ("timescaledb",),
    "mariadb": ("mysql",),
    "mysql": ("mariadb",),
}
MANIFEST = "db_dump/manifest.tsv"
SQL_RE = re.compile(r"^db_dump/db-\d{3}\.sql$")
MAX_ENTRY = 64 * 1024 ** 3          # sanity cap per archive entry (64 GiB)


class ConvertError(Exception):
    pass


class Report:
    def __init__(self, source_type: str, target_type: str):
        self.source_type, self.target_type = source_type, target_type
        self.out_path = ""
        self.notes: List[str] = []
        self.warnings: List[str] = []
        self.stats: Dict[str, int] = {}

    def bump(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n


# ───────────────────────────── archive helpers ─────────────────────────────
def _check_member(name: str) -> None:
    """Reject zip-slip style names even though we never extract to disk."""
    p = name.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/"):
        raise ConvertError(f"unsafe path inside archive: {name!r}")


def read_manifest_type(zf: zipfile.ZipFile) -> Optional[str]:
    try:
        text = zf.read(MANIFEST).decode("utf-8", "replace")
    except KeyError:
        return None
    for line in text.splitlines():
        if "db_type=" in line:
            for part in line.replace("\t", " ").split():
                if part.startswith("db_type="):
                    return part.split("=", 1)[1].strip()
    return None


def _detect_type(zf: zipfile.ZipFile) -> str:
    t = read_manifest_type(zf)
    if t:
        return t
    names = zf.namelist()
    if any(n.endswith((".sqlite3", ".sqlite")) for n in names if n.startswith("db_dump/")):
        return "sqlite"
    if "db_dump/globals.sql" in names:
        return "postgresql"
    if any(SQL_RE.match(n) for n in names):
        return "mysql"
    raise ConvertError("this does not look like a PG-Backup archive (no db_dump/manifest.tsv)")


def supported_targets(source_type: str) -> Tuple[str, ...]:
    return SUPPORTED.get(source_type, ())


def inspect(zip_path: str) -> Dict[str, object]:
    """Cheap look at an archive: engine type + which conversions are possible."""
    with zipfile.ZipFile(zip_path) as zf:
        t = _detect_type(zf)
    return {"type": t, "targets": list(supported_targets(t))}


# ───────────────────────────── PostgreSQL family ───────────────────────────
_DOLLAR = re.compile(r"\$[A-Za-z_]*\$")
_COPY = re.compile(r"^COPY\s+(\S+)\s*\((.*)\)\s+FROM\s+stdin;\s*$", re.S)
_INHERITS = re.compile(r"INHERITS\s*\(\s*([^)\s]+)\s*\)", re.I)
_CHUNK_TBL = re.compile(r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(_timescaledb_\w+\.\S+)", re.I)
_PG_VER = re.compile(r"Dumped from database version (\d+)")


_STR = re.compile(r"'(?:[^']|'')*'")
_LINE_CMT = re.compile(r"--[^\n]*")
_BODY = re.compile(r"(\$[A-Za-z_]*\$)(.*?)\1", re.S)
_TS_BODY = re.compile(r"timescaledb|time_bucket|hypertable", re.I)


def _mentions_timescale(stmt: str) -> bool:
    """True when the statement's SQL *structure* refers to TimescaleDB.

    String literals and comments are ignored (user data / notes may legitimately say
    "timescaledb"); dollar-quoted function bodies are checked separately for real calls.
    """
    if "_timescaledb" in _LINE_CMT.sub("", stmt):
        return True                                  # internal schemas, even inside setval('…') strings
    bodies = [m.group(2) for m in _BODY.finditer(stmt)]
    code = _BODY.sub(" ", stmt)
    code = _STR.sub("''", _LINE_CMT.sub("", code))
    if "timescaledb" in code.lower():
        return True
    return any(_TS_BODY.search(_LINE_CMT.sub("", b)) for b in bodies)


class _StmtScanner:
    """Group pg_dump text into statements while tracking quotes/dollar-quotes."""

    def __init__(self) -> None:
        self.buf: List[str] = []
        self.in_dollar: Optional[str] = None
        self.in_quote = False

    def feed(self, line: str) -> Optional[str]:
        """Add a line; return the finished statement text or None while incomplete."""
        self.buf.append(line)
        i, n = 0, len(line)
        while i < n:
            if self.in_dollar:
                j = line.find(self.in_dollar, i)
                if j < 0:
                    i = n
                else:
                    i, self.in_dollar = j + len(self.in_dollar), None
                continue
            c = line[i]
            if self.in_quote:
                if c == "'":
                    if line[i + 1:i + 2] == "'":
                        i += 1                      # escaped quote ''
                    else:
                        self.in_quote = False
            elif c == "'":
                self.in_quote = True
            elif c == "-" and line[i + 1:i + 2] == "-":
                break                               # rest of line is a comment
            elif c == "$":
                m = _DOLLAR.match(line, i)
                if m:
                    self.in_dollar = m.group(0)
                    i = m.end()
                    continue
            i += 1
        if self.in_dollar or self.in_quote:
            return None
        body = line.split("--", 1)[0].rstrip() if "--" in line else line.rstrip()
        if body.endswith(";"):
            stmt, self.buf = "".join(self.buf), []
            return stmt
        return None

    def pending(self) -> bool:
        return bool(self.buf)


def pg_timescale_to_plain(src: Iterable[str], dst, rep: Report) -> None:
    """Rewrite a pg_dump of a TimescaleDB database so it loads into stock PostgreSQL.

    * every statement that mentions timescaledb internals is dropped
      (extension, catalog, hypertable triggers, policies, pre/post_restore calls)
    * rows that pg_dump stored in hypertable chunks are loaded straight into the parent table
    """
    chunk_parent: Dict[str, str] = {}
    sc = _StmtScanner()
    in_copy = False
    skip_copy = False
    for line in src:
        if in_copy:
            if line.rstrip("\r\n") == "\\.":
                in_copy = False
                if not skip_copy:
                    dst.write(line)
                continue
            if not skip_copy:
                dst.write(line)
            continue
        if not sc.pending():
            stripped = line.strip()
            if not stripped:
                dst.write(line)
                continue
            if stripped.startswith("\\"):                  # psql meta-command (\restrict / \unrestrict …)
                dst.write(line)
                continue
            if stripped.startswith("--"):
                if "timescaledb" in stripped.lower():
                    rep.bump("comments_dropped")
                else:
                    dst.write(line)
                continue
        stmt = sc.feed(line)
        if stmt is None:
            continue
        head = stmt.lstrip()
        m = _COPY.match(head.strip()) if head.startswith("COPY ") else None
        if m:
            table, cols = m.group(1), m.group(2)
            if table in chunk_parent:
                dst.write(f"COPY {chunk_parent[table]} ({cols}) FROM stdin;\n")
                rep.bump("chunk_copy_blocks")
                in_copy, skip_copy = True, False
            elif "timescaledb" in table.lower():
                in_copy, skip_copy = True, True
                rep.bump("internal_tables_skipped")
            else:
                dst.write(stmt)
                in_copy, skip_copy = True, False
            continue
        low = head.lower()
        ct = _CHUNK_TBL.match(head)
        if ct:
            inh = _INHERITS.search(head)
            if inh:
                chunk_parent[ct.group(1)] = inh.group(1)
                rep.bump("chunks_merged")
            rep.bump("statements_dropped")
            continue
        if _mentions_timescale(head):
            if "continuous" in low or "_materialized_hypertable" in low:
                rep.warnings.append("a TimescaleDB continuous aggregate was found and removed; "
                                    "it cannot exist on plain PostgreSQL")
            rep.bump("statements_dropped")
            continue
        dst.write(stmt)
    if sc.pending():
        dst.write("".join(sc.buf))          # truncated dump: keep the tail untouched
        rep.warnings.append("the SQL dump ended in the middle of a statement")
    if chunk_parent:
        rep.notes.append(f"{len(set(chunk_parent.values()))} hypertable(s) / {len(chunk_parent)} chunk(s) "
                         "were merged into ordinary tables")


# ───────────────────────────── MySQL family ────────────────────────────────
_UCA = re.compile(r"utf8mb4_uca1400_(\w+)")
_M0900 = re.compile(r"utf8mb4_0900_(\w+)")
_MY8_COMMENT = re.compile(r"/\*!8\d{4}\s.*?\*/", re.S)
_MARIA_COMMENT = re.compile(r"^/\*M!\d+.*?\*/;?\s*$")


def _uca_to_mysql(m: "re.Match[str]") -> str:
    k = m.group(1)
    if k.endswith("as_cs"):
        return "utf8mb4_0900_as_cs"
    if k.endswith("as_ci"):
        return "utf8mb4_0900_as_ci"
    return "utf8mb4_0900_ai_ci"


def _0900_to_maria(m: "re.Match[str]") -> str:
    k = m.group(1)
    if k == "bin" or k.endswith("as_cs"):
        return "utf8mb4_bin"
    return "utf8mb4_unicode_ci"


def mysql_family_convert(src: Iterable[str], dst, source: str, target: str, rep: Report) -> None:
    to_mysql = target == "mysql"
    for line in src:
        orig = line
        if to_mysql:
            if _MARIA_COMMENT.match(line.strip()):
                rep.bump("version_comments_dropped")
                continue
            line = _UCA.sub(_uca_to_mysql, line)
        else:
            line = _MY8_COMMENT.sub("", line)
            line = _M0900.sub(_0900_to_maria, line)
        if line != orig:
            rep.bump("lines_changed")
        dst.write(line)


# ───────────────────────────── compose / env / manifest ────────────────────
_IMAGE_LINE = re.compile(r"^(\s*)image:\s*(['\"]?)([^'\"\s#]+)\2(.*)$")


def _find_db_service(compose: str, family: Tuple[str, ...]) -> Optional[Tuple[str, str]]:
    """(service_name, image) of the database service, by image name."""
    needles = ("timescale", "postgres") if family == PG_FAMILY else ("mariadb", "mysql")
    cur = None
    in_services = False
    for raw in compose.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        s = raw.strip()
        if indent == 0:
            in_services = s == "services:"
            cur = None
            continue
        if not in_services:
            continue
        if indent == 2 and s.endswith(":") and not s.startswith("-"):
            cur = s[:-1].strip("'\"")
            continue
        if cur and indent >= 4:
            m = _IMAGE_LINE.match(raw)
            if m and any(n in m.group(3).lower() for n in needles):
                return cur, m.group(3)
    return None


def _pg_major(image: str, dump_major: Optional[int]) -> int:
    m = re.search(r"pg(\d{2})", image) or re.search(r":(\d{2})(?:\D|$)", image)
    if m:
        return int(m.group(1))
    return dump_major or 17


def rewrite_compose(compose: str, source: str, target: str, dump_major: Optional[int], rep: Report) -> str:
    family = PG_FAMILY if source in PG_FAMILY else MY_FAMILY
    found = _find_db_service(compose, family)
    if not found:
        rep.warnings.append("could not find the database service in docker-compose.yml — "
                            f"change its image to the {target} image by hand before restoring")
        return compose
    svc, old = found
    if source in PG_FAMILY:
        major = _pg_major(old, dump_major)
        new = f"postgres:{major}" if target == "postgresql" else f"timescale/timescaledb:latest-pg{major}"
        if "-ha" in old:
            rep.warnings.append("the old image is a timescaledb-ha image: its data directory differs; "
                                "check the volume mount of the database service")
        if major >= 18:
            rep.warnings.append("PostgreSQL 18+ images moved the data directory; "
                                "check the volume mount of the database service")
    else:
        new = "mysql:8.4" if target == "mysql" else "mariadb:lts"
    out, done = [], False
    for raw in compose.splitlines(keepends=True):
        m = _IMAGE_LINE.match(raw.rstrip("\r\n"))
        if not done and m and m.group(3) == old:
            eol = raw[len(raw.rstrip("\r\n")):]
            raw = f"{m.group(1)}image: {new}{m.group(4)}{eol}"
            done = True
        out.append(raw)
    rep.notes.append(f"docker-compose.yml: service '{svc}' image {old} → {new}")
    if re.search(r"healthcheck\.sh|mysqladmin|mariadb-admin", compose) and family == MY_FAMILY:
        rep.warnings.append("the database healthcheck may be engine specific — review it in docker-compose.yml")
    return "".join(out)


def _db_data_host_dirs(compose: str, svc_family: Tuple[str, ...]) -> List[str]:
    """Host directories under /var/lib/pasarguard that hold the OLD engine's raw data files."""
    target_dirs = ("/var/lib/postgresql/data", "/var/lib/postgresql", "/home/postgres/pgdata/data") \
        if svc_family == PG_FAMILY else ("/var/lib/mysql",)
    out = []
    for m in re.finditer(r"-\s*['\"]?(/var/lib/pasarguard/[^:'\"\s]+):([^:'\"\s]+)", compose):
        if m.group(2).rstrip("/") in target_dirs:
            out.append(m.group(1).rstrip("/"))
    return out


def rewrite_manifest(text: str, target: str) -> str:
    out = []
    for ln in text.splitlines(keepends=True):
        if ln.lstrip().startswith("#") and "db_type=" in ln:
            ln = re.sub(r"db_type=\S+", f"db_type={target}", ln)
        elif ln.strip() and not ln.lstrip().startswith("#"):
            cols = ln.rstrip("\n").split("\t")
            if len(cols) >= 3 and target in ("postgresql", "mariadb", "mysql"):
                cols[2] = "0"                                    # has_timescale flag
                if len(cols) >= 5:
                    cols[4] = ""
            ln = "\t".join(cols) + ("\n" if ln.endswith("\n") else "")
        out.append(ln)
    return "".join(out)


# ───────────────────────────── main entry ──────────────────────────────────
def convert_zip(src_zip: str, target: str, out_zip: Optional[str] = None,
                log: Callable[[str], None] = print) -> Report:
    """Convert ``src_zip`` to ``target`` and write the result to ``out_zip``. Never restores anything."""
    if not os.path.isfile(src_zip):
        raise ConvertError(f"file not found: {src_zip}")
    if not zipfile.is_zipfile(src_zip):
        raise ConvertError("not a valid zip file")
    with zipfile.ZipFile(src_zip) as zin:
        source = _detect_type(zin)
        if target == source:
            raise ConvertError(f"the backup already uses {source}")
        if target not in supported_targets(source):
            ok = ", ".join(supported_targets(source)) or "none"
            raise ConvertError(f"{source} → {target} is not supported (from {source} you can convert to: {ok}). "
                               "Conversions across SQL families (SQLite / PostgreSQL / MySQL) are not offered "
                               "because PasarGuard's own migrations must create the schema.")
        names = zin.namelist()
        for n in names:
            _check_member(n)
        if not any(SQL_RE.match(n) for n in names):
            raise ConvertError("the archive has no db_dump/db-NNN.sql dump")
        rep = Report(source, target)
        base = os.path.basename(src_zip)
        if out_zip is None:
            stem = base[:-4] if base.lower().endswith(".zip") else base
            out_zip = os.path.join(os.path.dirname(os.path.abspath(src_zip)), f"converted_{target}_{stem}.zip")
        out_zip = os.path.abspath(out_zip)
        if os.path.abspath(src_zip) == out_zip:
            raise ConvertError("output file must differ from the source file")
        os.makedirs(os.path.dirname(out_zip), exist_ok=True)

        compose = zin.read("docker-compose.yml").decode("utf-8", "replace") if "docker-compose.yml" in names else ""
        family = PG_FAMILY if source in PG_FAMILY else MY_FAMILY
        skip_prefixes = [f"pasarguard_data/{os.path.relpath(d, '/var/lib/pasarguard')}/"
                         for d in _db_data_host_dirs(compose, family)]

        tmp_out = out_zip + ".part"
        dump_major: Optional[int] = None
        fd = os.open(tmp_out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
                # 1. dumps first (so the PostgreSQL major version is known for the compose rewrite)
                for info in zin.infolist():
                    n = info.filename
                    if info.is_dir() or not SQL_RE.match(n):
                        continue
                    if info.file_size > MAX_ENTRY:
                        raise ConvertError(f"{n} is unreasonably large")
                    log(f"converting {n} ({info.file_size // 1024 // 1024} MiB) …")
                    with tempfile.TemporaryFile("w+", encoding="utf-8", newline="") as tmp:
                        with zin.open(info) as fh:
                            txt = io.TextIOWrapper(fh, encoding="utf-8", errors="surrogateescape", newline="")

                            def lines():
                                nonlocal dump_major
                                for i, ln in enumerate(txt):
                                    if dump_major is None and i < 40:
                                        mm = _PG_VER.search(ln)
                                        if mm:
                                            dump_major = int(mm.group(1))
                                    yield ln
                            if source in PG_FAMILY:
                                if source == "timescaledb" and target == "postgresql":
                                    pg_timescale_to_plain(lines(), tmp, rep)
                                else:                                   # postgresql -> timescaledb: dump loads as is
                                    for ln in lines():
                                        tmp.write(ln)
                            else:
                                mysql_family_convert(lines(), tmp, source, target, rep)
                        tmp.flush()
                        tmp.seek(0)
                        with zout.open(zipfile.ZipInfo(n, time.localtime(time.time())[:6]), "w", force_zip64=True) as w:
                            zinfo_enc = "utf-8"
                            while True:
                                chunk = tmp.read(1 << 20)
                                if not chunk:
                                    break
                                w.write(chunk.encode(zinfo_enc, "surrogateescape"))
                # 2. everything else, with the three small files rewritten
                for info in zin.infolist():
                    n = info.filename
                    if info.is_dir() or SQL_RE.match(n):
                        continue
                    if any(n.startswith(p) for p in skip_prefixes):
                        rep.bump("old_engine_data_files_skipped")
                        continue
                    if n == "db_dump/CONVERSION.txt":
                        continue
                    if n == "docker-compose.yml":
                        zout.writestr(n, rewrite_compose(compose, source, target, dump_major, rep))
                    elif n == MANIFEST:
                        zout.writestr(n, rewrite_manifest(zin.read(n).decode("utf-8", "replace"), target))
                    else:
                        with zin.open(info) as r, zout.open(zipfile.ZipInfo(n, info.date_time), "w", force_zip64=True) as w:
                            shutil.copyfileobj(r, w, 1 << 20)
                if rep.stats.get("old_engine_data_files_skipped"):
                    rep.notes.append("the old engine's raw data files were left out of the new archive "
                                     f"({rep.stats['old_engine_data_files_skipped']} files)")
                history = zin.read("db_dump/CONVERSION.txt").decode("utf-8", "replace") \
                    if "db_dump/CONVERSION.txt" in names else ""
                zout.writestr("db_dump/CONVERSION.txt",
                              f"Converted by PG-Backup (pgb.dbconv): {source} -> {target}\n"
                              f"Source archive: {base}\n" + "".join(f"- {x}\n" for x in rep.notes + rep.warnings)
                              + ("\n" + history if history else ""))
            os.replace(tmp_out, out_zip)
            os.chmod(out_zip, 0o600)
        except BaseException:
            try:
                os.remove(tmp_out)
            except OSError:
                pass
            raise
    rep.out_path = out_zip
    return rep


def verify_output(path: str, target: str) -> List[str]:
    """Post-conversion self-check; returns a list of problems (empty = ok)."""
    problems: List[str] = []
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            problems.append(f"corrupt entry: {bad}")
        if read_manifest_type(z) != target:
            problems.append("manifest db_type was not updated")
        if target == "postgresql":
            for n in z.namelist():
                if SQL_RE.match(n):
                    in_copy = False
                    with z.open(n) as fh:
                        for raw in fh:
                            ln = raw.decode("utf-8", "replace")
                            if in_copy:
                                in_copy = ln.rstrip("\r\n") != "\\."
                                continue
                            if ln.startswith("COPY ") and ln.rstrip().endswith("FROM stdin;"):
                                in_copy = True
                            if not ln.lstrip().startswith(("--", "\\")) and _mentions_timescale(ln):
                                problems.append(f"{n}: timescaledb reference left: {ln.strip()[:80]}")
                                break
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="pgb.dbconv", description=__doc__.split("\n\n")[0])
    ap.add_argument("file", help="PG-Backup .zip to convert (read only)")
    ap.add_argument("--to", required=True, choices=sorted({t for v in SUPPORTED.values() for t in v}))
    ap.add_argument("--out", help="output .zip (default: converted_<engine>_<name>.zip next to the source)")
    a = ap.parse_args(argv)
    try:
        rep = convert_zip(a.file, a.to, a.out)
        probs = verify_output(rep.out_path, a.to)
    except ConvertError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    for n in rep.notes:
        print(f"[*] {n}")
    for w in rep.warnings:
        print(f"[!] {w}", file=sys.stderr)
    if rep.stats:
        print("[*] " + ", ".join(f"{k}={v}" for k, v in sorted(rep.stats.items())))
    if probs:
        for p in probs:
            print(f"[-] {p}", file=sys.stderr)
        return 3
    print(f"[+] Converted {rep.source_type} → {rep.target_type}: {rep.out_path}")
    print("[+] Nothing was restored; the source archive and this server were not touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
