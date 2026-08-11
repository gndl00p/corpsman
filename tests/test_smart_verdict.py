from corpsman.smart.parse import SmartData
from corpsman.smart.verdict import assess, THRESHOLDS
from corpsman.types import (
    HEALTH_REUSE, HEALTH_SCRATCH_ONLY, HEALTH_SCRAP, HEALTH_UNKNOWN,
)


def sd(overrides=None):
    # NOTE: the brief's helper signature was `def sd(**attrs)` called as
    # `sd({5: 3})`, which is invalid Python -- dict-unpacking into
    # **kwargs requires string keys, and attribute ids here are ints.
    # Fixed to take the overrides dict positionally; every test name,
    # assertion, and comment below is transcribed verbatim from the brief.
    base = {5: 0, 187: 0, 188: 0, 197: 0, 198: 0, 199: 0}
    if overrides:
        base.update(overrides)
    return SmartData(available=True, overall_passed=True, attrs=base,
                     power_on_hours=1000)


def test_clean_drive_is_reuse():
    assert assess(sd()).health == HEALTH_REUSE


def test_unavailable_smart_is_unknown_never_reuse():
    v = assess(SmartData(available=False, unreadable_reason="USB bridge"))
    assert v.health == HEALTH_UNKNOWN
    assert v.health != HEALTH_REUSE


def test_failed_overall_status_is_scrap():
    s = sd()
    s.overall_passed = False
    assert assess(s).health == HEALTH_SCRAP


def test_a_few_reallocated_sectors_is_not_scrap():
    # A 10 TB drive that remapped three sectors in year one and none since
    # is a working drive. Condemning it would bin most used inventory.
    assert assess(sd({5: 3})).health == HEALTH_SCRATCH_ONLY


def test_many_reallocated_sectors_is_scrap():
    assert assess(sd({5: 128})).health == HEALTH_SCRAP


def test_any_pending_sectors_growing_is_scrap():
    prior = {5: 0, 197: 4}
    assert assess(sd({197: 41}), prior=prior).health == HEALTH_SCRAP


def test_growth_below_threshold_is_still_scrap():
    # Isolates the growth-comparison branch from the threshold branch.
    # 197=6 is below THRESHOLDS[197]=8, so only the prior comparison
    # (6 - 3 = 3, at or above _GROWTH_FLOOR) can produce SCRAP here. The
    # test above uses a value (41) that already clears the threshold on its
    # own, so removing the growth comparison entirely still leaves it
    # passing -- it never actually exercises this path. This test closes
    # that gap.
    prior = {197: 3}
    assert assess(sd({197: 6}), prior=prior).health == HEALTH_SCRAP


def test_static_low_pending_is_scratch_only():
    prior = {197: 2}
    assert assess(sd({197: 2}), prior=prior).health == HEALTH_SCRATCH_ONLY


def test_crc_errors_alone_do_not_condemn_the_drive():
    # Attribute 199 is an interface fault. Counting it against the drive is
    # how a good disk gets binned while the bad cable stays in the machine.
    v = assess(sd({199: 4831}))
    assert v.health == HEALTH_REUSE
    assert v.cabling is not None
    assert "199" in v.cabling or "CRC" in v.cabling


def test_reported_uncorrect_is_scratch_only():
    # Below THRESHOLDS[187] = 8.
    assert assess(sd({187: 5})).health == HEALTH_SCRATCH_ONLY


def test_reported_uncorrect_above_threshold_is_scrap():
    # 187 means the host already received data the drive could not
    # correct -- a realized failure, stronger than a successful remap. It
    # thresholds tighter than 5 for the same reason 197/198 do.
    assert assess(sd({187: 8})).health == HEALTH_SCRAP


def test_command_timeout_is_informational_and_never_scores():
    # 188 packs three 16-bit sub-counters into a 48-bit raw on many vendors,
    # so a benign drive can report millions. Scoring it would bin healthy
    # inventory. It must appear in the reasons but not move the verdict.
    v = assess(sd({188: 4295032833}))
    assert v.health == HEALTH_REUSE
    assert any("188" in r for r in v.reasons)


def test_unreported_self_assessment_is_not_a_failure():
    # overall_passed is tri-state. None means the drive reported no
    # self-assessment, which is common on SAS and is neither pass nor fail.
    # Collapsing it to False would condemn a drive for a reason unrelated to
    # its health.
    s = sd()
    s.overall_passed = None
    assert assess(s).health == HEALTH_REUSE


def test_reasons_name_the_attributes_that_fired():
    v = assess(sd({5: 128}))
    assert any("5" in r for r in v.reasons)


def test_offline_uncorrectable_above_threshold_is_scrap():
    assert assess(sd({198: 20})).health == HEALTH_SCRAP


def test_growth_of_one_sector_is_not_scrap():
    # A delta of exactly one is normal aging noise, not evidence of active
    # failure -- the same class of mistake as condemning any nonzero count.
    # `prior` may be minutes or months old and assess() has no timestamp,
    # so only a delta at or above _GROWTH_FLOOR escalates to SCRAP.
    prior = {197: 2}
    assert assess(sd({197: 3}), prior=prior).health == HEALTH_SCRATCH_ONLY


def test_pending_sectors_threshold_is_tighter_than_reallocated():
    # 197/198 mean the drive cannot read those sectors now; 5 means it
    # already handled them successfully. The sharper signal must not
    # tolerate more.
    assert THRESHOLDS[197] < THRESHOLDS[5]
    assert THRESHOLDS[198] < THRESHOLDS[5]
    assert THRESHOLDS[187] < THRESHOLDS[5]


def test_table_without_any_predictive_attributes_is_unknown_not_reuse():
    # "No predictive attributes above zero" must not be reported for a
    # drive where they were never reported at all.
    s = SmartData(available=True, overall_passed=True,
                  attrs={9: 1000, 194: 35}, power_on_hours=1000)
    assert assess(s).health == HEALTH_UNKNOWN
