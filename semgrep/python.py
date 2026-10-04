# Rule tests for python.yml, run by `semgrep --test`. Not application code.
#
# Each rule is tested in both directions: it must fire on the violation, and it must stay
# quiet on the correct equivalent next to it. The second half is what keeps a rule usable:
# one that flags correct code gets suppressed, and then it protects nothing.
#
# ruff: noqa
# mypy: ignore-errors

import asyncio
import importlib
import json
import logging
import os
import subprocess
import sys

import yaml

SELECT_ASSET_BY_ID = "SELECT * FROM assets WHERE id = ?"


# --- sift-no-string-built-sql ------------------------------------------------------


async def sql_violations(conn, asset_id, ids, table, order):
    # ruleid: sift-no-string-built-sql
    await conn.execute(f"SELECT * FROM assets WHERE id = {asset_id}")

    # ruleid: sift-no-string-built-sql
    await conn.execute("SELECT * FROM assets WHERE id = '%s'" % asset_id)

    # ruleid: sift-no-string-built-sql
    await conn.execute("SELECT * FROM assets WHERE id = '" + asset_id + "'")

    # ruleid: sift-no-string-built-sql
    await conn.execute("SELECT * FROM assets WHERE id = {}".format(asset_id))

    # Built in one place, executed in another: the usual shape of the bug, and the reason
    # the rule runs in taint mode instead of matching a call.
    query = f"SELECT * FROM assets ORDER BY {order}"
    # ruleid: sift-no-string-built-sql
    await conn.execute(query)

    sql = "DELETE FROM assets WHERE id = " + str(asset_id)
    stmt = sql
    # ruleid: sift-no-string-built-sql
    await conn.executescript(stmt)

    # ruleid: sift-no-string-built-sql
    await conn.executemany(f"INSERT INTO {table} VALUES (?)", ids)

    # ruleid: sift-no-string-built-sql
    await conn.execute_fetchall(f"SELECT * FROM assets WHERE owner = {asset_id}")

    # The kernel's read helpers are sinks too: a hand-built query passed to a read is as injectable
    # as one passed to a write.
    # ruleid: sift-no-string-built-sql
    await db.fetch_all(f"SELECT * FROM tags WHERE name = '{title}'")

    # ruleid: sift-no-string-built-sql
    await db.fetch_one("SELECT * FROM tags WHERE id = " + str(asset_id))


async def sql_correct(conn, asset_id, title, ids):
    # ok: sift-no-string-built-sql
    await db.fetch_all("SELECT * FROM tags WHERE name = ?", (title,))

    # ok: sift-no-string-built-sql
    await conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))

    # ok: sift-no-string-built-sql
    await conn.execute(SELECT_ASSET_BY_ID, (asset_id,))

    # A computed *bound parameter* is correct usage and must not be flagged.
    # ok: sift-no-string-built-sql
    await conn.execute("SELECT * FROM assets WHERE title LIKE ?", (f"%{title}%",))

    # ok: sift-no-string-built-sql
    await conn.execute(
        "UPDATE assets SET title = ?, rating = ? WHERE id = ?",
        (title.strip(), 5, asset_id),
    )

    # ok: sift-no-string-built-sql
    await conn.execute(
        """
        SELECT a.id, a.title
          FROM assets a
          JOIN asset_tags t ON t.asset_id = a.id
         WHERE t.tag_id = ?
        """,
        (ids[0],),
    )

    # The sanctioned expansion. It can only ever add `?` and `,`, so the query it returns is not
    # built from anything a caller supplied.
    query, params = in_clause("SELECT * FROM assets WHERE id IN (?*)", ids)
    # ok: sift-no-string-built-sql
    await conn.execute(query, params)


# --- sift-no-database-driver-outside-kernel ----------------------------------------

# ruleid: sift-no-database-driver-outside-kernel
import aiosqlite

# ruleid: sift-no-database-driver-outside-kernel
import sqlite3

# ruleid: sift-no-database-driver-outside-kernel
from aiosqlite import Connection


# --- sift-no-shell-true ------------------------------------------------------------


def shell_violations(url):
    # ruleid: sift-no-shell-true
    subprocess.run(f"yt-dlp {url}", shell=True)

    # ruleid: sift-no-shell-true
    subprocess.Popen("gallery-dl " + url, shell=True)

    # ruleid: sift-no-shell-true
    subprocess.check_output(f"ffprobe {url}", shell=True)

    # ruleid: sift-no-shell-true
    os.system(f"ffmpeg -i {url} out.mp4")

    # ruleid: sift-no-shell-true
    os.popen(f"yt-dlp {url}")


async def shell_violation_async(url):
    # ruleid: sift-no-shell-true
    await asyncio.create_subprocess_shell(f"yt-dlp {url}")


def shell_correct(url, dest):
    # A list argv is not re-parsed, so a URL containing shell metacharacters stays a URL.
    # ok: sift-no-shell-true
    subprocess.run(["yt-dlp", "--no-playlist", "-o", dest, url], check=True)

    # ok: sift-no-shell-true
    subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", url], check=True)


async def shell_correct_async(url, dest):
    # ok: sift-no-shell-true
    await asyncio.create_subprocess_exec("yt-dlp", "-o", dest, url)


# --- sift-no-unsafe-deserialization ------------------------------------------------

# ruleid: sift-no-unsafe-deserialization
import pickle

# ruleid: sift-no-unsafe-deserialization
import marshal


def deser_violations(blob, fh):
    # ruleid: sift-no-unsafe-deserialization
    pickle.loads(blob)

    # ruleid: sift-no-unsafe-deserialization
    pickle.load(fh)

    # ruleid: sift-no-unsafe-deserialization
    yaml.load(blob)

    # ruleid: sift-no-unsafe-deserialization
    yaml.load(blob, Loader=yaml.Loader)


def deser_correct(blob, payload):
    # ok: sift-no-unsafe-deserialization
    json.loads(blob)

    # ok: sift-no-unsafe-deserialization
    json.dumps(payload)

    # ok: sift-no-unsafe-deserialization
    yaml.safe_load(blob)


# --- sift-no-downloader-import -----------------------------------------------------

# ruleid: sift-no-downloader-import
import yt_dlp

# ruleid: sift-no-downloader-import
import gallery_dl

# ruleid: sift-no-downloader-import
from yt_dlp import YoutubeDL

# ruleid: sift-no-downloader-import
from gallery_dl import job


def dynamic_import_violations():
    # ruleid: sift-no-downloader-import
    importlib.import_module("yt_dlp")

    # ruleid: sift-no-downloader-import
    __import__("gallery_dl")


def downloader_correct(url, dest):
    # ok: sift-no-downloader-import
    subprocess.run(["yt-dlp", "-o", dest, url], check=True)


# --- sift-no-print-or-raw-logger ---------------------------------------------------


def logging_violations(cookie_jar, request_body):
    # ruleid: sift-no-print-or-raw-logger
    print(f"fetching with cookies: {cookie_jar}")

    # ruleid: sift-no-print-or-raw-logger
    log = logging.getLogger(__name__)

    # ruleid: sift-no-print-or-raw-logger
    logging.basicConfig(level="DEBUG")

    # ruleid: sift-no-print-or-raw-logger
    sys.stdout.write(str(request_body))

    # ruleid: sift-no-print-or-raw-logger
    sys.stderr.write("boom")


def logging_correct(asset_id, url):
    from sift.kernel.log import get_logger

    log = get_logger(__name__)

    # ok: sift-no-print-or-raw-logger
    log.info("download.started", asset_id=asset_id, host=url_host(url))

    # ok: sift-no-print-or-raw-logger
    log.warning("download.failed", asset_id=asset_id, reason="timeout")


def url_host(url):
    return url


# --- sift-no-pin-in-key-derivation -------------------------------------------------


def pin_in_kdf_violations(master_key, wrapped, password, pin, old_password, body, self):
    # ruleid: sift-no-pin-in-key-derivation
    wrap_master_key(master_key, pin)

    # ruleid: sift-no-pin-in-key-derivation
    unwrap_master_key(wrapped, pin)

    # A PIN read off a request body is still a PIN.
    # ruleid: sift-no-pin-in-key-derivation
    wrap_master_key(master_key, body.pin)

    # ruleid: sift-no-pin-in-key-derivation
    rewrap_master_key(wrapped, old_password, self.pin)

    # ruleid: sift-no-pin-in-key-derivation
    _wrapping_key(pin, salt)

    # A local named for the PIN, however it got there.
    user_pin = body.pin
    # ruleid: sift-no-pin-in-key-derivation
    wrap_master_key(master_key, user_pin)


def pin_in_kdf_correct(master_key, wrapped, password, old_password, new_password, salt):
    # The password is what wraps the key. This is the whole design, and it must not trip.
    # ok: sift-no-pin-in-key-derivation
    wrap_master_key(master_key, password)

    # ok: sift-no-pin-in-key-derivation
    unwrap_master_key(wrapped, password)

    # ok: sift-no-pin-in-key-derivation
    rewrap_master_key(wrapped, old_password, new_password)

    # ok: sift-no-pin-in-key-derivation
    _wrapping_key(password, salt)

    # The PIN is hashed for its own verification, which is a hash and not a key derivation, and is
    # not one of the wrapping functions. It is allowed.
    # ok: sift-no-pin-in-key-derivation
    hasher.hash(pin)


# --- no file removal outside the delete slice ------------------------------------------------------


def file_removal_violations(path, destination, staging_dir, source):
    # ruleid: sift-no-file-removal-outside-delete-trash
    os.remove(path)

    # ruleid: sift-no-file-removal-outside-delete-trash
    os.unlink(path)

    # ruleid: sift-no-file-removal-outside-delete-trash
    shutil.rmtree(staging_dir)

    # A rename is as destructive as a delete when it lands on something: both of these overwrite
    # whatever is already at the destination, and neither says so.
    # ruleid: sift-no-file-removal-outside-delete-trash
    os.rename(source, destination)

    # ruleid: sift-no-file-removal-outside-delete-trash
    os.replace(source, destination)

    # ruleid: sift-no-file-removal-outside-delete-trash
    shutil.move(source, destination)

    # ruleid: sift-no-file-removal-outside-delete-trash
    path.unlink()

    # ruleid: sift-no-file-removal-outside-delete-trash
    path.unlink(missing_ok=True)

    # ruleid: sift-no-file-removal-outside-delete-trash
    source.rename(destination)

    # ruleid: sift-no-file-removal-outside-delete-trash
    source.replace(destination)

    # ruleid: sift-no-file-removal-outside-delete-trash
    os.rmdir(staging_dir)


def file_removal_correct(asset_id, viewer, deleter, text, template, response):
    # The seam. This is what the rule exists to push callers towards: it proves the folder was
    # handed over read-write, refuses anyone who is not an admin, and moves the file to a bin it
    # can be recovered from.
    # ok: sift-no-file-removal-outside-delete-trash
    await deleter.remove(asset_id, mode="disk", actor=viewer)

    # str.replace shares a name with Path.replace and is used all over the codebase. A rule that
    # fired on it would be suppressed within a week and would then be guarding nothing.
    # ok: sift-no-file-removal-outside-delete-trash
    cleaned = text.replace("-", " ")

    # ok: sift-no-file-removal-outside-delete-trash
    rendered = template.replace("{name}", "clip.mp4")

    # ok: sift-no-file-removal-outside-delete-trash
    body = response.replace("\r\n", "\n")

    return cleaned, rendered, body


# --- sift-no-archive-member-joined-without-confine -----------------------------------


def archive_member_joined(archive, folder):
    for member in archive.infolist():
        # ruleid: sift-no-archive-member-joined-without-confine
        target = folder / member.filename

    for name in archive.namelist():
        # ruleid: sift-no-archive-member-joined-without-confine
        target = folder.joinpath(*name.split("/"))

    for name in archive.namelist():
        # ruleid: sift-no-archive-member-joined-without-confine
        target = os.path.join(folder, name)
    return target


def archive_member_confined(archive, folder, prefix):
    for member in archive.infolist():
        # ok: sift-no-archive-member-joined-without-confine
        target = confine(folder, folder.joinpath(*member.filename.split("/")))

    # A folder joined to a name nobody read out of an archive is not what this rule is about.
    # ok: sift-no-archive-member-joined-without-confine
    staged = folder / "incoming"
    return target, staged
