from __future__ import annotations

from dataclasses import dataclass

from stratus.services.probability import CalibrationParams


@dataclass(frozen=True, slots=True)
class CalibrationPeer:
    station_id: int
    params: CalibrationParams
    sample_n: int


@dataclass(frozen=True, slots=True)
class HierarchicalCalibration:
    params: CalibrationParams
    local_sample_n: int
    peer_sample_n: int
    peer_stations: int
    local_weight: float


def partial_pool_calibration(
    local: CalibrationPeer,
    peers: list[CalibrationPeer],
    *,
    prior_samples: int,
    minimum_peer_stations: int = 2,
) -> HierarchicalCalibration | None:
    """Shrink a station calibration toward the same model/kind/horizon peer pool.

    The local estimate remains dominant as its independent calibration history
    grows.  The parent estimate is sample-weighted and excludes the target
    station, preventing a station from counting its own observations twice.
    """

    if prior_samples < 0:
        raise ValueError("prior_samples cannot be negative")
    if minimum_peer_stations < 1:
        raise ValueError("minimum_peer_stations must be positive")
    if local.sample_n <= 0:
        return None

    usable = [peer for peer in peers if peer.sample_n > 0 and peer.station_id != local.station_id]
    peer_station_count = len({peer.station_id for peer in usable})
    peer_sample_n = sum(peer.sample_n for peer in usable)
    if peer_station_count < minimum_peer_stations or peer_sample_n <= 0:
        return None

    def peer_mean(attribute: str) -> float:
        return sum(
            float(getattr(peer.params, attribute)) * peer.sample_n for peer in usable
        ) / peer_sample_n

    local_weight = (
        1.0
        if prior_samples == 0
        else local.sample_n / (local.sample_n + prior_samples)
    )
    parent_weight = 1.0 - local_weight

    def pooled(attribute: str) -> float:
        return (
            local_weight * float(getattr(local.params, attribute))
            + parent_weight * peer_mean(attribute)
        )

    params = CalibrationParams(
        bias_c=pooled("bias_c"),
        sigma_c=max(0.25, pooled("sigma_c")),
        intercept=pooled("intercept"),
        slope=pooled("slope"),
        sigma_intercept=max(0.25, pooled("sigma_intercept")),
        sigma_slope=pooled("sigma_slope"),
        source_bucket=f"hierarchical:{local.params.source_bucket}",
    )
    return HierarchicalCalibration(
        params=params,
        local_sample_n=local.sample_n,
        peer_sample_n=peer_sample_n,
        peer_stations=peer_station_count,
        local_weight=local_weight,
    )
