#!/usr/bin/env python3
"""
VM User Provisioning Script (isolated environment)
- Sequentially provisions MULTIPLE hosts: e.g. -l 10.0.0.5 10.0.0.6 10.0.0.7
- Adds 50 users (first-initial + lastname) + scoringengine account
- Windows: admin + RDP privileges, runs uploaded .exe then deletes it
- Linux: full passwordless sudo (NOPASSWD:ALL), runs uploaded binary then deletes it
- All users get password: Strange-Cheese-Stairs1!

Usage: 
python3 provision.py -l 192.168.1.10 --user root --password 'RootPw' --binary ./payload

# Windows target with exe
python3 provision.py -w 192.168.1.20 --user Administrator --password 'AdminPw' --exe ./payload.exe

# Multiple IPs with credential files
python3 provision.py -w 192.168.1.20 192.168.1.21 \
    --user-file users.txt --password-file passwords.txt --exe payload.exe
"""

import argparse
import sys
import os
import random
import io
import time
from pathlib import Path

# Common last names (kept as-is per requirement)
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
    "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts", "Turner", "Phillips", "Evans", "Collins", "Stewart",
    "Morris", "Rogers", "Reed", "Cook", "Morgan", "Bell", "Murphy", "Bailey",
    "Cooper", "Richardson", "Cox", "Howard", "Ward", "Peterson", "Gray", "James",
    "Watson", "Brooks", "Kelly", "Sanders", "Price", "Bennett", "Wood", "Barnes",
    "Ross", "Henderson", "Coleman", "Jenkins", "Perry", "Powell", "Long", "Patterson"
]

# Common first initials ONLY (exclude rare ones like q, x, z)
FIRST_INITIALS = list("abcdefghijklmnoprstuvwy")

# Generic password for all accounts (per requirement)
GENERIC_PASSWORD = "Strange-Cheese-Stairs1!"


def generate_users(count=50):
    """Generate `count` unique usernames in format: <initial><lastname>."""
    users = []
    seen = set()
    last_pool = LAST_NAMES * 5
    random.shuffle(last_pool)

    for last in last_pool:
        if len(users) >= count:
            break
        initials = FIRST_INITIALS[:]
        random.shuffle(initials)
        for init in initials:
            uname = f"{init}{last.lower()}"
            if uname in seen:
                continue
            seen.add(uname)
            users.append((init.upper(), last, uname))
            break

    while len(users) < count:
        last = random.choice(LAST_NAMES)
        init = random.choice(FIRST_INITIALS)
        uname = f"{init}{last.lower()}"
        if uname in seen:
            uname = f"{uname}{random.randint(2, 999)}"
        seen.add(uname)
        users.append((init.upper(), last, uname))

    return users


def read_credentials(args):
    """Load username(s)/password(s) from args or files."""
    usernames = []
    passwords = []

    if args.user:
        usernames = [args.user]
    elif args.user_file:
        usernames = Path(args.user_file).read_text().splitlines()

    if args.password:
        passwords = [args.password]
    elif args.password_file:
        passwords = Path(args.password_file).read_text().splitlines()

    usernames = [u.strip() for u in usernames if u.strip()]
    passwords = [p.strip() for p in passwords if p.strip()]
    return usernames, passwords


# ----------------------------- LINUX -----------------------------

def provision_linux(ip, username, password, users, binary_path):
    """SSH into Linux host, create users with NOPASSWD sudo, run binary, delete it."""
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(ip, username=username, password=password,
                       timeout=15, allow_agent=False, look_for_keys=False)
    except Exception as e:
        print(f"    [!] SSH connection failed: {e}")
        return False

    def run(cmd, check=False):
        stdin, stdout, stderr = client.exec_command(cmd)
        rc = stdout.channel.recv_exit_status()
        err = stderr.read().decode(errors="replace").strip()
        out = stdout.read().decode(errors="replace").strip()
        if check and rc != 0:
            print(f"    [!] cmd failed (rc={rc}): {err}")
        return rc, out, err

    rc, _, _ = run("sudo -n true")
    sudo = "sudo -n " if rc == 0 else f"echo '{password}' | sudo -S "

    # Ensure password auth for SSH (new users need it)
    run(f"{sudo}sh -c \"grep -qE '^PasswordAuthentication yes' /etc/ssh/sshd_config || "
        f"echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config\"")
    run(f"{sudo}systemctl restart ssh 2>/dev/null || {sudo}systemctl restart sshd 2>/dev/null")
    run(f"{sudo}mkdir -p /etc/sudoers.d")

    created = 0
    for first_initial, last, uname in users:
        fullname = f"{first_initial}. {last}"
        cmd = (
            f"{sudo}useradd -m -c '{fullname}' -s /bin/bash {uname} && "
            f"echo '{uname}:{GENERIC_PASSWORD}' | {sudo}chpasswd && "
            f"echo '{uname} ALL=(ALL) NOPASSWD:ALL' | {sudo}tee /etc/sudoers.d/{uname} >/dev/null && "
            f"{sudo}chmod 440 /etc/sudoers.d/{uname}"
        )
        rc, _, _ = run(cmd)
        if rc == 0:
            created += 1

    run(
        f"{sudo}useradd -m -c 'Scoring Engine' -s /bin/bash scoringengine ; "
        f"echo 'scoringengine:{GENERIC_PASSWORD}' | {sudo}chpasswd && "
        f"echo 'scoringengine ALL=(ALL) NOPASSWD:ALL' | {sudo}tee /etc/sudoers.d/scoringengine >/dev/null && "
        f"{sudo}chmod 440 /etc/sudoers.d/scoringengine"
    )

    print(f"    [+] Created {created}/50 users + scoringengine")

    if binary_path:
        remote = "/tmp/.provision_bin"
        try:
            sftp = client.open_sftp()
            sftp.put(binary_path, remote)
            sftp.chmod(remote, 0o755)
            sftp.close()
            print(f"    [*] Running binary ...")
            rc, _, err = run(f"{sudo}{remote}")
            print(f"    [*] Binary exit={rc}" + (f" stderr={err[:200]}" if err else ""))
        except Exception as e:
            print(f"    [!] Binary upload/run failed: {e}")
        finally:
            run(f"{sudo}rm -f {remote}")

    client.close()
    return True


# ----------------------------- WINDOWS -----------------------------

def provision_windows(ip, username, password, users, exe_path):
    """Add users via SMB, run batch + optional exe via atexec, then clean up."""
    try:
        from impacket.smbconnection import SMBConnection
    except ImportError:
        print("    [!] impacket not installed (pip install impacket)")
        return False

    try:
        smb = SMBConnection(ip, ip, timeout=15)
        smb.login(username, password)
    except Exception as e:
        print(f"    [!] SMB login failed: {e}")
        return False

    lines = ["@echo off", "setlocal"]
    for first_initial, last, uname in users:
        fullname = f"{first_initial}. {last}"
        lines.append(f'net user {uname} "{GENERIC_PASSWORD}" /add /fullname:"{fullname}" /y')
        lines.append(f'net localgroup Administrators {uname} /add')
        lines.append(f'net localgroup "Remote Desktop Users" {uname} /add')
    lines.append(f'net user scoringengine "{GENERIC_PASSWORD}" /add /fullname:"Scoring Engine" /y')
    lines.append('net localgroup Administrators scoringengine /add')
    lines.append('net localgroup "Remote Desktop Users" scoringengine /add')
    lines.append("endlocal")
    batch = "\r\n".join(lines) + "\r\n"

    rdp_enable = (
        '@echo off\r\n'
        'reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server" '
        '/v fDenyTSConnections /t REG_DWORD /d 0 /f\r\n'
        'netsh advfirewall firewall set rule group="remote desktop" new enable=Yes\r\n'
    )

    try:
        smb.putFile("\\Windows\\Temp\\provision_users.bat", io.BytesIO(batch.encode("utf-8")))
        smb.putFile("\\Windows\\Temp\\enable_rdp.bat", io.BytesIO(rdp_enable.encode("utf-8")))
    except Exception as e:
        print(f"    [!] Batch upload failed: {e}")
        smb.close()
        return False

    exe_uploaded = False
    if exe_path and os.path.isfile(exe_path):
        try:
            with open(exe_path, "rb") as f:
                smb.putFile("\\Windows\\Temp\\provision_payload.exe", f)
            exe_uploaded = True
        except Exception as e:
            print(f"    [!] Exe upload failed: {e}")

    smb.close()

    def run_remote(cmd):
        import subprocess
        args = [
            sys.executable, "-m", "impacket.examples.atexec",
            f"{username}:{password}@{ip}", cmd,
        ]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                print(f"    [!] atexec failed: {r.stderr.strip()[:300]}")
            return r.returncode == 0
        except Exception as e:
            print(f"    [!] atexec exception: {e}")
            return False

    print("    [*] Executing provisioning batch ...")
    run_remote("C:\\Windows\\Temp\\provision_users.bat")
    run_remote("C:\\Windows\\Temp\\enable_rdp.bat")

    if exe_uploaded:
        print("    [*] Running payload exe ...")
        run_remote("C:\\Windows\\Temp\\provision_payload.exe")

    print("    [*] Cleaning up ...")
    run_remote("cmd /c del /f /q C:\\Windows\\Temp\\provision_users.bat")
    run_remote("cmd /c del /f /q C:\\Windows\\Temp\\enable_rdp.bat")
    if exe_uploaded:
        run_remote("cmd /c del /f /q C:\\Windows\\Temp\\provision_payload.exe")

    print("    [+] Windows provisioning complete")
    return True


# ----------------------------- MAIN -----------------------------

def provision_one_host(ip, args, users, usernames, passwords):
    """Try every (user,password) combo on one host. Returns True if provisioned."""
    for uname in usernames:
        for pw in passwords:
            print(f"    [*] Trying {uname}@{ip}")
            try:
                if args.linux:
                    ok = provision_linux(ip, uname, pw, users, args.binary)
                else:
                    ok = provision_windows(ip, uname, pw, users, args.exe)
            except Exception as e:
                print(f"    [!] Error: {e}")
                ok = False
            if ok:
                return True
    return False


def main():
    p = argparse.ArgumentParser(
        description="Provision 50 users + scoringengine on one or more Windows/Linux VMs (sequentially)."
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-w", "--windows", action="store_true", help="Target is Windows")
    g.add_argument("-l", "--linux", action="store_true", help="Target is Linux")

    p.add_argument("ips", nargs="+", help="One or more target IPs, space-separated")
    p.add_argument("--user", help="Auth username")
    p.add_argument("--password", help="Auth password")
    p.add_argument("--user-file", help="File with usernames (one per line)")
    p.add_argument("--password-file", help="File with passwords (one per line)")
    p.add_argument("--exe", help="Windows .exe to run then delete")
    p.add_argument("--binary", help="Linux binary to run then delete")

    args = p.parse_args()

    if args.windows and not args.exe:
        print("[*] Note: no --exe; skipping payload step on Windows")
    if args.linux and not args.binary:
        print("[*] Note: no --binary; skipping payload step on Linux")

    usernames, passwords = read_credentials(args)
    if not usernames or not passwords:
        print("[!] Need credentials (--user/--password or file variants)")
        sys.exit(1)

    users = generate_users(50)
    print(f"[*] Generated 50 users. Sample: {', '.join(u[2] for u in users[:5])}, ...")
    print(f"[*] Password for ALL accounts: {GENERIC_PASSWORD}")
    print(f"[*] Target hosts ({len(args.ips)}): {', '.join(args.ips)}")
    print(f"[*] Platform: {'Linux' if args.linux else 'Windows'}")
    print()

    results = {}
    start = time.time()

    for idx, ip in enumerate(args.ips, start=1):
        print(f"=== [{idx}/{len(args.ips)}] {ip} ===")
        host_start = time.time()
        try:
            ok = provision_one_host(ip, args, users, usernames, passwords)
        except KeyboardInterrupt:
            print(f"\n[!] Interrupted while processing {ip}")
            results[ip] = "interrupted"
            break
        except Exception as e:
            print(f"    [!] Unexpected error on {ip}: {e}")
            ok = False
        results[ip] = "OK" if ok else "FAILED"
        elapsed = time.time() - host_start
        status = "SUCCESS" if ok else "FAILED"
        print(f"=== [{idx}/{len(args.ips)}] {ip}: {status} ({elapsed:.1f}s) ===\n")

    total = time.time() - start
    print("\n========== SUMMARY ==========")
    for ip, status in results.items():
        marker = "✓" if status == "OK" else "✗"
        print(f"  {marker} {ip:20s} {status}")
    ok_count = sum(1 for s in results.values() if s == "OK")
    print(f"\n  {ok_count}/{len(args.ips)} hosts provisioned successfully")
    print(f"  Total time: {total:.1f}s")
    print("=============================")

    sys.exit(0 if ok_count == len(args.ips) else 1)


if __name__ == "__main__":
    main()