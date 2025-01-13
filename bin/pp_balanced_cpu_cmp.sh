#!/usr/bin/env bash
#/usr/bin/bash
# ! Usage: ./pp_balanced_cyanstore.sh [-t <osd-be-type>] [-d rundir]
# !		 
# ! Run postprocessing to assemble a 3-side comparison of response latency curves
# for the three CPU allocation strategies
# ! -d : indicate the run directory cd to
# ! -t :  OSD backend type: cyan, blue, sea
# ! -o : output .md file
# ! Remember to edit the range for the reactors
#
# Defaults:
OSD_TYPE=cyan
RUN_DIR="/tmp"
OUT_MD="${OSD_TYPE}_out_cmp.md"
OUT_LOG="${OSD_TYPE}_out_cmp.log"

usage() {
    cat $0 | grep ^"# !" | cut -d"!" -f2-
}
# -d for the directory to traverse
# -t for the type of OSD backend: [cyan| blue | sea ](store), 
while getopts 'd:t:o:' option; do
  case "$option" in
    d) RUN_DIR=$OPTARG
        ;;
    t) OSD_TYPE=$OPTARG
        ;;
    o) OUT_MD=$OPTARG
        ;;
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
 done
# Post process the data collected from run_balanced_cyanstore.sh
PLOT_TEMPLATE=/root/bin/cpu_cmp_TEMPLATE_CMP.plot

#########################################
# Main:
pushd ${RUN_DIR} 

# Run balanced vs default CPU core/reactor distribution for ${OSD_TYPE}store
fun_pp_bal_vs_default_tests() {
  declare -A bal_ops_table
  declare -A subs_table
  bal_ops_table["default"]="DEFAULT_DAT"
  bal_ops_table["bal_osd"]="OSD_BAL_DAT"
  bal_ops_table["bal_socket"]="SOCKET_BAL_DAT"
  declare -a workloads=( randread randwrite seqwrite seqread )
  declare -a plots=( _bal_vs_unbal_iops_vs_lat _osd_cpu _osd_mem _fio_cpu _fio_mem )

  for WORKLOAD in "${workloads[@]}"; do
    echo "# ${WORKLOAD}" >> ${OUT_MD}
    # The ranges should come from the test plan .yaml
    for NUM_OSD in 8; do
      for NUM_REACTORS in 5 6; do

        test_name="${OSD_TYPE}_${NUM_OSD}osd_${NUM_REACTORS}reactor_8fio_${WORKLOAD}"
        test_title="${OSD_TYPE}store-${NUM_OSD}osd-${NUM_REACTORS}reactor-${WORKLOAD}"
        # ${OSD_TYPE}_5osd_3reactor_8fio_bal_socket_rc_1procs_randread.zip
        echo -e "## $NUM_OSD OSD crimson, $NUM_REACTORS reactor, fixed FIO 8 cores, response latency" >> ${OUT_MD}
        for KEY in "${!bal_ops_table[@]}"; do

          TEST_RESULT="${OSD_TYPE}_${NUM_OSD}osd_${NUM_REACTORS}reactor_8fio_${KEY}_rc_1procs_${WORKLOAD}"
          zarch="${TEST_RESULT}.zip"
          test_dat=${zarch/zip/dat}
          subs_table[${KEY}]="${test_dat}"
          #fun_run_fio -g $test_name
          if [ ! -f "${zarch}" ]; then
            echo -e "Missing ${zarch}" | tee >> ${OUT_LOG}
            continue
            #echo "![${test_dat}]()"
          else
            [ ! -f "${test_dat}" ] && unzip ${zarch} ${test_dat} 2>&1 >> ${OUT_LOG}
            if [ ! -f "${test_name}.plot" ]; then
              # Copy the template into this comparison  if this is the first key
              #./subs.sed ${PLOT_TEMPLATE} > ${TEST_RESULT}.plot
              sed -e "s/TEST_RUN/${test_name}/g" ${PLOT_TEMPLATE} > ${test_name}.plot
              sed -i -e "s/TEST_TITLE/${test_title}/g" ${test_name}.plot
            fi
            # sed the template for this KEY
            #./subs.sed -i ${TEST_RESULT}.plot
            sed -i -e "s/${bal_ops_table[${KEY}]}/${subs_table[${KEY}]}/g" ${test_name}.plot
          fi
        done # KEY
        gnuplot ${test_name}.plot > /dev/null 2>&1
        for x in "${plots[@]}"; do
          this_plot="${test_name}${x}"
          echo "![${this_plot}](${this_plot}.png)" >> ${OUT_MD}
        done
        echo "---" >> ${OUT_MD}
        done # NUM_REACTORS
      done # NUM_OSD
    done # WORKLOAD
}

fun_pp_bal_vs_default_tests

popd
