## import packages
import torch
import numpy as np
import pandas as pd
import xarray as xr
from model import ORCADLConfig, ORCADLModel

## Download the contents in stat and ckpt folder

## Prepare input data

# salinity, potential temp, sea surface temp, zonal current, meridional current, sea surface height, zonal wind stress, meridional wind stress
variables = ['salt', 'pottmp', 'sst', 'ucur', 'vcur', 'sshg', 'uflx', 'vflx']
ocean_variables = variables[:-2]

# load mean and std
stat = {
    'mean': {v: np.load(f"./stat/mean/{v}.npy") for v in variables},
    'std': {v: np.load(f"./stat/std/{v}.npy") for v in variables}
}
## Note: the unit for temperatue is °C, salinity is g/kg, current is m/s, ssh is m, wind stress is N/m²

# load data
month = 0  # month of the initial condition (0 = January); the corresponding statistical values ​​for each month are different
ocean_vars = []
atmo_vars = []
coords_by_var = {}
for v in ocean_variables:
    ds = xr.open_dataset(f"./example_data/{v}.nc")
    coords_by_var[v] = ds.coords
    # Important: the units of input data should be consistent with the units of the statistical values
    normed_data = (ds[v].values - stat['mean'][v][month]) / stat['std'][v][month]
    ocean_vars.append(normed_data if len(normed_data.shape) == 3 else normed_data[None])
for v in variables[-2:]:
    ds = xr.open_dataset(f"./example_data/{v}.nc")
    normed_data = (ds[v].values - stat['mean'][v][month]) / stat['std'][v][month]
    atmo_vars.append(normed_data[None])

device = 'cuda' if torch.cuda.is_available() else 'cpu'

ocean_vars = torch.from_numpy(np.nan_to_num(np.concatenate(ocean_vars, axis=0)))[None].float().to(device) # (1, 66, 128, 360)
atmo_vars = torch.from_numpy(np.nan_to_num(np.concatenate(atmo_vars, axis=0)))[None].float().to(device) # (1, 2, 128, 360)

## Setup ORCA-DL
model = ORCADLModel(ORCADLConfig.from_json_file('./model_config.json'))
model.load_state_dict(torch.load('./ckpt/seed_1.bin', map_location='cpu'))
model.to(device)
model.eval()

## Run a 1-year simulation from the initial conditions above
# the model's timestep is 1 month, so 12 steps forecast a full year ahead
predict_time_steps = 12

with torch.no_grad():
    output = model(ocean_vars=ocean_vars, atmo_vars=atmo_vars, predict_time_steps=predict_time_steps)

# Post-process the output
preds = output.preds.detach().cpu().numpy() # (1, 12, 66, 128, 360)
# salinity, potential temp, sea surface temp, zonal current, meridional current, sea surface height
pred_all_variables = np.split(preds, model.split_chans, axis=2) # split by channels
# pred_all_variables contains the prediction of all ocean variables, in the same order as ocean_variables

# inverse the normalization for each lead month, using that month's statistics
level_vars = {'salt', 'pottmp', 'ucur', 'vcur'}
data_vars = {}
for v, pred in zip(ocean_variables, pred_all_variables):
    denormed_steps = []
    for t in range(predict_time_steps):
        target_month = (month + t + 1) % 12  # stat should be the prediction month
        denormed_steps.append(pred[:, t] * stat['std'][v][target_month] + stat['mean'][v][target_month])
    year_pred = np.stack(denormed_steps, axis=1)[0] # (12, ..., 128, 360), batch dim dropped
    if v in level_vars:
        data_vars[v] = (('time', 'level', 'lat', 'lon'), year_pred)
    else:
        data_vars[v] = (('time', 'lat', 'lon'), year_pred[:, 0])

# assemble the 1-year forecast into a single Dataset with all ocean variables
init_time = pd.Timestamp(coords_by_var['sst']['time'].values)
forecast_times = [init_time + pd.DateOffset(months=t + 1) for t in range(predict_time_steps)]

year_ds = xr.Dataset(
    data_vars,
    coords={
        'time': forecast_times,
        'level': coords_by_var['salt']['level'].values,
        'lat': coords_by_var['sst']['lat'].values,
        'lon': coords_by_var['sst']['lon'].values,
    },
)

year_ds.to_zarr("./1Y_vanilla_ORCA-DL.zarr")
