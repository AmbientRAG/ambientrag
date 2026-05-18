"""Shared utilities for AmbientRAG."""
import shutil
import socket
import subprocess
from pathlib import Path

import click
from rich.console import Console

console = Console()


def run_sql(db_url: str, sql: str) -> None:
    """Execute SQL via psycopg2."""
    import psycopg2

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()


def run_sql_file(db_url: str, file_path: Path) -> None:
    """Execute a .sql file via psycopg2."""
    sql = file_path.read_text()
    run_sql(db_url, sql)


def check_port(port: int) -> bool:
    """Check if something is listening on a port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_command(cmd: str) -> bool:
    """Check if a command is on PATH."""
    return shutil.which(cmd) is not None


def check_db_exists(db_name: str) -> bool:
    """Check if a PostgreSQL database exists."""
    try:
        result = subprocess.run(
            ["psql", "-lqt"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return any(
            db_name == line.split("|")[0].strip()
            for line in result.stdout.splitlines()
            if "|" in line
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def create_db(db_name: str) -> bool:
    """Create a PostgreSQL database."""
    try:
        subprocess.run(
            ["createdb", db_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def print_success(msg: str) -> None:
    console.print(f"[green]  OK[/green] {msg}")


def print_error(msg: str) -> None:
    console.print(f"[red]  FAIL[/red] {msg}")


def print_warning(msg: str) -> None:
    console.print(f"[yellow]  WARN[/yellow] {msg}")


def print_info(msg: str) -> None:
    console.print(f"[blue]  INFO[/blue] {msg}")


def confirm(msg: str) -> bool:
    return click.confirm(msg, default=True)
