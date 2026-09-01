# Cluster resources: GT vs NERSC, and how to size a job

A practical guide to the SLURM resource knobs this project uses, why
the two clusters need different settings, and how to convert a job
between them. Grounded in the two proven submission scripts —
`scripts/submit_trap_filter.sh` + `scripts/apply_trap_filter.sh` (GT
PACE) and `scripts/offline/submit_trap_filter_nersc.sh` (NERSC
Perlmutter) — and in the failures that taught us the numbers
(2026-08-11: the same filter task at 4 CPUs was OOM-killed on NERSC;
it needs the GT-proven ~100 GB).

## The mental model: node → task → CPU → memory

- A **node** is one physical machine (Perlmutter CPU node: 128
  physical cores / 256 logical CPUs, 512 GB; GT PACE nodes vary).
- A **task** (`--ntasks`) is one running copy of your program. Our
  work is one *independent program run per work item* (one segment,
  one run), so our jobs are always `--ntasks=1` — parallelism comes
  from submitting MANY such jobs (an array), not from tasks talking to
  each other (that's MPI, which we don't use).
- **CPUs per task** (`--cpus-per-task`) is how many cores that one
  program may use. This only helps if the program can use them — ours
  can, through dask (below).
- **Memory** is the silent killer: exceed your allocation and the
  kernel kills the process instantly — the log just says `Killed`, no
  Python traceback, and the job may still end "COMPLETED"-ish. If a
  task died with no traceback, suspect memory first.

## Why the trap filter needs so much memory

One subrun's waveforms form a **~7.6 GB lazy dask array**
(`waves.waves()` in `calibrationnet/acquisition/waveforms.py`). "Lazy"
means dask streams it in chunks rather than loading it whole, but the
working set during `applyTrapFilter` still peaks at tens of GB.

The laziness also dictates how `segment_energies` separates a segment
from its neighbours: it filters the WHOLE subrun and applies the
timestamp mask to the resulting energies, never to the waveforms —
boolean-indexing the lazy waveform array would force an expensive
dask rechunk, whereas masking the small energies array is free. (Only
the two subruns at a segment's edges have anything masked off at
all.) The proven sizing
is the GT task's: **~24-32 CPUs and 100 GB, 4 h** per segment. Under
that (NERSC shared at 4 CPUs ≈ 8 GB) the task is OOM-killed within
minutes.

Dask also supplies the in-task parallelism: it spreads the filter math
across whatever `--cpus-per-task` grants, so one segment task at 32
CPUs finishes several times faster than at 4. That is why we give a
single task many CPUs even though our tasks never communicate.

## The two clusters side by side

| | GT PACE (phoenix/embers) | NERSC Perlmutter |
|---|---|---|
| account | `-A gts-ajezghani3` | none needed in our scripts (default project; add `-A mXXXX` if required) |
| QOS we use | `embers` (free backfill, preemptable — pair with `--requeue`) | `shared` (fraction of a node, starts sooner, billed only for the cores requested) |
| node type selector | none | `--constraint=cpu` (required — omitting it can land you on GPU nodes) |
| memory | explicit `--mem=100gb` | shared QOS grants ~2 GB per requested CPU **unless you say `--mem=...`** — always say it for the filter |
| walltime cap | 7:59 on embers (submit script caps at 479 min) | shared allows much longer, but shorter requests schedule sooner — request honestly (4 h for filter tasks, not 47) |
| submit cap | ~50 SUBMITTED jobs/user (QOSMaxSubmitJobPerUserLimit) — the GT script packs segments into ≤40 chunked array tasks because of this | no such tight cap; one array task per segment is fine |
| software | `module load` for system libs; nabPy env in scratch/home | same idea: `module load cray-hdf5` for the deltarice BUILD only (runtime needs nothing — rpath is baked in) |
| filesystems | home + scratch | `$HOME` (backed up, slower) + `$PSCRATCH`/`/pscratch` (fast, purged!) — big data lives in pscratch, code in home |
| watch progress | `squeue -u $USER`; per-run report job | `squeue -u $USER`; `sacct -j <jobid>` after; logs in the `--output` dir |

## Array jobs vs many-tasks-in-one-job

Two ways to run N independent things; we use the first:

1. **Job array** (`--array=1-23`): N independent single-task jobs
   sharing one script; each reads its work item from a manifest line
   via `$SLURM_ARRAY_TASK_ID`. Each task gets its OWN walltime and
   memory; one failing doesn't touch the others, and task N is redone
   alone (`sbatch --array=N`). Both our submit scripts use this
   pattern (GT adds `%40` throttling and segment-chunking only because
   of its submit cap).
2. **One job, many parallel tasks** (`-N1 --ntasks-per-node=28` +
   `srun`): a single job that launches 28 program copies at once.
   Fine when every item is small and uniform; worse for us because
   the whole job shares one walltime, one failure story, and the
   memory math gets murky. If you've used this style before (28
   1-CPU tasks), note it is the OPPOSITE trade of ours: many small
   single-CPU programs vs few big multi-CPU ones — for dask-backed
   work, few-big wins.

## Converting a job GT → NERSC (checklist)

1. Drop `-A gts-...`, add `--constraint=cpu`, set `--qos=shared` (or
   `regular` for a whole node).
2. Keep `--ntasks=1` + generous `--cpus-per-task`; **add an explicit
   `--mem=`** matching the GT value — NERSC will not infer it.
3. Re-examine walltime: no 8 h embers cap, but shorter = scheduled
   sooner.
4. Arrays: remove GT's chunking/`MAX_SUBMIT` machinery — one array
   task per work item is fine on NERSC.
5. Paths: scratch → `/pscratch/sd/<u>/<user>/...`; environment →
   `$HOME/pyNabEnv/bin/python` by absolute path (no activation needed
   in batch scripts).
6. `--requeue` is an embers (preemption) tool; harmless but pointless
   on shared.

## Proven sizings for this project

| work | per-task request | duration |
|---|---|---|
| trap filter, one ~30 min segment | 32 CPUs, 100 GB | ~10 min |
| trap filter, one multi-hour dwell (9416) | 32 CPUs, 100 GB | ~1-2 h |
| spectrum fits (offline/fit_spectra.py) | login node or 4 CPUs, few GB | seconds per healthy pixel; minutes per failing pixel (retry ladder) |
| calibrations (offline/calibrate.py) | login node | seconds |

Login-node etiquette (both sites): smoke tests and few-subrun slices
only — anything CPU-heavy for more than a few minutes belongs in a
batch job.

## Debugging a dead task

1. Read its log (the `--output` directory): a Python traceback means
   a code/input problem; a bare `Killed` means memory; a usage error
   means the batch script mangled the command line.
2. `sacct -j <jobid> -o JobID,State,Elapsed,MaxRSS` — `MaxRSS` tells
   you what memory it actually used (size the next request from it);
   `OUT_OF_MEMORY` state confirms an OOM.
3. Redo just the failed indices: `sbatch --array=N,M <same script>`.
