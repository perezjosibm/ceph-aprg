#!/usr/bin/env bash
#/usr/bin/bash

#
# Post process the data collected from run_balanced_cyanstore.sh
PLOT_TEMPLATE=/root/bin/cyan_TEMPLATE_CMP.plot
# Run balanced vs default CPU core/reactor distribution for cyanstore
fun_pp_bal_vs_default_tests() {

  declare -A bal_ops_table
  declare -A subs_table
  bal_ops_table["default"]="DEFAULT_DAT"
  bal_ops_table["bal_osd"]="OSD_BAL_DAT"
  bal_ops_table["bal_socket"]="SOCKET_BAL_DAT"
  declare -a workloads=( randread randwrite seqwrite seqread )
  declare -a plots=( _bal_vs_unbal_iops_vs_lat _osd_cpu _osd_mem _fio_cpu _fio_mem )

  for WORKLOAD in "${workloads[@]}"; do
    echo "# ${WORKLOAD}"
    for NUM_OSD in 5 8; do
      for NUM_REACTORS in 3 4 5; do

        test_name="cyan_${NUM_OSD}osd_${NUM_REACTORS}reactor_8fio_${WORKLOAD}"
        test_title="cyanstore-${NUM_OSD}osd-${NUM_REACTORS}reactor-${WORKLOAD}"
        # cyan_5osd_3reactor_8fio_bal_socket_rc_1procs_randread.zip
        echo -e "## $NUM_OSD OSD crimson, $NUM_REACTORS reactor, fixed FIO 8 cores, response latency"
        for KEY in "${!bal_ops_table[@]}"; do

          TEST_RESULT="cyan_${NUM_OSD}osd_${NUM_REACTORS}reactor_8fio_${KEY}_rc_1procs_${WORKLOAD}"
          #echo ${TEST_RESULT}
          zarch="${TEST_RESULT}.zip"
          test_dat=${zarch/zip/dat}
          subs_table[${KEY}]="${test_dat}"
          #fun_run_fio -g $test_name
          if [ -f "${zarch}" ]; then
            #echo "![${test_dat}]()"
            [ ! -f "${test_dat}" ] && unzip -q ${zarch} ${test_dat}
          fi
          if [ ! -f "${test_name}.plot" ]; then
            # Copy the template into this comparison  if this is the first key
            #./subs.sed ${PLOT_TEMPLATE} > ${TEST_RESULT}.plot
            sed -e "s/TEST_RUN/${test_name}/g" ${PLOT_TEMPLATE} > ${test_name}.plot
            sed -i -e "s/TEST_TITLE/${test_title}/g" ${test_name}.plot
          fi
            # sed the template for this KEY
            #./subs.sed -i ${TEST_RESULT}.plot
          sed -i -e "s/${bal_ops_table[${KEY}]}/${subs_table[${KEY}]}/g" ${test_name}.plot
          done
          gnuplot ${test_name}.plot > /dev/null 2>&1
          for x in "${plots[@]}"; do
            this_plot="${test_name}${x}"
            echo "![${this_plot}](${this_plot}.png)"
          done
          echo "---"
        done
      done
    done
  }

fun_pp_bal_vs_default_tests

