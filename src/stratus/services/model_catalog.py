from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    provider: str
    model: str
    model_version: str
    region: str
    resolution_km: Decimal | None
    run_frequency_hours: Decimal | None
    expected_latency_minutes: int | None
    source_url: str
    license_status: str
    enabled: bool
    shadow_only: bool
    introduced_at: datetime | None = None


_IFS_50R1 = datetime(2026, 5, 12, 6, tzinfo=UTC)


def canonical_model(model: str) -> str:
    if model.startswith("ecmwf_ens_m"):
        return "ecmwf_ens"
    if model.startswith("gefs_m"):
        return "gefs"
    return model


def descriptor_for(model: str, *, init_time: datetime, source_url: str) -> ModelDescriptor:
    model = canonical_model(model)
    if model in {"ecmwf", "ecmwf_ens"}:
        version = "ifs-50r1" if init_time >= _IFS_50R1 else "ifs-49r1"
        return ModelDescriptor(
            provider="ecmwf",
            model=model,
            model_version=version,
            region="global",
            resolution_km=Decimal("9") if model == "ecmwf" else Decimal("18"),
            run_frequency_hours=Decimal("6"),
            expected_latency_minutes=390,
            source_url=source_url,
            license_status="open_meteo_terms",
            enabled=True,
            shadow_only=False,
            introduced_at=_IFS_50R1 if version == "ifs-50r1" else None,
        )
    if model in {"gfs", "gefs"}:
        return ModelDescriptor(
            provider="noaa",
            model=model,
            model_version="operational-unversioned",
            region="global",
            resolution_km=Decimal("13") if model == "gfs" else Decimal("25"),
            run_frequency_hours=Decimal("6"),
            expected_latency_minutes=300,
            source_url=source_url,
            license_status="us_government_open",
            enabled=True,
            shadow_only=False,
        )
    if model == "icon":
        return ModelDescriptor(
            provider="dwd",
            model=model,
            model_version="operational-unversioned",
            region="seamless",
            resolution_km=Decimal("7"),
            run_frequency_hours=Decimal("3"),
            expected_latency_minutes=240,
            source_url=source_url,
            license_status="open_meteo_terms",
            enabled=True,
            shadow_only=False,
        )
    if model == "gem":
        return ModelDescriptor(
            provider="eccc",
            model=model,
            model_version="operational-unversioned",
            region="global",
            resolution_km=Decimal("15"),
            run_frequency_hours=Decimal("6"),
            expected_latency_minutes=300,
            source_url=source_url,
            license_status="open_meteo_terms",
            enabled=True,
            shadow_only=False,
        )
    if model == "nws":
        return ModelDescriptor(
            provider="noaa",
            model=model,
            model_version="ndfd-operational-unversioned",
            region="us",
            resolution_km=Decimal("2.5"),
            run_frequency_hours=Decimal("1"),
            expected_latency_minutes=60,
            source_url=source_url,
            license_status="us_government_open",
            enabled=True,
            shadow_only=False,
        )
    regional = next((item for item in REGIONAL_SHADOW_MODELS if item.model == model), None)
    if regional is not None:
        return ModelDescriptor(
            provider=regional.provider,
            model=regional.model,
            model_version=regional.model_version,
            region=regional.region,
            resolution_km=regional.resolution_km,
            run_frequency_hours=regional.run_frequency_hours,
            expected_latency_minutes=regional.expected_latency_minutes,
            source_url=source_url,
            license_status=regional.license_status,
            enabled=False,
            shadow_only=True,
            introduced_at=regional.introduced_at,
        )
    raise ValueError(f"no point-in-time descriptor for model {model}")


REGIONAL_SHADOW_MODELS: tuple[ModelDescriptor, ...] = (
    ModelDescriptor(
        "noaa",
        "hrrr",
        "operational-unversioned",
        "us-conus",
        Decimal("3"),
        Decimal("1"),
        90,
        "https://open-meteo.com/en/docs/gfs-api",
        "open_meteo_terms",
        False,
        True,
    ),
    ModelDescriptor(
        "noaa",
        "nbm",
        "5.0",
        "us-conus",
        Decimal("2.5"),
        Decimal("1"),
        90,
        "https://vlab.noaa.gov/web/mdl/nbm-documentation",
        "us_government_open",
        False,
        True,
        datetime(2026, 5, 5, tzinfo=UTC),
    ),
    ModelDescriptor(
        "noaa",
        "lamp",
        "operational-unversioned",
        "us-stations",
        None,
        Decimal("1"),
        60,
        "https://vlab.noaa.gov/web/mdl/lamp",
        "us_government_open",
        False,
        True,
    ),
    ModelDescriptor(
        "ukmo",
        "uk-2km",
        "operational-unversioned",
        "uk",
        Decimal("2"),
        Decimal("1"),
        120,
        "https://open-meteo.com/en/docs/ukmo-api",
        "open_meteo_terms",
        False,
        True,
    ),
    ModelDescriptor(
        "meteofrance",
        "arome-france",
        "operational-unversioned",
        "france",
        Decimal("1.5"),
        Decimal("1"),
        120,
        "https://open-meteo.com/en/docs/meteofrance-api",
        "open_meteo_terms",
        False,
        True,
    ),
    ModelDescriptor(
        "dwd",
        "icon-eu",
        "operational-unversioned",
        "europe",
        Decimal("7"),
        Decimal("3"),
        180,
        "https://open-meteo.com/en/docs/dwd-api",
        "open_meteo_terms",
        False,
        True,
    ),
    ModelDescriptor(
        "knmi",
        "harmonie-arome",
        "operational-unversioned",
        "netherlands",
        Decimal("2"),
        Decimal("1"),
        120,
        "https://open-meteo.com/en/docs/knmi-api",
        "open_meteo_terms",
        False,
        True,
    ),
    ModelDescriptor(
        "jma",
        "jma-msm",
        "operational-unversioned",
        "japan",
        Decimal("5"),
        Decimal("3"),
        180,
        "https://open-meteo.com/en/docs/jma-api",
        "open_meteo_terms",
        False,
        True,
    ),
    ModelDescriptor(
        "kma",
        "kma-ldps",
        "operational-unversioned",
        "korea",
        Decimal("1.5"),
        Decimal("6"),
        240,
        "https://open-meteo.com/en/docs/kma-api",
        "open_meteo_terms",
        False,
        True,
    ),
    ModelDescriptor(
        "cma",
        "grapes-global",
        "operational-unversioned",
        "global",
        Decimal("15"),
        Decimal("6"),
        300,
        "https://open-meteo.com/en/docs/cma-api",
        "open_meteo_terms",
        False,
        True,
    ),
    ModelDescriptor(
        "bom",
        "access-g",
        "operational-unversioned",
        "global",
        Decimal("15"),
        Decimal("6"),
        300,
        "https://open-meteo.com/en/docs/bom-api",
        "open_meteo_terms",
        False,
        True,
    ),
    ModelDescriptor(
        "ecmwf",
        "aifs-single",
        "2",
        "global",
        Decimal("25"),
        Decimal("6"),
        390,
        "https://www.ecmwf.int/en/forecasts/datasets/aifs-machine-learning-data",
        "cc-by-4.0",
        False,
        True,
        datetime(2026, 5, 12, tzinfo=UTC),
    ),
    ModelDescriptor(
        "noaa",
        "aigefs",
        "operational-unversioned",
        "global",
        Decimal("25"),
        Decimal("6"),
        300,
        "https://www.weather.gov/media/notification/pdf_2025/scn25-89_AIGFS_AIGEFS_and_HGEFS.pdf",
        "us_government_open",
        False,
        True,
    ),
)


STATION_REGIONAL_MODELS: dict[str, tuple[str, ...]] = {
    "KLGA": ("hrrr", "nbm", "aifs-single"),
    "KMIA": ("hrrr", "nbm", "aifs-single"),
    "KORD": ("hrrr", "nbm", "aifs-single"),
    "KLAX": ("hrrr", "nbm", "aifs-single"),
    "EGLC": ("uk-2km", "icon-eu", "aifs-single"),
    "LFPB": ("arome-france", "icon-eu", "aifs-single"),
    "EHAM": ("harmonie-arome", "icon-eu", "aifs-single"),
    "EDDM": ("icon-eu", "aifs-single"),
    "LEMD": ("icon-eu", "aifs-single"),
    "EFHK": ("icon-eu", "aifs-single"),
    "EPWA": ("icon-eu", "aifs-single"),
    "RJTT": ("jma-msm", "aifs-single"),
    "RKSI": ("kma-ldps", "aifs-single"),
    "RKPK": ("kma-ldps", "aifs-single"),
    "ZSPD": ("grapes-global", "aifs-single"),
    "RCSS": ("grapes-global", "aifs-single"),
    "WSSS": ("grapes-global", "aifs-single"),
    "WMKK": ("grapes-global", "aifs-single"),
    "RPLL": ("grapes-global", "aifs-single"),
    "NZWN": ("access-g", "aifs-single"),
}
