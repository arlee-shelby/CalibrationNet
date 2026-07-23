#!/bin/bash
# Ingest the nabPy-standard trap filter outputs into CalibrationNet from
# a PACE Phoenix node. The Postgres lives on the cluster, so this runs at
# internal-network speed — no SSH tunnel involved.
#
#   sbatch scripts/ingest_filter_outputs.sbatch /path/to/filterOutputCSVs
#
# Setup (once, from the repo root on the cluster — see README):
#   module load python/3.11    # or the cluster's current python >= 3.9
#   python -m venv .venv && source .venv/bin/activate && pip install -e .
#   cp .env.example .env       # DATABASE_URL with host atl1-1-01-006-17-2,
#                              # NO tunnel/localhost, same search_path option
#
#SBATCH -A gts-ajezghani3
#SBATCH -J calnet-filter-ingest
#SBATCH --output=./scripts/slurmoutputs/filter_ingest_%j.out
#SBATCH --time=02:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G

set -euo pipefail

CSV_DIR=${1:?usage: sbatch ingest_filter_outputs.sbatch <csv_dir>}
WORKERS=${SLURM_CPUS_PER_TASK:-4}

cd "${SLURM_SUBMIT_DIR}"
source .venv/bin/activate

# One python process per file, WORKERS at a time. Safe: each file is its
# own transaction and re-ingestion replaces rather than duplicates.
ls "${CSV_DIR}"/*.csv | xargs -P "${WORKERS}" -I {} \
    python scripts/ingest_filter_output.py {} --label nabpy-standard

echo "all files processed"
