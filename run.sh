#!/usr/bin/env bash
set -euo pipefail

input_dir="${1:?usage: run.sh <input_pdf_dir> <output_path>}"
output_path="${2:?usage: run.sh <input_pdf_dir> <output_path>}"

# Absolute paths: the container runs with a read-only root and no guaranteed cwd.
exec python3 -m mib_pipeline "$input_dir" "$output_path"
