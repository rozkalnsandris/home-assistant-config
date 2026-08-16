from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SILDITAJS = ROOT / "packages" / "silditajs.yaml"
SCHEDULER = ROOT / "packages" / "heater_scheduler.yaml"


def test_recurring_schedule_has_one_source_authority() -> None:
    legacy = SILDITAJS.read_text(encoding="utf-8")
    scheduler = SCHEDULER.read_text(encoding="utf-8")

    assert "scheduler.add" in scheduler
    assert "heater_sched_save" in scheduler

    for legacy_schedule_marker in (
        "silditajs_grafiks",
        "silditajs_grafiks_on",
        "silditajs_grafiks_off",
        "silditajs_ieslegt",
        "silditajs_izslegt",
    ):
        assert legacy_schedule_marker not in legacy


def test_independent_auto_off_timer_is_preserved() -> None:
    legacy = SILDITAJS.read_text(encoding="utf-8")

    assert "silditajs_taimeris" in legacy
    assert "silditajs_taimeris_min" in legacy
    assert "silditajs_auto_off" in legacy
    assert "heater_switch_entity" in legacy
    assert "switch.turn_off" in legacy
