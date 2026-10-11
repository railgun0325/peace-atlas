#!/usr/bin/env python3
"""Dependency-free local PEACE ATLAS client and explicit Skill installer."""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import shutil
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


SKILL_FILES = ("SKILL.md", "agents/openai.yaml", "references/api.md", "scripts/peace_atlas.py")


class ClientError(Exception):
    pass


def local_base(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.path not in {"", "/"}):
        raise ClientError("Connection must use a plain local HTTP origin.")
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if not loopback or not parsed.port:
        raise ClientError("Only explicit loopback endpoints are supported.")
    return value.rstrip("/")


def load_connection(path):
    file = Path(path)
    info = file.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ClientError("The connection file must be a regular private file, not a link.")
    if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
        raise ClientError("Invalid connection file.")
    if os.name != "nt" and info.st_mode & 0o077:
        raise ClientError("Connection file permissions must be owner-only (0600).")
    value = json.loads(file.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("protocol") != 1
            or not isinstance(value.get("agent_token"), str) or len(value["agent_token"]) < 32
            or not isinstance(value.get("base_url"), str)):
        raise ClientError("Invalid Agent connection; export a new one from the desktop menu.")
    expiry = value.get("expires_at")
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
        raise ClientError("Invalid authorization expiry; export a new connection from the desktop menu.")
    if expiry <= time.time():
        raise ClientError("Agent authorization expired; export a new connection from the desktop menu.")
    return local_base(value["base_url"]), value["agent_token"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ClientError("Agent endpoints must not redirect; no credential was forwarded.")


def request(base, route, token="", payload=None, *, timeout=60):
    base = local_base(base)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Peace-Atlas-Agent"] = token
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    call = urllib.request.Request(base + route, data=data, headers=headers, method="POST" if payload is not None else "GET")
    # Ignore system proxy variables for loopback and forbid credential-bearing
    # redirects. No browser cookie, desktop session or AI key is loaded.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(call, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            error = json.load(exc).get("error", {})
            code = error.get("code", "request_error")
            message = error.get("message", "Request was rejected.")
        except (ValueError, AttributeError):
            code, message = "request_error", "Request was rejected."
        raise ClientError(f"HTTP {exc.code} / {code}: {message}") from None
    except urllib.error.URLError:
        raise ClientError("Local application is unavailable; check that it is open and this grant is current.") from None


def install_skill(destination):
    target = Path(destination).expanduser().absolute()
    if target.name != "peace-atlas":
        raise ClientError("Destination must end with the skill folder name peace-atlas.")
    source = Path(__file__).resolve().parents[1]
    if target.exists():
        raise ClientError("Destination already exists; inspect the current skill before choosing another installation.")
    for relative in SKILL_FILES:
        public = source / relative
        info = public.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ClientError("Installation requires the four regular public Skill files.")
        for folder in public.parents:
            if folder == source:
                break
            if folder.is_symlink() or getattr(folder.lstat(), "st_file_attributes", 0) & 0x400:
                raise ClientError("Public Skill assets must not pass through links.")
    # Claim a new exact target first. Never copy an arbitrary recursive tree:
    # exported credentials, installer results and local scratch files may have
    # been placed next to a skill since its original distribution.
    target.mkdir(parents=True, exist_ok=False)
    owned_dirs, owned_files = [target], []
    try:
        for relative in SKILL_FILES:
            output = target / relative
            if output.parent != target and output.parent not in owned_dirs:
                output.parent.mkdir(exist_ok=False)
                owned_dirs.append(output.parent)
            with output.open("xb") as saved:
                owned_files.append(output)
                with (source / relative).open("rb") as original:
                    shutil.copyfileobj(original, saved)
    except BaseException:
        # Only paths created by this invocation are removed, in reverse order.
        # No recursive deletion, no pre-existing installation or sibling file.
        for file in reversed(owned_files):
            file.unlink(missing_ok=True)
        for folder in reversed(owned_dirs):
            folder.rmdir()
        raise
    return {"installed": str(target), "contains_credentials": False,
            "next": "Check the host's Skills list; loading/automatic selection depends on its configuration."}


def main(argv=None):
    # Machine-readable JSON stays UTF-8 even on Windows consoles whose active
    # code page is GBK. This also makes captured CLI results portable.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", default=os.getenv("PEACE_ATLAS_CONNECTION", ""), help="Private connection file explicitly exported by the desktop user")
    parser.add_argument("--base", default="", help="Explicit loopback origin for public manifest/schema only")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("manifest", "schema"):
        commands.add_parser(name)
    for name in ("read", "preview"):
        command = commands.add_parser(name)
        command.add_argument("operation", help="Operation from schema; classify.start quickly classifies saved account names, handles and bios; review.start performs evidence review")
        group = command.add_mutually_exclusive_group()
        group.add_argument("--json", default="{}")
        group.add_argument("--input", type=Path)
        command.add_argument("--output", type=Path, help="Create a new result/preview file; existing files are not overwritten")
    execute = commands.add_parser("execute")
    execute.add_argument("--preview-file", type=Path, required=True)
    execute.add_argument("--key", required=True, help="Stable operation key; preserve it on network retries")
    execute.add_argument("--confirm", action="store_true", help="Use only after the user authorizes this previewed action")
    install = commands.add_parser("install")
    install.add_argument("--destination", required=True, help="Explicit discovered skill root ending in peace-atlas")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = install_skill(args.destination)
        else:
            if args.connection:
                base, token = load_connection(args.connection)
            elif args.command in {"manifest", "schema"} and args.base:
                base, token = local_base(args.base), ""
            else:
                raise ClientError("Select the private connection file exported by the desktop user with --connection.")
            if args.command in {"manifest", "schema"}:
                result = request(base, "/agents/v1/" + args.command)
            elif args.command == "execute":
                if not args.confirm:
                    raise ClientError("Execution requires the user's authorization of the saved preview and --confirm.")
                preview = json.loads(args.preview_file.read_text(encoding="utf-8"))
                result = request(base, "/agents/v1/execute", token, {
                    "operation": preview["operation"], "arguments": preview["arguments"],
                    "preview_id": preview["preview_id"], "operation_key": args.key, "confirmed": True})
            else:
                arguments = json.loads(args.input.read_text(encoding="utf-8") if args.input else args.json)
                result = request(base, "/agents/v1/" + args.command, token,
                                 {"operation": args.operation, "arguments": arguments})
            if getattr(args, "output", None):
                with args.output.open("x", encoding="utf-8") as output:
                    json.dump(result, output, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ClientError, OSError, ValueError, KeyError) as exc:
        # Connection contents/headers and secret-bearing URLs are never printed.
        message = str(exc) if isinstance(exc, ClientError) else "Unable to read/write the selected local artifact or validate its JSON."
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
