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
    # stacked on another, and they exist on real machines right now.
    #
    # This proves resolution terminates in general; it does NOT exercise the
    # `seen` guard as load-bearing. The current walk only follows `slaves`,
    # which forms a DAG by kernel design, so it would terminate here even
    # without the guard -- see the comment on _physical_ancestors() in
    # topology/linux.py for why the guard is kept anyway (defence for a
    # future walker that also follows `holders`, and for corrupted/
    # adversarial /sys content).
    #
    # signal.alarm still gives this test teeth against any regression that
    # DOES make resolution loop -- a hanging test would otherwise report
    # nothing rather than failing.
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
    # /run is 0:22 in the LUKS fixture's mountinfo. Major 0 is the kernel's
    # anonymous-bdev range, so there is no sys/dev/block entry for it, and
    # resolution must return "" -- not fall back to guessing a device name --
    # so the tmpfs mount contributes no device to the map at all.
    from corpsman.topology.linux import _name_from_majmin
    assert _name_from_majmin(LUKS, "0:22") == ""


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
