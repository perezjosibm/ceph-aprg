#!/usr/bin/env bash
# Example of a worker script that performs a specific task, while can be monitored by a watchdog.
# # It handles signals to ensure proper cleanup on termination.
# # Usage: ./worker.sh 

fun_tidyup() {
    echo "$(date): [worker] Cleaning up before exit..."
}

#############################################################################################
#trap "exit" INT TERM
#trap "kill 0" EXIT
# 
trap 'echo "$(date):[worker]== Got signal from parent, quiting =="; kill -9 ${fio_id[@]}; jobs -p | xargs -r kill -9; fun_tidyup ${TEST_RESULT} _failed; exit 1' SIGINT SIGTERM SIGHUP

#############################################################################################

FIO_CMD="fio --name=global --ioengine=libaio --rw=write --bs=4k --size=1G --numjobs=1 --time_based --runtime=60 --group_reporting --filename=/tmp/testfile"

while true; do
    echo "$(date): Running FIO command: $FIO_CMD"
    eval echo $FIO_CMD 
    fio_id+=($!)
    wait ${fio_id[@]}
    FIO_EXIT_CODE=$?
    if [[ $FIO_EXIT_CODE -ne 0 ]]; then
        echo "$(date): FIO command failed with exit code $FIO_EXIT_CODE"
        fun_tidyup ${TEST_RESULT} _failed
        exit 1
    else
        echo "$(date): FIO command completed successfully."
    fi
    sleep 5
done

