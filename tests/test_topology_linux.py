import os
from corpsman.topology.linux import system_devices

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "linux")
LUKS = os.path.join(FIX, "luks-lvm")
PLAIN = os.path.join(FIX, "plain-sata")
MDRAID = os.path.join(FIX, "mdraid")


def test_resolves_root_through_lvm_and_luks_to_the_physical_disk():
    # / is /dev/mapper/vg-root = dm-1 -> dm-0 -> sda2 -> sda. Assert the
    # REASON is "/", not merely that sda appears: /boot reaches sda via
    # sda1 independently, so bare membership passes even when the chain
    # walk is broken.
    sysd = system_devices(root=LUKS)
    assert "sda" in sysd
    assert "/" in sysd["sda"]


def test_reason_names_the_mountpoint():
    sysd = system_devices(root=LUKS)
    assert "/" in sysd["sda"]


def test_boot_on_a_partition_is_also_caught():
    assert "/boot" in system_devices(root=LUKS)["sda"]


def test_swap_on_a_mapper_device_is_caught():
    assert any("swap" in r for r in system_devices(root=LUKS)["sda"])


def test_unrelated_disk_is_not_flagged():
    # sdb holds nothing. Flagging it would train operators to override.
    assert "sdb" not in system_devices(root=LUKS)


def test_tmpfs_does_not_produce_a_device():
    for reasons in system_devices(root=LUKS).values():
        assert "/run" not in reasons


def test_plain_fixture_root_is_on_a_different_disk():
    # / is on nvme0n1p2, which is not enumerated in this fixture at all.
    assert "sda" not in system_devices(root=PLAIN)


def test_resolution_terminates_despite_the_holders_slaves_cycle():
    # dm-0/holders/dm-1 and dm-1/slaves/dm-0 are reciprocal symlinks. This is
    # not a fixture artefact -- the kernel creates them for any dm device
    # stacked on another, and they exist on real machines right now. A walk
    # that follows both edge types without tracking what it has visited spins
    # forever, and it would do so while an operator waits to find out whether
    # a disk is safe to wipe.
    #
    # signal.alarm gives this test teeth: without the `seen` guard it would
    # hang the suite rather than fail it, and a hanging test reports nothing.
    import signal

    def _timeout(signum, frame):
        raise AssertionError("system_devices did not terminate: cycle not guarded")

    old = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(10)
    try:
        system_devices(root=LUKS)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def test_tmpfs_major_zero_does_not_resolve_to_a_block_device():
    # /run is 0:22. Major 0 is the kernel's anonymous-bdev range, so there is
    # no sys/dev/block entry for it. Absence must be an expected outcome, not
    # an error.
    sysd = system_devices(root=LUKS)
    assert all(isinstance(v, list) for v in sysd.values())


def test_unreadable_mountinfo_raises_rather_than_returning_empty():
    # Failing open here would mark every disk as safe to destroy.
    import pytest
    from corpsman.topology.linux import TopologyError
    with pytest.raises(TopologyError):
        system_devices(root=os.path.join(FIX, "does-not-exist"))


def test_partition_on_mdraid_reaches_every_member_disk():
    # / on md0p1 must flag BOTH array members. Stopping at the virtual
    # device leaves every member disk destroyable.
    sysd = system_devices(root=MDRAID)
    assert "sdc" in sysd
    assert "sdd" in sysd
