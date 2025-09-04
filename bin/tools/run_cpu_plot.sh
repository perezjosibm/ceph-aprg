#!/usr/bin/env bash
# This script runs the CPU plotting Python script with specified parameters.
# Usage: ./run_cpu_plot.sh <input_file_path> 
# # Normally called from find:
# # find . -type f -name "OSD*_top_cpu.dat" -exec gsed -i  '1 s/#//g' {} \;
# find . -name "cpu_usage.log" -exec ./run_cpu_plot.sh {} \;
#pushd
INPUT_FILE="$1"
pathname=$(dirname "$INPUT_FILE")
filename=$(basename "$INPUT_FILE")
cd "$pathname" || exit
gnuplot "$filename"
#popd
