from datetime import UTC, datetime

from stratus.services.model_catalog import STATION_REGIONAL_MODELS, descriptor_for


def test_every_station_regional_model_has_a_point_in_time_descriptor() -> None:
    models = {
        model for station_models in STATION_REGIONAL_MODELS.values() for model in station_models
    }
    for model in models:
        descriptor = descriptor_for(
            model,
            init_time=datetime(2026, 7, 12, tzinfo=UTC),
            source_url="https://example.test/forecast",
        )
        assert descriptor.model == model
        assert descriptor.shadow_only is True
        assert descriptor.enabled is False
