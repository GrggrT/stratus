"""Read processed weather-model forecasts straight from the Open-Meteo open-data bucket on AWS.

The bucket (s3://openmeteo, CC-BY-4.0) holds the same arrays the Open-Meteo API serves, so
there is no request quota and commercial use is allowed with attribution. Two layouts are used:

- data/<model>/<variable>/chunk_<n>.om       rolling "latest forecast" time series, [ny, nx, time]
- data_run/<model>/<run>/<variable>.om       one model run, [ny, nx, time], kept for 3 months

Only a few kilobytes are fetched per point through HTTP range requests.
"""
import re
import time

import numpy as np
import omfiles
import pandas as pd
import requests

BUCKET = "https://openmeteo.s3.amazonaws.com"

# alias -> (bucket model, radiation variables that add up to global horizontal irradiance)
MODELS = {
    "ifs": ("ecmwf_ifs025", ["shortwave_radiation"]),
    "iconeu": ("dwd_icon_eu", ["direct_radiation", "diffuse_radiation"]),
    "arpege": ("meteofrance_arpege_europe", ["shortwave_radiation"]),
    "gfs": ("ncep_gfs013", ["shortwave_radiation"]),
    "icond2": ("dwd_icon_d2", ["direct_radiation", "diffuse_radiation"]),
    "arome": ("meteofrance_arome_france0025", ["shortwave_radiation"]),
}


class RangeFS:
    """The two methods omfiles needs from a file system, implemented with HTTP range requests."""

    def __init__(self):
        self.session = requests.Session()
        self.requests = 0

    def _request(self, method, url, headers=None):
        for attempt in range(7):
            try:
                r = self.session.request(method, url, headers=headers, timeout=60)
                if r.status_code in (200, 206):
                    return r
                if r.status_code == 404:
                    raise FileNotFoundError(url)
            except requests.RequestException:
                pass
            time.sleep(1 + 2 * attempt)  # S3 answers 503 SlowDown now and then
        raise RuntimeError("request failed: " + url)

    def size(self, path):
        return int(self._request("HEAD", path).headers["Content-Length"])

    def cat_file(self, path, start=None, end=None, **_):
        self.requests += 1
        return self._request("GET", path, {"Range": f"bytes={start}-{end - 1}"}).content

    def json(self, url):
        return self._request("GET", url).json()


FS = RangeFS()
_META = {}


def meta(model):
    """Static metadata of a model: chunk length, time step, bounding box, latest run."""
    if model not in _META:
        m = FS.json(f"{BUCKET}/data/{model}/static/meta.json")
        lat0, lon0, lat1, lon1 = (float(v) for v in re.search(r"BBOX\[([^\]]+)\]", m["crs_wkt"]).group(1).split(","))
        m["bbox"] = (lat0, lon0, lat1, lon1)
        _META[model] = m
    return _META[model]


def cell(model, shape, lat, lon):
    """Nearest grid cell of a regular latitude-longitude grid stored south to north, west to east."""
    lat0, lon0, lat1, lon1 = meta(model)["bbox"]
    ny, nx = shape[0], shape[1]
    y = int(round((lat - lat0) / ((lat1 - lat0) / (ny - 1))))
    x = int(round((lon - lon0) / ((lon1 - lon0) / (nx - 1))))
    return min(max(y, 0), ny - 1), min(max(x, 0), nx - 1)


def read_latest(model, variable, points, start, end):
    """Latest available forecast for [start, end] (UTC timestamps on the model's time step)."""
    m = meta(model)
    dt, length = m["temporal_resolution_seconds"], m["chunk_time_length"]
    i0, i1 = int(pd.Timestamp(start).timestamp()) // dt, int(pd.Timestamp(end).timestamp()) // dt
    index = pd.to_datetime(np.arange(i0, i1 + 1) * dt, unit="s", utc=True)
    out = {name: np.full(len(index), np.nan, dtype="float32") for name in points}
    for chunk in range(i0 // length, i1 // length + 1):
        try:
            reader = omfiles.OmFileReader.from_fsspec(FS, f"{BUCKET}/data/{model}/{variable}/chunk_{chunk}.om")
        except FileNotFoundError:
            continue
        a, b = max(i0, chunk * length), min(i1, (chunk + 1) * length - 1)
        for name, (lat, lon) in points.items():
            y, x = cell(model, reader.shape, lat, lon)
            out[name][a - i0:b - i0 + 1] = reader[y, x, a - chunk * length:b - chunk * length + 1]
        reader.close()
    return pd.DataFrame(out, index=index)


def read_run(model, run, variable, points):
    """One model run (run = UTC init time) from the per-run archive; index = valid times."""
    run = pd.Timestamp(run)
    base = f"{BUCKET}/data_run/{model}/{run:%Y/%m/%d/%H%M}Z"
    valid = pd.to_datetime(FS.json(f"{base}/meta.json")["valid_times"], utc=True)
    reader = omfiles.OmFileReader.from_fsspec(FS, f"{base}/{variable}.om")
    nt = reader.shape[2]
    index = valid[len(valid) - nt:]  # backward-averaged variables come without the first step
    out = {}
    for name, (lat, lon) in points.items():
        y, x = cell(model, reader.shape, lat, lon)
        out[name] = reader[y, x, :]
    reader.close()
    return pd.DataFrame(out, index=index)
