#!/bin/bash
set -euo pipefail

# =============================================================
# Towerwatch Pi disk-prep: grow rootfs + create twdata partition.
# Run once on a fresh Pi OS Lite install, BEFORE scripts/install-pi.sh.
#
#   sudo bash scripts/partition-pi-data.sh
#
# Prerequisites: rootfs auto-expansion was disabled before first
# boot (see README §1 — strip the `resize` token from cmdline.txt).
#
# What this does:
#   1. Grows rootfs (/dev/mmcblk0p2) to 6 GB online — safe via the
#      ext4 EXT4_IOC_RESIZE_FS ioctl (journaled, atomic, power-safe).
#   2. Appends a 1 GB ext4 partition labelled `twdata` at
#      /dev/mmcblk0p3 in the freed space.
#
# Idempotent: each step skips itself if already done. Re-running on
# a fully-prepped Pi is a no-op.
#
# Auto-detects MBR vs GPT partition table. Works on any SD card
# size as long as there's >= 7 GB free at the end of the disk.
# =============================================================

DISK="/dev/mmcblk0"
ROOT_DEV="/dev/mmcblk0p2"
DATA_DEV="/dev/mmcblk0p3"
DATA_LABEL="twdata"
ROOT_TARGET_MB="6000"
DATA_SIZE_MB="1024"

echo "=== Towerwatch disk prep ==="

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo bash scripts/partition-pi-data.sh)"
    exit 1
fi

# --- Sanity: required block devices and tools present ---
if [ ! -b "$DISK" ]; then
    echo "ERROR: $DISK not found. This script expects to run on a Raspberry Pi."
    exit 1
fi
if [ ! -b "$ROOT_DEV" ]; then
    echo "ERROR: $ROOT_DEV not found. Unexpected partition layout."
    exit 1
fi

echo "[1/7] Installing prerequisites (parted, e2fsprogs)..."
apt-get update -qq
apt-get install -y -qq parted e2fsprogs >/dev/null

for tool in parted resize2fs tune2fs e2fsck partprobe blkid; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: required tool '$tool' not on PATH after apt install."
        exit 1
    fi
done

# --- Detect partition table type (MBR vs GPT). parted handles both,
#     but we log it for visibility. ---
PT_TYPE="$(parted -sm "$DISK" print 2>/dev/null | awk -F: 'NR==2 {print $6}')"
echo "  Partition table: ${PT_TYPE:-unknown}"

# --- Verify rootfs has the resize feature flag (it always should, but
#     fail fast with a clear message if not). ---
echo "[2/7] Verifying rootfs supports online resize..."
if ! tune2fs -l "$ROOT_DEV" 2>/dev/null | grep -qE "^Filesystem features:.*resize_inode"; then
    echo "ERROR: $ROOT_DEV is missing the 'resize_inode' feature."
    echo "       Online grow is not safe. Aborting."
    exit 1
fi
echo "  resize_inode feature present."

# --- Helper: read the END (in MB) of a partition from parted -m output. ---
_part_end_mb() {
    local part_num="$1"
    parted -sm "$DISK" unit MB print | awk -F: -v n="$part_num" '
        $1 == n { gsub("MB","",$3); printf "%d\n", $3+0 }
    '
}

# --- Helper: total disk size in MB. ---
_disk_size_mb() {
    parted -sm "$DISK" unit MB print | awk -F: 'NR==2 { gsub("MB","",$2); printf "%d\n", $2+0 }'
}

ROOT_END_MB="$(_part_end_mb 2)"
DISK_SIZE_MB="$(_disk_size_mb)"

if [ -z "$ROOT_END_MB" ] || [ "$ROOT_END_MB" -le 0 ]; then
    echo "ERROR: Could not determine end of $ROOT_DEV from parted output."
    parted -sm "$DISK" unit MB print
    exit 1
fi
if [ -z "$DISK_SIZE_MB" ] || [ "$DISK_SIZE_MB" -le 0 ]; then
    echo "ERROR: Could not determine size of $DISK from parted output."
    exit 1
fi

echo "  Disk total: ${DISK_SIZE_MB} MB"
echo "  Rootfs (p2) ends at: ${ROOT_END_MB} MB"

# --- Step 3: Grow rootfs to ROOT_TARGET_MB if it isn't already. ---
if [ "$ROOT_END_MB" -ge "$ROOT_TARGET_MB" ]; then
    echo "[3/7] Rootfs already >= ${ROOT_TARGET_MB} MB. Skipping grow."
else
    REQUIRED_MB=$((ROOT_TARGET_MB + DATA_SIZE_MB + 32))  # +32 MB safety margin
    if [ "$DISK_SIZE_MB" -lt "$REQUIRED_MB" ]; then
        echo "ERROR: Disk too small. Need >= ${REQUIRED_MB} MB; have ${DISK_SIZE_MB} MB."
        exit 1
    fi

    echo "[3/7] Growing rootfs from ${ROOT_END_MB} MB to ${ROOT_TARGET_MB} MB..."

    # Read-only sanity check on the live FS. e2fsck -n is safe on mounted
    # filesystems and reports issues without modifying anything.
    echo "  Pre-flight: e2fsck -n $ROOT_DEV"
    if ! e2fsck -n "$ROOT_DEV" >/dev/null 2>&1; then
        echo "  WARNING: e2fsck -n reported issues. Proceeding anyway —"
        echo "           online resize is journaled and safe even with minor"
        echo "           inconsistencies, but a full fsck on next reboot is"
        echo "           recommended. Run:  sudo touch /forcefsck && sudo reboot"
    fi

    # Grow the partition. parted's "partition is in use, are you sure?"
    # warning is interactive even with -s, so we feed it Yes via stdin.
    # The new partition end stays well past the existing data, so this
    # is safe — we are extending into unallocated space only.
    printf 'Yes\n' | parted ---pretend-input-tty "$DISK" unit MB resizepart 2 "$ROOT_TARGET_MB"

    # Tell the kernel to re-read the table. "device busy" here is expected
    # and harmless — the kernel updates the size of the busy partition
    # in-place via BLKPG, which resize2fs will see.
    partprobe "$DISK" 2>/dev/null || true
    sleep 1

    # Grow the filesystem. Online — uses EXT4_IOC_RESIZE_FS, journaled.
    echo "  Running resize2fs..."
    resize2fs "$ROOT_DEV"

    # Verify three independent ways.
    NEW_ROOT_END_MB="$(_part_end_mb 2)"
    DF_SIZE_MB="$(df --output=size --block-size=1M "$ROOT_DEV" | tail -1 | tr -d ' M')"
    BLOCK_COUNT="$(tune2fs -l "$ROOT_DEV" | awk -F: '/^Block count:/ {print $2+0}')"
    BLOCK_SIZE="$(tune2fs -l "$ROOT_DEV" | awk -F: '/^Block size:/ {print $2+0}')"
    FS_SIZE_MB=$(( BLOCK_COUNT * BLOCK_SIZE / 1024 / 1024 ))

    # Partition LENGTH (not end position) for the FS-size sanity check.
    # parted reports the start in MB too:
    ROOT_START_MB="$(parted -sm "$DISK" unit MB print | awk -F: '/^2:/ {gsub("MB","",$2); printf "%d\n", $2+0}')"
    PART_LEN_MB=$(( NEW_ROOT_END_MB - ROOT_START_MB ))

    echo "  Verify: parted end=${NEW_ROOT_END_MB} MB | partition length=${PART_LEN_MB} MB | df=${DF_SIZE_MB} MB | tune2fs=${FS_SIZE_MB} MB"

    if [ "$NEW_ROOT_END_MB" -lt "$ROOT_TARGET_MB" ]; then
        echo "ERROR: Partition end (${NEW_ROOT_END_MB} MB) < target (${ROOT_TARGET_MB} MB)."
        exit 1
    fi
    # ext4 metadata overhead is ~3-5% of partition length. The FS should
    # be within 300 MB of the partition length; if it's a lot smaller,
    # resize2fs didn't grow it.
    if [ "$FS_SIZE_MB" -lt $((PART_LEN_MB - 300)) ]; then
        echo "ERROR: Filesystem size (${FS_SIZE_MB} MB) is much smaller than"
        echo "       the partition length (${PART_LEN_MB} MB). resize2fs"
        echo "       may have failed silently. Investigate manually."
        exit 1
    fi
    echo "  Rootfs grown successfully."
fi

# --- Step 4: Idempotent guard for the data partition. ---
if [ -b "$DATA_DEV" ]; then
    EXISTING_LABEL="$(blkid -s LABEL -o value "$DATA_DEV" 2>/dev/null || true)"
    if [ "$EXISTING_LABEL" = "$DATA_LABEL" ]; then
        echo "[4/7] $DATA_DEV already exists with label '$DATA_LABEL'. Done."
        echo
        echo "Final layout:"
        parted -s "$DISK" unit MB print
        exit 0
    fi
    echo "ERROR: $DATA_DEV exists but is labelled '$EXISTING_LABEL' (expected '$DATA_LABEL')."
    echo "       Refusing to overwrite. Inspect with: lsblk -f $DISK"
    exit 1
fi

# --- Step 5: Compute placement for the new data partition. ---
LAST_END_MB="$(_part_end_mb 2)"   # rootfs is now grown
NEW_START_MB="$LAST_END_MB"
NEW_END_MB=$((NEW_START_MB + DATA_SIZE_MB))

if [ "$NEW_END_MB" -gt "$DISK_SIZE_MB" ]; then
    echo "ERROR: Not enough free space. Need end at ${NEW_END_MB} MB; disk is ${DISK_SIZE_MB} MB."
    exit 1
fi

echo "[5/7] Creating $DATA_DEV: ${NEW_START_MB} MB -> ${NEW_END_MB} MB"
parted -s "$DISK" unit MB mkpart primary ext4 "$NEW_START_MB" "$NEW_END_MB"

# Re-read partition table. Same "busy" forgiveness as before, but this
# new partition isn't in use yet so partprobe should succeed cleanly.
partprobe "$DISK" 2>/dev/null || true
sleep 1

if [ ! -b "$DATA_DEV" ]; then
    echo "ERROR: $DATA_DEV did not appear after partprobe."
    echo "       Try rebooting and re-running this script (idempotent)."
    exit 1
fi

echo "[6/7] Formatting $DATA_DEV as ext4 with label '$DATA_LABEL'..."
mkfs.ext4 -F -L "$DATA_LABEL" "$DATA_DEV"

echo "[7/7] Done."
echo
echo "Final layout:"
parted -s "$DISK" unit MB print
echo
echo "Next:"
echo "  sudo bash scripts/install-pi.sh"
