#!/usr/bin/env bash

# ! Usage: ./run_balanced_osd.sh [-t <osd-be-type>] [-d rundir]
# !		 
# ! Run test plans to compare Classic vs Crimson OSD
# ! -e : test plan .sh file to source, default is balanced_osd_test_plan.sh
# ! -d : indicate the run directory cd to
# ! -t :  OSD backend type: classic, cyan, blue, sea. 
# !  Runs all the balanced vs default CPU core/reactor
# !    distribution tests for the given OSD backend type, 'all' for the three of them.
# ! -b : Run a single balanced CPU core/reactor distribution tests for all the OSD backend types

# Test plan experiment to compare the effect of balanced vs unbalanced CPU core distribution for the Seastar
# reactor threads -- using a pure reactor env cyanstore -- extended for Bluestore as well
#
# get the CPU distribution for the 2 general cases: 24 physical vs 52 inc. HT

# Redirect to stdout/stderr to a log file
# exec 3>&1 4>&2
# trap 'exec 2>&4 1>&3' 0 1 2 3
# exec 1>/tmp/run_balanced_osd.log 2>&1
#############################################################################################


SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/common.sh
source ${SCRIPT_DIR}/run_osd_utils.sh

#########################################
# Main:
#
trap 'echo "$(date)== INT received, exiting... =="; fun_stop ${fio_pid}; exit 1' SIGINT SIGTERM SIGHUP

# DEfine some FIO options, or a .json test plan instead
while getopts 'ab:c:d:e:g:t:s:r:jlpxz:' option; do
  case "$option" in
    a) fun_show_all_tests
       exit
        ;;
    c) OSD_CPU=$OPTARG
        ;;
    b) BALANCE=$OPTARG
        ;;
    d) RUN_DIR=$OPTARG
        ;;
    e) if [ ! -z "${OPTARG}" ] && [ -f "${SCRIPT_DIR}/${OPTARG}" ]; then
        TEST_PLAN="${SCRIPT_DIR}/${OPTARG}"
       fi
        ;;
    r) fun_run_fio $OPTARG
       exit
        ;;
    s) fun_show_grid $OPTARG
       exit
        ;;
    t) OSD_TYPE=$OPTARG
        ;;
    j) MULTI_JOB_VOL=true
        ;;
    l) LATENCY_TARGET=true
        ;;
    p) PRECOND=true
        ;;
    g) REGEN=false
        ;;
    z) CACHE_ALG=$OPTARG
       if [ "$CACHE_ALG" != "LRU" ] && [ "$CACHE_ALG" != "2Q" ]; then
         echo -e "${RED}== Invalid cache algorithm: ${CACHE_ALG} ==${NC}"
         exit 1
       fi
       ;;
   x) SKIP_EXEC=true
       ;;
    :) printf "missing argument for -%s\n" "$OPTARG" >&2
       usage >&2
       exit 1
       ;;
  esac
 done

 echo -e "${GREEN}== OSD_TYPE ${OSD_TYPE} BALANCE ${BALANCE} ==${NC}"
 echo -e "${GREEN}== Loading test plan from ${TEST_PLAN} ==${NC}"
 source $TEST_PLAN

 # Create the run directory if it does not exist
 [ ! -d "${RUN_DIR}" ] && mkdir -p ${RUN_DIR}
 fun_save_test_plan
 cd /ceph/build/

 if [ "$REGEN" = true ]; then
     fun_run_regen_fio_files
 fi
 if [ "$PRECOND" = true ]; then
     fun_run_precond "precond"
 fi
 if [ "$OSD_TYPE" == "all" ]; then
     for OSD_TYPE in classic sea; do # cyan blue 
         fun_run_bal_vs_default_tests ${OSD_TYPE} ${BALANCE}
     done
 else
    echo -e "${GREEN}==fun_run_bal_vs_default_tests: OSD_TYPE ${OSD_TYPE} BALANCE ${BALANCE} ==${NC}"
     fun_run_bal_vs_default_tests ${OSD_TYPE} ${BALANCE}
 fi
 exit
