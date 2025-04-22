
set terminal pngcairo size 650,280 enhanced font 'Verdana,10'
set output 'MSGR_server_msgr_crimson_56smp_56clients_balanced_top_mem.png'
set key outside horiz bottom center box noreverse noenhanced autotitle
set datafile missing '-'
set datafile separator ","
set timefmt "%Y-%m-%d %H:%M:%S"
#set format x "%Y-%m-%d %H:%M:%S"
#set format y "%2.3f"
set format y '%.0s%c'
set style data lines
set xtics border in scale 1,0.5 nomirror rotate by -45  autojustify
set title "MSGR-server-msgr-crimson-56smp-56clients-balanced-top-mem"
set ylabel 'MEM%'
set grid
set key autotitle columnheader

# Each column is a thread name
plot 'MSGR_server_msgr_crimson_56smp_56clients_balanced_top_mem.dat' using 1 w lp, for [i=2:10] '' using i w lp
#plot 'MSGR_server_msgr_crimson_56smp_56clients_balanced_top_mem.dat' using 1:1 title columnheader(1) w lp, for [i=3:10] '' using i:i title columnheader(i) w lp

#set logscale y
#set output 'MSGR_server_msgr_crimson_56smp_56clients_balanced_top_mem-log.png'
#plot 'MSGR_server_msgr_crimson_56smp_56clients_balanced_top_mem.dat' using 1:2 title columnheader(2) w lp, for [i=3:10] '' using 1:i title columnheader(i) w lp
