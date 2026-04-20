"""Injection primitives for the bench harness."""

import subprocess


def inject_iptables_reject(host_or_subnet: str, chain: str = "OUTPUT") -> None:
    """Insert an iptables REJECT rule for host_or_subnet in chain."""
    subprocess.run(
        ["iptables", "-I", chain, "-d", host_or_subnet, "-j", "REJECT"],
        check=True,
    )


def inject_iptables(*args) -> None:
    """Insert an iptables rule with arbitrary arguments.
    
    Args:
        *args: Arguments to pass to iptables (e.g., "-I", "OUTPUT", "-j", "REJECT")
    """
    subprocess.run(["iptables"] + list(args), check=True)


def inject_tc_latency(iface: str, ms: int) -> None:
    """Add a netem delay of ms milliseconds to iface (replaces existing root qdisc)."""
    subprocess.run(
        ["tc", "qdisc", "replace", "dev", iface, "root", "netem", "delay", f"{ms}ms"],
        check=True,
    )


def inject_tc(*args) -> None:
    """Insert a tc (traffic control) rule with arbitrary arguments.
    
    Args:
        *args: Arguments to pass to tc
    """
    subprocess.run(["tc"] + list(args), check=True)
