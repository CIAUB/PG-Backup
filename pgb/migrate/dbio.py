"""Database access layer for the migration engine.

Three things live here:

* ``SqliteDB``  – a SQLite file (read from a private copy, written in a single
  transaction).
* ``DockerDB``  – a PostgreSQL / TimescaleDB / MySQL / MariaDB server running
  in a ``docker compose`` project, reached through ``docker compose exec``
  (the same way PG-Backup itself backs those databases up). SQL is piped in on
  stdin; credentials never appear on a shell command line we build.
* ``Stmt``      – a parameterised statement. SQLite binds the parameters;
  the docker back-ends render them into dialect-correct literals.

Everything that touches the network/containers is isolated behind ``BaseDB``
so the readers/writers can be unit-tested against plain SQLite files.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "DBError", "Column", "Stmt", "BaseDB", "SqliteDB", "DockerDB",
    "quote_ident", "render_literal", "render_statement", "to_db_datetime",
    "parse_dt", "parse_json", "copy_sqlite_snapshot",
]


class DBError(RuntimeError):
    """Raised for any database access problem (message is user-presentable)."""


@dataclass
class Column:
    name: str
    type: str = ""
    notnull: bool = False
    has_default: bool = False
    pk: bool = False

    @property
    def required(self) -> bool:
        """True when an INSERT must supply a value."""
        return self.notnull and not self.has_default and not self.pk


@dataclass
class Stmt:
    sql: str          # uses ``?`` placeholders only
    params: tuple = ()


# --------------------------------------------------------------------------
# value helpers
# --------------------------------------------------------------------------
def quote_ident(dialect: str, name: str) -> str:
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def parse_dt(value: Any) -> Optional[datetime]:
    """Best-effort conversion of a DB value to a *naive UTC* datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        # unix seconds (or milliseconds)
        v = float(value)
        if v > 1e11:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc).replace(tzinfo=None)
    else:
        s = str(value).strip()
        if not s:
            return None
        if re.fullmatch(r"-?\d+(\.\d+)?", s):
            return parse_dt(float(s))
        s = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                        "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_db_datetime(dialect: str, dt: Optional[datetime]) -> Optional[str]:
    """Naive-UTC datetime -> literal string for the given dialect."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if dialect == "postgresql":
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f") + "+00:00"
    if dialect == "mysql":
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def parse_json(value: Any, default: Any = None) -> Any:
    """Decode a JSON column that may arrive as str / dict / list / None."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    s = str(value).strip()
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


def render_literal(dialect: str, value: Any) -> str:
    """Render one Python value as an SQL literal (docker/text back-ends)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        if dialect == "postgresql":
            return "true" if value else "false"
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, datetime):
        value = to_db_datetime(dialect, value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    s = str(value)
    if "\x00" in s:
        raise DBError("a value contains a NUL byte and cannot be written safely")
    if dialect == "mysql":
        s = s.replace("\\", "\\\\").replace("'", "''")
    else:
        s = s.replace("'", "''")
    return "'" + s + "'"


def adapt_param(dialect: str, value: Any) -> Any:
    """Convert a Python value to what the sqlite3 driver can bind."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, datetime):
        return to_db_datetime(dialect, value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return value


def render_statement(dialect: str, stmt: Stmt) -> str:
    """Substitute ``?`` placeholders with literals (never inside quotes: our
    generated SQL never contains a literal question mark)."""
    parts = stmt.sql.split("?")
    if len(parts) - 1 != len(stmt.params):
        raise DBError(f"placeholder/parameter mismatch in: {stmt.sql[:120]}")
    out = [parts[0]]
    for piece, val in zip(parts[1:], stmt.params):
        out.append(render_literal(dialect, val))
        out.append(piece)
    return "".join(out)


# --------------------------------------------------------------------------
# base class
# --------------------------------------------------------------------------
class BaseDB:
    dialect = "sqlite"
    label = ""

    # introspection ---------------------------------------------------------
    def table_names(self) -> List[str]:
        raise NotImplementedError

    def columns(self, table: str) -> List[Column]:
        raise NotImplementedError

    def has_table(self, table: str) -> bool:
        return table in set(self.table_names())

    def column_names(self, table: str) -> List[str]:
        return [c.name for c in self.columns(table)]

    # reading ----------------------------------------------------------------
    def select(self, table: str, columns: Optional[Sequence[str]] = None,
               order_by: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def query(self, sql: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def scalar(self, sql: str) -> Any:
        rows = self.query(sql)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    # writing ----------------------------------------------------------------
    def execute(self, stmts: Sequence[Stmt]) -> None:
        """Run all statements atomically (all-or-nothing)."""
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - trivial
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# --------------------------------------------------------------------------
# SQLite
# --------------------------------------------------------------------------
def copy_sqlite_snapshot(path: str, dest_dir: str) -> str:
    """Copy a SQLite database together with its -wal/-shm companions so a live
    (WAL-mode) database is read consistently without touching the original."""
    if not os.path.isfile(path):
        raise DBError(f"SQLite file not found: {path}")
    dst = os.path.join(dest_dir, "snapshot.sqlite3")
    shutil.copy2(path, dst)
    for ext in ("-wal", "-shm"):
        if os.path.exists(path + ext):
            shutil.copy2(path + ext, dst + ext)
    return dst


class SqliteDB(BaseDB):
    dialect = "sqlite"

    def __init__(self, path: str, writable: bool = False):
        self.path = path
        self.writable = writable
        self.label = f"sqlite:{path}"
        self._tmp: Optional[str] = None
        if writable:
            if not os.path.isfile(path):
                raise DBError(f"SQLite file not found: {path}")
            target = path
        else:
            self._tmp = tempfile.mkdtemp(prefix="pgb_src_")
            os.chmod(self._tmp, 0o700)
            target = copy_sqlite_snapshot(path, self._tmp)
        try:
            self._conn = sqlite3.connect(target, timeout=30, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.Error as e:
            self.close()
            raise DBError(f"cannot open SQLite database {path}: {e}")

    def table_names(self) -> List[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return [r[0] for r in rows]

    def columns(self, table: str) -> List[Column]:
        rows = self._conn.execute(f"PRAGMA table_info({quote_ident('sqlite', table)})").fetchall()
        cols = []
        for r in rows:
            cols.append(Column(
                name=r["name"], type=(r["type"] or "").upper(),
                notnull=bool(r["notnull"]), has_default=r["dflt_value"] is not None,
                pk=bool(r["pk"]),
            ))
        return cols

    def select(self, table, columns=None, order_by=None):
        cols = "*" if not columns else ", ".join(quote_ident("sqlite", c) for c in columns)
        sql = f"SELECT {cols} FROM {quote_ident('sqlite', table)}"
        if order_by:
            sql += f" ORDER BY {quote_ident('sqlite', order_by)}"
        return self.query(sql)

    def query(self, sql):
        try:
            return [dict(r) for r in self._conn.execute(sql).fetchall()]
        except sqlite3.Error as e:
            raise DBError(f"query failed: {e}")

    def execute(self, stmts):
        if not self.writable:
            raise DBError("database was opened read-only")
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            for st in stmts:
                cur.execute(st.sql, tuple(adapt_param("sqlite", p) for p in st.params))
            cur.execute("COMMIT")
        except sqlite3.Error as e:
            try:
                cur.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise DBError(f"write failed (rolled back): {e}")

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None


# --------------------------------------------------------------------------
# Docker (PostgreSQL / TimescaleDB / MySQL / MariaDB)
# --------------------------------------------------------------------------
class DockerDB(BaseDB):
    """A database server inside a docker-compose project."""

    def __init__(self, compose_dir: str, service: str, dialect: str,
                 user: str, password: str, dbname: str, timeout: int = 600):
        if dialect not in ("postgresql", "mysql"):
            raise DBError(f"unsupported docker dialect: {dialect}")
        self.compose_dir = compose_dir
        self.service = service
        self.dialect = dialect
        self.user = user
        self.password = password
        self.dbname = dbname
        self.timeout = timeout
        self.label = f"{dialect}:{service}@{compose_dir}/{dbname}"

    # -- low level -----------------------------------------------------------
    def _cmd(self, extra_tail: Sequence[str]) -> List[str]:
        base = ["docker", "compose", "exec", "-T"]
        if self.dialect == "postgresql":
            if self.password:
                base += ["-e", "PGPASSWORD"]          # value comes from our env, never argv
            base += [self.service, "psql", "-U", self.user, "-d", self.dbname,
                     "-v", "ON_ERROR_STOP=1", "-X", "-q", "-t", "-A"]
        else:
            if self.password:
                base += ["-e", "MYSQL_PWD"]
            base += [self.service, "mysql", "-u", self.user, "--batch", "--raw",
                     "--skip-column-names", "--default-character-set=utf8mb4", self.dbname]
        return base + list(extra_tail)

    def env(self) -> Dict[str, str]:
        """Environment for the docker client: carries the DB password so it never
        shows up in `ps` (argv is world-readable on Linux)."""
        e = dict(os.environ)
        if self.password:
            e["PGPASSWORD" if self.dialect == "postgresql" else "MYSQL_PWD"] = self.password
        return e

    def _run(self, sql: str) -> str:
        cmd = self._cmd([])
        try:
            r = subprocess.run(cmd, cwd=self.compose_dir, input=sql.encode("utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=self.timeout, env=self.env())
        except FileNotFoundError:
            raise DBError("docker is not installed or not in PATH")
        except subprocess.TimeoutExpired:
            raise DBError("database command timed out")
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", "replace").strip()
            # never echo the password back
            if self.password:
                err = err.replace(self.password, "***")
            first = next((ln for ln in err.splitlines() if ln.strip()), "unknown error")
            raise DBError(f"{self.dialect} command failed: {first}")
        return r.stdout.decode("utf-8", "replace")

    def _json_lines(self, sql: str) -> List[Dict[str, Any]]:
        out = self._run(sql)
        rows = []
        for ln in out.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except ValueError:
                raise DBError(f"unexpected database output: {ln[:100]}")
        return rows

    # -- introspection -------------------------------------------------------
    def table_names(self) -> List[str]:
        if self.dialect == "postgresql":
            sql = ("SELECT row_to_json(r)::text FROM (SELECT table_name AS n FROM information_schema.tables "
                   "WHERE table_schema = current_schema() AND table_type='BASE TABLE') r;")
        else:
            sql = ("SELECT JSON_OBJECT('n', table_name) FROM information_schema.tables "
                   "WHERE table_schema = DATABASE() AND table_type='BASE TABLE';")
        return [r["n"] for r in self._json_lines(sql)]

    def columns(self, table: str) -> List[Column]:
        t = render_literal(self.dialect, table)
        if self.dialect == "postgresql":
            sql = ("SELECT row_to_json(r)::text FROM (SELECT column_name AS n, data_type AS t, is_nullable AS nl, "
                   "column_default AS d, is_identity AS ident FROM information_schema.columns "
                   f"WHERE table_schema = current_schema() AND table_name = {t} ORDER BY ordinal_position) r;")
            rows = self._json_lines(sql)
            return [Column(name=r["n"], type=str(r["t"]).upper(), notnull=(r["nl"] == "NO"),
                           has_default=(r["d"] is not None or r.get("ident") == "YES")) for r in rows]
        sql = ("SELECT JSON_OBJECT('n', column_name, 't', data_type, 'nl', is_nullable, 'd', column_default, "
               "'x', extra, 'k', column_key) FROM information_schema.columns "
               f"WHERE table_schema = DATABASE() AND table_name = {t} ORDER BY ordinal_position;")
        rows = self._json_lines(sql)
        return [Column(name=r["n"], type=str(r["t"]).upper(), notnull=(r["nl"] == "NO"),
                       has_default=(r["d"] is not None or "auto_increment" in str(r.get("x", "")).lower()
                                    or "generated" in str(r.get("x", "")).lower()),
                       pk=(r.get("k") == "PRI")) for r in rows]

    # -- reading -------------------------------------------------------------
    def select(self, table, columns=None, order_by=None):
        qt = quote_ident(self.dialect, table)
        if self.dialect == "postgresql":
            if columns:
                inner = "SELECT " + ", ".join(quote_ident("postgresql", c) for c in columns) + f" FROM {qt}"
            else:
                inner = f"SELECT * FROM {qt}"
            if order_by:
                inner += f" ORDER BY {quote_ident('postgresql', order_by)}"
            return self._json_lines(f"SELECT row_to_json(t)::text FROM ({inner}) t;")
        cols = list(columns) if columns else self.column_names(table)
        if not cols:
            return []
        pairs = ", ".join(f"{render_literal('mysql', c)}, {quote_ident('mysql', c)}" for c in cols)
        sql = f"SELECT JSON_OBJECT({pairs}) FROM {qt}"
        if order_by:
            sql += f" ORDER BY {quote_ident('mysql', order_by)}"
        return self._json_lines(sql + ";")

    def query(self, sql):
        # Only used for tiny scalar queries; wrap so the output is JSON.
        if self.dialect == "postgresql":
            return self._json_lines(f"SELECT row_to_json(t)::text FROM ({sql.rstrip(';')}) t;")
        return self._json_lines(f"SELECT JSON_OBJECT('v', x.v) FROM (SELECT ({sql.rstrip(';')}) AS v) x;") \
            if sql.lstrip().upper().startswith("SELECT") and " FROM " not in sql.upper() else \
            self._json_lines(sql)

    def ping(self) -> bool:
        try:
            self._run("SELECT 1;")
            return True
        except DBError:
            return False

    # -- writing -------------------------------------------------------------
    def execute(self, stmts):
        lines: List[str] = []
        if self.dialect == "mysql":
            lines.append("SET SESSION sql_mode = REPLACE(@@sql_mode, 'NO_BACKSLASH_ESCAPES', '');")
            lines.append("SET NAMES utf8mb4;")
            lines.append("START TRANSACTION;")
        else:
            lines.append("BEGIN;")
        for st in stmts:
            lines.append(render_statement(self.dialect, st).rstrip().rstrip(";") + ";")
        lines.append("COMMIT;")
        # On the first error psql (ON_ERROR_STOP) / mysql (no --force) stop and
        # the open transaction is rolled back when the connection closes.
        self._run("\n".join(lines) + "\n")
