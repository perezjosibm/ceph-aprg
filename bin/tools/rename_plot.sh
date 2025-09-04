#!/usr/bin/env bash
# This si an auxiliary script  to rename the filenames of the .png plots according to the cache algorithm,
# # so that they can be easily identified.
# Usage: ./run_cpu_plot.sh <input_file_path> 
# # Normally called from find:
# # find . -type f -name "OSD*_top_cpu.dat" -exec gsed -i  '1 s/#//g' {} \;
# find . -name "cpu_usage.log" -exec ./run_cpu_plot.sh {} \;
#pushd
INPUT_FILE="$1"
pathname=$(dirname "$INPUT_FILE")
filename=$(basename "$INPUT_FILE")
cd "$pathname" || exit
if [[ "$pathname" == *"_2Q_"* ]]; then
    #echo $filename | gsed -r 's/(OSD[0-9]+)_2Q_(.*)/\1_2Q_\2_cpu.png/' | xargs -I {} mv "$filename" "{}"
    #echo "REnaming file: $filename to ${filename/_2Q_/_2Q_}_cpu.png"
    echo "Renaming file: $filename to ${filename/_32fio_bal_osd_rc_1procs_/_2Q_}"
    mv "$filename" "${filename/_32fio_bal_osd_rc_1procs_/_2Q_}"
# elif [[ "$pathname" == *"_ARC_"* ]]; then
#     #echo $filename | gsed -r 's/(OSD[0-9]+)_ARC_(.*)/\1_ARC_\2_cpu.png/' | xargs -I {} mv "$filename" "{}"
#     #echo "REnaming file: $filename to ${filename/_ARC_/_ARC_}_cpu.png"
#     echo "REnaming file: $filename to ${filename/_32fio_bal_osd_rc_1procs_/_ARC_}"
#     #mv "$filename" "${filename/_32fio_bal_osd_rc_1procs_/_ARC_}"
elif [[ "$pathname" == *"_LRU_"* ]]; then
    #echo $filename | gsed -r 's/(OSD[0-9]+)_LRU_(.*)/\1_LRU_\2_cpu.png/' | xargs -I {} mv "$filename" "{}"
    #echo "REnaming file: $filename to ${filename/_LRU_/_LRU_}_cpu.png"
    echo "Renaming file: $filename to ${filename/_32fio_bal_osd_rc_1procs_/_LRU_}"
    mv "$filename" "${filename/_32fio_bal_osd_rc_1procs_/_LRU_}"
else
    echo "Skipping file: $INPUT_FILE (does not match pattern)"
    exit 0
fi
#gnuplot "$filename"
#popd
