#!/bin/bash
# Recreate the full CalibrationNet python environment on any machine:
# database layer (sqlalchemy/alembic/psycopg) + Nab data reading
# (nabPy, deltaRice, h5py).
#
#   ./scripts/setup_env.sh /path/to/new/env /path/to/pyNab /path/to/deltarice
#
# pyNab:     clone from https://gitlab.com/NabExperiment/pyNab
# deltarice: HDF5 compression codec used by Nab data files (source checkout)
#
# The numba/llvmlite/numpy pins are load-bearing: newer llvmlite has no
# binary wheel everywhere and fails to build from source; these versions
# are the tested-good set (mirrors the ManitobaWork_1374 environment).

set -euo pipefail

ENV_DIR=${1:?usage: setup_env.sh <env_dir> <pyNab_dir> <deltarice_dir>}
PYNAB_DIR=${2:?path to pyNab checkout}
DELTARICE_DIR=${3:?path to deltarice checkout}

python3 -m venv "$ENV_DIR"
source "$ENV_DIR/bin/activate"
pip install --upgrade pip

# Pinned scientific stack (see note above).
pip install "numpy==1.26.4" "llvmlite==0.42.0" "numba==0.59.1"

# Nab data reading. pyarrow is needed by recent dask's dataframe module.
pip install pyarrow
pip install -e "$PYNAB_DIR"
pip install "$DELTARICE_DIR"

# CalibrationNet itself (sqlalchemy, alembic, psycopg, python-dotenv).
pip install -e "$(cd "$(dirname "$0")/.." && pwd)"

python - <<'EOF'
import warnings; warnings.filterwarnings("ignore")
import nabPy, deltaRice, h5py, sqlalchemy, alembic
print("environment OK: nabPy, deltaRice, h5py, sqlalchemy, alembic all import")
EOF
