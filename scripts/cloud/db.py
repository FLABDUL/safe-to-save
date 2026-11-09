"""Own one synthetic-only PostgreSQL 14 cluster per Cloud checkout."""

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["name"]
SETTINGS = {
    "budget-v2": (55432, "budget_v2", "budget_cloud"),
    "safe-to-save": (55433, "safe_to_save", "sts_cloud"),
}
PORT, DATABASE, ADMIN = SETTINGS[PROJECT]
STATE = ROOT / ".cloud"
DATA = STATE / "pgdata"
CONFIG = STATE / "database.json"


def checked_paths():
    for path in (STATE, DATA, CONFIG, STATE / "socket", STATE / "server.log"):
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("Cloud database state must stay inside this checkout")
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".cloud/database.json"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if ignored.returncode:
        raise ValueError("Ignore .cloud/ in Git before creating database state")


def validate_config(value):
    expected = {
        "project": PROJECT,
        "root": str(ROOT),
        "port": PORT,
        "database": DATABASE,
        "user": ADMIN,
        "synthetic_only": True,
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError("Refusing database configuration from another checkout or project")
    if not re.fullmatch(r"[0-9a-f]{64}", value.get("password", "")):
        raise ValueError("Unexpected Cloud database credentials")
    return value


def config():
    checked_paths()
    return validate_config(json.loads(CONFIG.read_text()))


def pg_bin():
    directory = os.environ.get("PG_BIN")
    if directory:
        result = Path(directory)
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        result = cache / "codex-cloud" / "postgresql-14.24" / "bin"
    version = subprocess.check_output([str(result / "postgres"), "--version"], text=True)
    if not re.search(r"\b14\.", version):
        raise ValueError("PostgreSQL 14 tools are required")
    return result


def pg_env(value):
    # Never inherit libpq service files, address overrides, passwords or options.
    result = {key: item for key, item in os.environ.items() if not key.startswith("PG")}
    result.update(PGPASSWORD=value["password"], PGHOSTADDR="127.0.0.1")
    return result


def pg_call(binary, *args, value=None):
    return subprocess.run(
        [str(pg_bin() / binary), *map(str, args)],
        env=pg_env(value) if value else pg_env({"password": ""}),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def owned_cluster():
    value = config()
    if (DATA / "PG_VERSION").read_text().strip() != "14":
        raise ValueError("Expected the marked PostgreSQL 14 Cloud cluster")
    return value


def running_cluster():
    value = owned_cluster()
    pid = (DATA / "postmaster.pid").read_text().splitlines()
    if len(pid) < 6 or pid[1] != str(DATA) or pid[3] != str(PORT) or pid[5] != "127.0.0.1":
        raise ValueError("Running server does not match this checkout's cluster")
    pg_call("pg_ctl", "-D", DATA, "status", value=value)
    return value


def unused_port():
    with socket.socket() as probe:
        # PostgreSQL also reuses its listening address after a clean stop.
        # Closed client connections can otherwise keep this probe in TIME_WAIT.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", PORT))


def start():
    checked_paths()
    pg_bin()
    if not DATA.exists():
        if CONFIG.exists():
            raise ValueError("Existing credentials without cluster; refusing to recreate it")
        unused_port()
        STATE.mkdir(mode=0o700, exist_ok=True)
        STATE.chmod(0o700)
        value = {
            "project": PROJECT,
            "root": str(ROOT),
            "port": PORT,
            "database": DATABASE,
            "user": ADMIN,
            "synthetic_only": True,
            "password": secrets.token_hex(32),
        }
        password_file = STATE / "init-password"
        # Exclusive creation prevents replacing a pre-existing file or symlink.
        with open(password_file, "x", opener=lambda p, f: os.open(p, f, 0o600)) as handle:
            handle.write(value["password"] + "\n")
        try:
            pg_call(
                "initdb",
                "-D",
                DATA,
                "-U",
                ADMIN,
                "--encoding=UTF8",
                "--locale=C",
                "--auth-host=scram-sha-256",
                "--auth-local=scram-sha-256",
                f"--pwfile={password_file}",
            )
        finally:
            password_file.unlink()
        with open(CONFIG, "x", opener=lambda p, f: os.open(p, f, 0o600)) as handle:
            json.dump(value, handle)
        (STATE / "socket").mkdir(mode=0o700)
        with (DATA / "postgresql.conf").open("a") as handle:
            handle.write(f"\nlisten_addresses = '127.0.0.1'\nport = {PORT}\n")
            handle.write(
                f"unix_socket_directories = '{str(STATE / 'socket').replace(chr(39), chr(39) * 2)}'\n"
            )
    value = owned_cluster()
    if (DATA / "postmaster.pid").exists():
        running_cluster()
    else:
        unused_port()
        pg_call("pg_ctl", "-D", DATA, "-l", STATE / "server.log", "-w", "start", value=value)
        running_cluster()
    exists = pg_call(
        "psql",
        "-X",
        "-w",
        "-h",
        "127.0.0.1",
        "-p",
        PORT,
        "-U",
        ADMIN,
        "-d",
        "postgres",
        "-tAc",
        f"SELECT 1 FROM pg_database WHERE datname='{DATABASE}'",
        value=value,
    )
    if exists != "1":
        pg_call("createdb", "-w", "-h", "127.0.0.1", "-p", PORT, "-U", ADMIN, DATABASE, value=value)
    print(f"{PROJECT}: synthetic development database ready on loopback port {PORT}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "run"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "start":
        start()
    elif args.action == "stop":
        value = running_cluster()
        pg_call("pg_ctl", "-D", DATA, "-m", "fast", "-w", "stop", value=value)
        print(f"{PROJECT}: owned Cloud cluster stopped.")
    else:
        value = running_cluster()
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser.error("run needs a command after --")
        env = pg_env(value)
        env["DATABASE_URL"] = (
            f"postgresql://{ADMIN}:{value['password']}@127.0.0.1:{PORT}/{DATABASE}"
        )
        raise SystemExit(subprocess.run(command, env=env, cwd=ROOT, check=False).returncode)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError, KeyError):
        print(
            "Cloud database operation failed. Check the project setup and ignored .cloud logs; "
            "credentials and database details are suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
