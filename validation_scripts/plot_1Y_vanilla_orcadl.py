## Plot the first and last timesteps of each variable in the 1-year ORCA-DL forecast
import os
from datetime import datetime

import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
from global_land_mask import globe

ZARR_PATH = "./1Y_vanilla_ORCA-DL.zarr"
FIG_DIR = "./fig"

# metadata used to pick a sensible colormap / label / units per variable
VAR_INFO = {
    "sst":    {"label": "Sea Surface Temperature",  "units": "°C",  "cmap": "RdYlBu_r", "diverging": False},
    "pottmp": {"label": "Potential Temperature",     "units": "°C",  "cmap": "RdYlBu_r", "diverging": False},
    "salt":   {"label": "Salinity",                  "units": "g/kg","cmap": "viridis",  "diverging": False},
    "sshg":   {"label": "Sea Surface Height",        "units": "m",   "cmap": "RdBu_r",   "diverging": True},
    "ucur":   {"label": "Zonal Current",             "units": "m/s", "cmap": "RdBu_r",   "diverging": True},
    "vcur":   {"label": "Meridional Current",        "units": "m/s", "cmap": "RdBu_r",   "diverging": True},
}

# for 3D (time, level, lat, lon) variables, plot the shallowest level
SURFACE_LEVEL_IDX = 0


def source_timestamp(path):
    """Latest modification time (minute precision) among the files inside the zarr store."""
    mtimes = []
    for root, _, files in os.walk(path):
        for f in files:
            mtimes.append(os.path.getmtime(os.path.join(root, f)))
    latest = max(mtimes) if mtimes else os.path.getmtime(path)
    return datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M")


def add_source_caption(fig, path, timestamp):
    fig.text(
        0.5, 0.02,
        f"Source file: {path} - {timestamp}",
        ha="center", va="bottom", fontsize=8, color="0.35",
    )


def add_land_contour(ax, lat, lon):
    lon_pm = np.where(lon > 180, lon - 360, lon)
    lon_grid, lat_grid = np.meshgrid(lon_pm, lat)
    land = globe.is_land(lat_grid, lon_grid)
    ax.contour(lon, lat, land, levels=[0.5], colors="0.2", linewidths=0.6)


def plot_variable(ds, var, out_dir, timestamp, source_path):
    info = VAR_INFO[var]
    da = ds[var]
    has_level = "level" in da.dims

    first = da.isel(time=0)
    last = da.isel(time=-1)
    level_label = ""
    if has_level:
        first = first.isel(level=SURFACE_LEVEL_IDX)
        last = last.isel(level=SURFACE_LEVEL_IDX)
        level_label = f" @ {da.level.values[SURFACE_LEVEL_IDX]:g} m"

    lat = ds.lat.values
    lon = ds.lon.values

    data_first = first.values
    data_last = last.values

    if info["diverging"]:
        vmax = np.nanpercentile(np.abs(np.concatenate([data_first.ravel(), data_last.ravel()])), 99)
        vmin, vmax = -vmax, vmax
    else:
        combined = np.concatenate([data_first.ravel(), data_last.ravel()])
        vmin, vmax = np.nanpercentile(combined, [1, 99])

    lon_span = lon.max() - lon.min()
    lat_span = lat.max() - lat.min()
    fig_width = 11
    panel_height = fig_width / 2 * (lat_span / lon_span)
    fig, axes = plt.subplots(
        1, 2, figsize=(fig_width, panel_height + 1.2), sharey=True,
    )

    mesh = None
    for ax, data, ts in zip(axes, [data_first, data_last], [first.time.values, last.time.values]):
        mesh = ax.pcolormesh(lon, lat, data, cmap=info["cmap"], vmin=vmin, vmax=vmax, shading="nearest")
        add_land_contour(ax, lat, lon)
        ax.set_aspect("equal")
        ax.set_xlabel("Longitude (°E)")
        ax.set_title(np.datetime_as_string(ts, unit="D"))

    axes[0].set_ylabel("Latitude (°N)")

    fig.suptitle(f"{info['label']}{level_label}", fontsize=13)
    fig.colorbar(mesh, ax=axes, orientation="vertical", fraction=0.03, pad=0.02, label=info["units"])

    add_source_caption(fig, source_path, timestamp)

    out_path = os.path.join(out_dir, f"{var}_first_last.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    ds = xr.open_zarr(ZARR_PATH)
    timestamp = source_timestamp(ZARR_PATH)
    source_path = os.path.normpath(ZARR_PATH)

    for var in VAR_INFO:
        plot_variable(ds, var, FIG_DIR, timestamp, source_path)


if __name__ == "__main__":
    main()
