#!/usr/bin/env bash
#
# runall.sh — Run a command on one or many Linux/Windows hosts.
#
# Usage:
#   ./runall.sh -o linux  -u root       -p 'P@ss' -c 'whoami; id'  -i 10.0.0.5 10.0.0.6
#   ./runall.sh -o windows -u Administrator -p 'P@ss' -c 'whoami'   -f targets.txt
#   ./runall.sh -o linux  -u root       -H <hash>  -c 'id'         -f targets.txt
#
# Requirements:
#   Linux   : sshpass + ssh  (apt install sshpass)
#   Windows : nxc  (NetExec)  OR  winexe
#
set -u

# ---------- defaults ----------
OS=""
USER=""
PASS=""
HASH=""
CMD=""
FILE=""
IPS=()

# ---------- parse args ----------
usage() {
    cat <<EOF
Usage: $0 -o <linux|windows> -u <user> (-p <pass> | -H <hash>) -c '<command>' (-i <ip...> | -f <file>)
  -o  Target OS: linux or windows
  -u  Username
  -p  Password
  -H  NTLM hash (Windows only)
  -c  Command to run (quote it)
  -i  One or more IPs, space-separated
  -f  File containing IPs, one per line
EOF
    exit 1
}

while getopts ":o:u:p:H:c:i:f:h" opt; do
    case "$opt" in
        o) OS="$OPTARG" ;;
        u) USER="$OPTARG" ;;
        p) PASS="$OPTARG" ;;
        H) HASH="$OPTARG" ;;
        c) CMD="$OPTARG" ;;
        i) shift $((OPTIND-1)); IPS=("$@"); OPTIND=$#; ;;
        f) FILE="$OPTARG" ;;
        h|*) usage ;;
    esac
done

# ---------- validate ----------
[[ -z "$OS"   ]] && usage
[[ -z "$USER" ]] && usage
[[ -z "$CMD"  ]] && usage
[[ -z "$PASS" && -z "$HASH" ]] && usage
[[ ${#IPS[@]} -eq 0 && -z "$FILE" ]] && usage
[[ "$OS" != "linux" && "$OS" != "windows" ]] && { echo "[!] -o must be linux or windows"; exit 1; }

# Build final target list
if [[ -n "$FILE" ]]; then
    [[ ! -f "$FILE" ]] && { echo "[!] File not found: $FILE"; exit 1; }
    while IFS= read -r line; do
        line="${line%%#*}"; line="$(echo "$line" | tr -d '[:space:]')"
        [[ -n "$line" ]] && IPS+=("$line")
    done < "$FILE"
fi

[[ ${#IPS[@]} -eq 0 ]] && { echo "[!] No targets provided"; exit 1; }

# ---------- dependency check ----------
if [[ "$OS" == "linux" ]]; then
    command -v sshpass >/dev/null || { echo "[!] sshpass not installed (apt install sshpass)"; exit 1; }
    command -v ssh     >/dev/null || { echo "[!] ssh not installed"; exit 1; }
else
    if command -v nxc >/dev/null; then
        WIN_TOOL="nxc"
    elif command -v netexec >/dev/null; then
        WIN_TOOL="netexec"
    else
        echo "[!] Need nxc (NetExec) for Windows targets: pip install netexec"; exit 1
    fi
fi

# ---------- run ----------
echo "[*] OS       : $OS"
echo "[*] User     : $USER"
echo "[*] Auth     : $([[ -n "$PASS" ]] && echo password || echo hash)"
echo "[*] Command  : $CMD"
echo "[*] Targets  : ${#IPS[@]}"
echo

ok=0; fail=0

for ip in "${IPS[@]}"; do
    echo "============================================================"
    echo "=== $ip"
    echo "============================================================"

    if [[ "$OS" == "linux" ]]; then
        if sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                -o ConnectTimeout=10 -o LogLevel=ERROR "$USER@$ip" "$CMD" 2>&1; then
            echo "[+] $ip OK"
            ((ok++))
        else
            echo "[-] $ip FAILED (rc=$?)"
            ((fail++))
        fi
    else
        # Windows — use nxc smb with -x (cmd) 
        NXC_ARGS=(smb "$ip" -u "$USER" -x "$CMD")
        if [[ -n "$HASH" ]]; then
            h="$HASH"; [[ "$h" != *:* ]] && h=":$h"
            NXC_ARGS+=(-H "$h")
        else
            NXC_ARGS+=(-p "$PASS")
        fi
        if "$WIN_TOOL" "${NXC_ARGS[@]}" 2>&1; then
            echo "[+] $ip OK"
            ((ok++))
        else
            echo "[-] $ip FAILED (rc=$?)"
            ((fail++))
        fi
    fi
    echo
done

echo "============================================================"
echo "SUMMARY: $ok succeeded, $fail failed, $((ok+fail)) total"
echo "============================================================"

[[ $fail -eq 0 ]] && exit 0 || exit 1