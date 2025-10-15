#!/usr/bin/env bash
# Example of a watchdog script that restarts a service if it is not running.
#
#To obtain the command name of the current script that is running, use:
# ps -q $$ -o comm=
#
# To get process information on all running scripts that have the same name as the current script, use:
# ps -C "$(ps -q $$ -o comm=)"
#
# To find just the process IDs of all scripts currently being run that have the same name as the current script, use:
# pgrep "$(ps -q $$ -o comm=)"
#
WATCHDOG=false
pid_watchdog=0 
pid_fio=0
pid_main=0
pname=$(ps -q $$ -o comm=)

fun_launch_main () {
    local pnames=$1
    echo "$(date): Starting main process: $pnames"
    # Simulate a long-running process (replace this with the actual command)
    sleep 30
    echo "$(date): Main process $pnames has completed."
}

#############################################################################################
# Stop the cluster and kill the worker  process 
fun_stop() {
    local pid_fio=$1

    echo "$(date)== Stopping the cluster... =="
    echo /ceph/src/stop.sh --crimson
    if [[ $pid_fio -ne 0 ]]; then
         echo "$(date)== Killing workers with pid $pid_fio... =="
         kill -15 $pid_fio
         #pkill -9 -P $pnames
    fi
    # kill -9 $(pgrep -f fio)
    # Kill all the background jobs
    #jobs -p | xargs -r kill
    # remaining process in the group
    #kill 0
}

#############################################################################################
#############################################################################################
# Watchdog to monitor the OSD process, if it dies, kill FIO and exit
fun_watchdog() {
    local pid_fio=$1
    # Check if the process pid_main is running

    while ps -p $pid_main >/dev/null 2>&1 && [[ "$WATCHDOG" == "true" ]]; do
        sleep 1
    done
    # If we reach here, it means the OSD process is not running
    # We can stop the FIO process and exit
    if [[ "$WATCHDOG" == "true" ]]; then
        WATCHDOG=false
        echo "$(date)== main process not running, quitting ... =="
        fun_stop $pid_fio
    fi 
}

trap 'echo "$(date)== INT received, exiting... =="; fun_stop; exit 1' SIGINT SIGTERM SIGHUP
# Launch the main process in the background
fun_launch_main "main" &
pid_main=$!
echo "$(date): Launched main process with PID $pid_main"
echo "$(date) Launching worker"
( ./worker.sh >> /tmp/worker.log ) &
pid_fio=$!
echo "$(date): Launched worker process with PID $pid_fio"

echo "$(date) Starting watchdog"
WATCHDOG=true
( fun_watchdog $pid_fio ) &
pid_watchdog=$!
#ps -ef | grep main | grep -v grep
ps -ef 

echo "$(date): Waiting for process with PID $pid_fio"
wait $pid_fio

