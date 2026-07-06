from stratus.jobs.audit_health import invalid_event_probability_sums


def test_probability_sum_health_check() -> None:
    assert invalid_event_probability_sums(
        {"good": [0.2, 0.3, 0.5], "bad": [0.2, 0.3]}
    ) == ["bad"]
