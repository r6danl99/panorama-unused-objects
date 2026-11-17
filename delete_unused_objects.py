#!/usr/bin/env python3
"""Delete unused shared address objects / groups from Panorama.

Reads the list produced by find_unused_objects.py, where each item is

    ("panorama_hostname", "object_name", "address" | "address-group")

and, for each Panorama, deletes every listed object through the XML API
(pan-python) and then commits the change to Panorama. Address groups are
deleted before address objects. The commit is to Panorama only; nothing is
pushed to managed devices.

Authentication (per Panorama, first match wins):
    1. --api-key, or the PANORAMA_API_KEY environment variable
    2. --username (password from PANORAMA_PASSWORD or an interactive prompt)

Usage:
    python delete_unused_objects.py [unused_objects.txt] [--dry-run] [--yes]
"""

import argparse
import ast
import getpass
import os
import ssl
import sys
from collections import OrderedDict

import pan.xapi

KINDS = ("address-group", "address")  # deletion order: groups first


def load_objects(path):
    """Return {panorama: [(name, kind), ...]} from the unused objects file."""
    with open(path, encoding="utf-8") as handle:
        rows = ast.literal_eval(handle.read())

    by_panorama = OrderedDict()
    for row in rows:
        if len(row) != 3 or row[2] not in KINDS:
            raise ValueError(
                f"bad entry {row!r}: expected (panorama, object_name, "
                f"'address' | 'address-group')"
            )
        panorama, name, kind = row
        by_panorama.setdefault(panorama, []).append((name, kind))

    for objects in by_panorama.values():
        objects.sort(key=lambda item: KINDS.index(item[1]))
    return by_panorama


def parse_host_map(pairs):
    host_map = {}
    for pair in pairs:
        name, sep, address = pair.partition("=")
        if not sep or not name or not address:
            raise SystemExit(f"--host-map expects NAME=ADDRESS, got '{pair}'")
        host_map[name] = address
    return host_map


def object_xpath(name, kind):
    quote = '"' if "'" in name else "'"
    return f"/config/shared/{kind}/entry[@name={quote}{name}{quote}]"


def connect(host, args, password):
    kwargs = {"hostname": host, "timeout": args.timeout}
    if args.insecure:
        kwargs["ssl_context"] = ssl._create_unverified_context()
    if args.api_key:
        kwargs["api_key"] = args.api_key
    else:
        kwargs["api_username"] = args.username
        kwargs["api_password"] = password
    xapi = pan.xapi.PanXapi(**kwargs)
    if not args.api_key:
        xapi.keygen()
    return xapi


def process_panorama(panorama, host, objects, args, password):
    """Delete objects on one Panorama and commit. Returns (deleted, failed)."""
    print(f"\n{panorama} ({host}): {len(objects)} objects")

    if args.dry_run:
        for name, kind in objects:
            print(f"  [dry-run] would delete {kind} '{name}'")
        return 0, 0

    try:
        xapi = connect(host, args, password)
    except pan.xapi.PanXapiError as exc:
        print(f"  ERROR: cannot connect: {exc}", file=sys.stderr)
        return 0, len(objects)

    deleted = failed = 0
    for name, kind in objects:
        try:
            xapi.delete(xpath=object_xpath(name, kind))
            deleted += 1
            print(f"  deleted {kind} '{name}'")
        except pan.xapi.PanXapiError as exc:
            failed += 1
            print(f"  FAILED  {kind} '{name}': {exc}", file=sys.stderr)

    if deleted:
        print(f"  committing {deleted} deletions to Panorama ...")
        try:
            xapi.commit(
                cmd="<commit><description>Remove unused shared address "
                "objects</description></commit>",
                sync=True,
                interval=5,
                timeout=args.commit_timeout,
            )
            print(f"  commit: {xapi.status_detail or xapi.status}")
        except pan.xapi.PanXapiError as exc:
            print(
                f"  ERROR: commit failed, deletions remain in the candidate "
                f"config: {exc}",
                file=sys.stderr,
            )
            failed += 1
    else:
        print("  nothing deleted, skipping commit")
    return deleted, failed


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "input",
        nargs="?",
        default="unused_objects.txt",
        help="file written by find_unused_objects.py (default: %(default)s)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("PANORAMA_API_KEY"),
        help="API key (default: $PANORAMA_API_KEY)",
    )
    parser.add_argument("--username", help="username, if not using an API key")
    parser.add_argument(
        "--host-map",
        action="append",
        default=[],
        metavar="NAME=ADDRESS",
        help="connect to ADDRESS (IP or FQDN) for the Panorama called NAME in "
        "the input file; repeatable",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="do not verify the Panorama TLS certificate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be deleted without contacting Panorama",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="skip the confirmation prompt"
    )
    parser.add_argument(
        "--timeout", type=int, default=60, help="API timeout in seconds"
    )
    parser.add_argument(
        "--commit-timeout",
        type=int,
        default=1800,
        help="seconds to wait for each commit (default: %(default)s)",
    )
    args = parser.parse_args()

    try:
        by_panorama = load_objects(args.input)
    except (OSError, SyntaxError, ValueError) as exc:
        raise SystemExit(f"Cannot read {args.input}: {exc}")
    if not by_panorama:
        print("No objects to delete.")
        return 0

    host_map = parse_host_map(args.host_map)
    total = sum(len(objects) for objects in by_panorama.values())
    print(f"{total} objects to delete across {len(by_panorama)} Panorama(s):")
    for panorama, objects in by_panorama.items():
        print(f"  {panorama}: {len(objects)}")

    password = None
    if not args.dry_run:
        if not args.api_key and not args.username:
            raise SystemExit(
                "Provide --api-key / $PANORAMA_API_KEY, or --username."
            )
        if not args.api_key:
            password = os.environ.get("PANORAMA_PASSWORD") or getpass.getpass(
                f"Password for {args.username}: "
            )
        if not args.yes:
            answer = input(
                "Delete these objects and commit on each Panorama? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return 1

    total_deleted = total_failed = 0
    for panorama, objects in by_panorama.items():
        host = host_map.get(panorama, panorama)
        deleted, failed = process_panorama(
            panorama, host, objects, args, password
        )
        total_deleted += deleted
        total_failed += failed

    if not args.dry_run:
        print(f"\nDone: {total_deleted} deleted, {total_failed} failed.")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
