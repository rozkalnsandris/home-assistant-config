import json

from tools.diagnose_heater_scheduler_target_shape import (
    BLOCKED,
    DIAGNOSTIC_COMPLETE,
    build_report,
    classify_scheduler_shape,
)


def exact_config() -> dict:
    return {
        "script": {
            "heater_sched_save": {
                "sequence": [
                    {
                        "variables": {
                            "heater_entity": "switch.private_fixture",
                            "weekdays": "{{ fixture }}",
                        }
                    },
                    {
                        "service": "scheduler.add",
                        "data": {"timeslots": "{{ fixture }}"},
                    },
                ]
            }
        }
    }


def test_exact_expected_shape() -> None:
    shape = classify_scheduler_shape(exact_config())

    assert shape["shape_reason"] == "EXPECTED_SCHEDULER_TARGET_SHAPE_EXACT"
    assert shape["expected_binding_key_occurrence_count"] == 1
    assert shape["expected_binding_value_is_string_count"] == 1
    assert shape["expected_binding_value_entity_scalar_count"] == 1
    assert shape["scheduler_add_step_count"] == 1


def test_missing_expected_binding_variable() -> None:
    config = exact_config()
    del config["script"]["heater_sched_save"]["sequence"][0]["variables"][
        "heater_entity"
    ]

    shape = classify_scheduler_shape(config)

    assert shape["shape_reason"] == "EXPECTED_BINDING_VARIABLE_MISSING"
    assert shape["expected_binding_key_occurrence_count"] == 0


def test_non_scalar_binding_value() -> None:
    config = exact_config()
    config["script"]["heater_sched_save"]["sequence"][0]["variables"][
        "heater_entity"
    ] = {"fixture": True}

    shape = classify_scheduler_shape(config)

    assert shape["shape_reason"] == "EXPECTED_BINDING_VALUE_SHAPE_INVALID"
    assert shape["expected_binding_value_is_string_count"] == 0
    assert shape["expected_binding_value_entity_scalar_count"] == 0


def test_duplicate_binding_variable_steps_are_ambiguous() -> None:
    config = exact_config()
    config["script"]["heater_sched_save"]["sequence"].insert(
        1,
        {"variables": {"heater_entity": "switch.second_private_fixture"}},
    )

    shape = classify_scheduler_shape(config)

    assert shape["shape_reason"] == "EXPECTED_BINDING_VARIABLE_AMBIGUOUS"
    assert shape["expected_binding_key_occurrence_count"] == 2


def test_missing_scheduler_add_step() -> None:
    config = exact_config()
    config["script"]["heater_sched_save"]["sequence"] = config["script"][
        "heater_sched_save"
    ]["sequence"][:1]

    shape = classify_scheduler_shape(config)

    assert shape["shape_reason"] == "SCHEDULER_ADD_STEP_NOT_EXACT"
    assert shape["scheduler_add_step_count"] == 0


def test_duplicate_scheduler_add_steps_are_ambiguous() -> None:
    config = exact_config()
    config["script"]["heater_sched_save"]["sequence"].append(
        {"action": "scheduler.add", "data": {"timeslots": "{{ fixture }}"}}
    )

    shape = classify_scheduler_shape(config)

    assert shape["shape_reason"] == "SCHEDULER_ADD_STEP_NOT_EXACT"
    assert shape["scheduler_add_step_count"] == 2


def test_report_is_sanitized_and_complete() -> None:
    probe = {
        "runtime_error": False,
        "installed_home_assistant_yaml_loader_used": True,
        "home_assistant_secret_resolution_used": True,
        "shape": classify_scheduler_shape(exact_config()),
    }

    report = build_report(probe, expected="2026.8.2", running="2026.8.2")
    encoded = json.dumps(report, sort_keys=True)

    assert report["decision"] == DIAGNOSTIC_COMPLETE
    assert all(value is False for value in report["privacy"].values())
    assert all(value is False for value in report["mutation"].values())
    assert "switch.private_fixture" not in encoded
    assert "switch.second_private_fixture" not in encoded


def test_version_mismatch_blocks() -> None:
    probe = {
        "runtime_error": False,
        "installed_home_assistant_yaml_loader_used": True,
        "home_assistant_secret_resolution_used": True,
        "shape": classify_scheduler_shape(exact_config()),
    }

    report = build_report(probe, expected="2026.8.2", running="2026.8.3")

    assert report["decision"] == BLOCKED
    assert report["reasons"] == ["HOME_ASSISTANT_VERSION_MISMATCH"]
