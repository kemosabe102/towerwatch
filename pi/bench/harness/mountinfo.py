"""Helpers for reading and interpreting /proc/self/mountinfo."""

from pathlib import Path


def bind_mounts_sharing_device(mount_point: str) -> list[str]:
    """Return other mount points backed by the same device as mount_point.

    A read-only remount fails if any other active mount (typically a bind mount
    like /var/lib/tailscale → /opt/towerwatch/data/tailscale-state/) holds open
    write handles on the same underlying device. Detect this up-front so we can
    skip cleanly instead of failing with "mount point is busy".
    
    Args:
        mount_point: Path to the mount point to check (e.g. '/opt/towerwatch/data')
    
    Returns:
        List of other mount point paths sharing the same device
    """
    try:
        entries = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return []
    
    # Find the device number (major:minor) for mount_point
    data_dev = None
    for line in entries:
        # mountinfo format: <id> <parent> <major:minor> <root> <mount-point> ...
        parts = line.split()
        if len(parts) >= 5 and parts[4] == mount_point:
            data_dev = parts[2]
            break
    
    if not data_dev:
        return []
    
    # Find all other mounts using the same device
    shared = []
    for line in entries:
        parts = line.split()
        if len(parts) >= 5 and parts[2] == data_dev and parts[4] != mount_point:
            shared.append(parts[4])
    
    return shared
