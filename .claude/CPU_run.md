# CPU run: checking `predictions_init_with_godas` against a local simulation

Date: 2026-10-07. Machine: CPU only (22 cores, no CUDA). Env: conda `orca`, used read-only.

## Goal

Check that the files in `predictions_init_with_godas/` (released by the ORCA-DL authors) match what the model produces from `example_data/` (GODAS 1980-01 initial state, wind stress `uflx`/`vflx`).

## Reference data layout

- `predictions_init_with_godas/{so,thetao,tos,uo,vo,zos}/lead_0{1,2,3}.nc`
- Each file has 504 initial times; `time[0]` = 1980-02-01 for lead 1, which is the January 1980 initial state + 1 month.
- Variable mapping: so=salt, thetao=pottmp, tos=sst, uo=ucur, vo=vcur, zos=sshg.
- Compare reference `lead_0k` at `time=0` with prediction step `k-1`.

## Findings

1. **Single seed (`seed_1.bin`, `run_1Y_vanilla_orcadl.py`, 12 steps)**
   - Grids, time stamps and land masks are identical.
   - Values are close but not equal. Correlation is >= 0.9988 for salt/pottmp/sst/sshg and 0.97-0.99 for the currents.
   - The mean absolute difference at lead 1 is 0.107 °C for sst.
2. **Horizon test.** A 3-step run and a 4-step run give identical first 3 steps (max diff 0.0). `forward_multi_steps` is purely autoregressive, so run length does not affect earlier months.
3. **Initial guess.** I first suspected the reference was an ensemble mean, from `predict.py` (`logits_sum / num_model`) and README line 92. README line 28 only says "all the predictions ... can be found in" the linked folder and does not say the files are an ensemble mean. `predict.sh` also passes a single checkpoint (`seed1.bin`).
4. **10-seed ensemble (3 steps, CPU).** The mean over `ckpt/seed_{1..10}.bin` matches the reference to float precision. Mean absolute difference averaged over leads 1-3:

| | salt | pottmp | sst | ucur | vcur | sshg |
| --- | --- | --- | --- | --- | --- | --- |
| single seed (typical) | 0.018 | 0.095 | 0.13 | 0.0058 | 0.0038 | 0.009 |
| mean of seeds 1-9 | 2.0e-3 | 1.0e-2 | 1.4e-2 | 6.3e-4 | 4.2e-4 | 1.0e-3 |
| **mean of seeds 1-10** | 4.5e-5 | 9.4e-5 | 2.0e-7 | 4.8e-6 | 4.0e-6 | 2.3e-8 |

The error drops monotonically as seeds are added and collapses only when seed 10 is included, so the reference is the mean of all 10 seeds.

## Conclusions

- The released predictions are a 10-seed ensemble mean.
- `example_data` is the same initial state the authors used.
- The normalisation and denormalisation in `run_1Y_vanilla_orcadl.py` are correct. Each lead uses the statistics of the target month, `(month + t + 1) % 12`.
- The model code behaves as expected.

## Residual error

The remaining difference is about 1e-4 °C for pottmp and much smaller for the other variables. The likely cause is hardware: the reference was probably produced on GPU, and this run was on CPU. Attention and conv kernels, summation order and TF32/cuDNN settings can all change float32 results slightly. Other possibilities, not checked:

- `predict.py` reads normalised `.npy` inputs, so a different float path in the preprocessing is possible.
- The ensemble mean may be taken in a different space or precision (e.g. float64 accumulation).
- Batching: the reference processes 504 initial times in batches of 8.

## Next step (GPU machine)

Use `predict.py` instead of a custom script. It already ensembles over `--ckpt_list`.

- Pass all 10 checkpoints: `--ckpt_list ckpt/seed_1.bin ... ckpt/seed_10.bin`. The shipped `predict.sh` passes only `seed1.bin`, and the checkpoints here are named `seed_N.bin`.
- Use `--predict_steps 3` to keep inference cheap, and `--input_steps 1`, `--atmo_var_list tauu tauv`, `--input_var_list 'so thetao tos uo vo zos'`, as in `predict.sh`.
- `--data_dir` must point to the GODAS `valid_test_data` (normalised `.npy` files) from the OneDrive or Hugging Face data, because `predict.py` does not read `example_data/*.nc`.
- Compare the output to the reference per variable. If it still differs at about 1e-4, try disabling TF32 (`torch.backends.cuda.matmul.allow_tf32 = False`, `torch.backends.cudnn.allow_tf32 = False`) and the same batch size as the reference.
- To use `example_data` on GPU instead, set `device='cuda'` in `run_1Y_vanilla_orcadl.py` (it already does this automatically) and loop over the 10 checkpoints. My scratchpad script did this on CPU with `predict_time_steps=3`.

## Files

- Nothing in the repo was changed apart from this note.
- Scratch outputs (not in the repo): `ens.py` and `preds.npy` (shape `(10, 1, 3, 66, 128, 360)`, normalised predictions) in the session scratchpad.
