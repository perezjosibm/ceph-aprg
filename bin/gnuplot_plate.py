import logging
import re

__author__ = 'Jose J Palacios-Perez'
logger = logging.getLogger(__name__)

class GnuplotTemplate(object):
    TIMEFORMAT = '"%Y-%m-%d %H:%M:%S"'
    NUMCOLS = 10 # by default, but should be set during construction
    def __init__(self, name:str, proc_groups:dict, num_samples:int):
        """
        Constructor: expect a dictionary:
        keys: threads names, 
        values: each a list of metrics (dictionary)

        <thread_id>:
           <metrics:>
             'cpu':
                _data: [samples]
                avg: geometric average (mean)
                min: numeric
                max: numeric
             'mem': <similar struct>
             'swctx': # number of context switch, taken from last_cpu
             'num_cpus': # number of CPU cores seen in last_cpu samples
        And we probably need a list of the top ten thread_id according to
        CPU and MEM util
        """
        self.name = name
        self.proc_groups = proc_groups
        self.num_samples = num_samples

    def __str__(self):
        """Convert to string, for str()."""
        return "Job({0})".format(self.name)

    def genPlot(self, metric:str, proc_name:str):
        """Produce the output .plot and .dat for the process group proc_name with metric"""
        out_name = f"{proc_name}_{self.name}"
        out_name = re.sub(r"[.]out",f"_{metric}", out_name)
        dat_name = f"{out_name}.dat"
        png_name = f"{out_name}.png"
        plot_name = f"{out_name}.plot"
        png_log_name = f"{out_name}-log.png"
        chart_title = re.sub(r"[_]","-",out_name)
        plot_template=f"""
set terminal pngcairo size 650,280 enhanced font 'Verdana,10'
set output '{png_name}'
set key outside horiz bottom center box noreverse noenhanced autotitle
set datafile missing '-'
set datafile separator ","
set timefmt {self.TIMEFORMAT}
#set format x {self.TIMEFORMAT}
#set format y "%2.3f"
set format y '%.0s%c'
set style data lines
set xtics border in scale 1,0.5 nomirror rotate by -45  autojustify
set title "{chart_title}"
set ylabel '{metric.upper()}%'
set grid
set key autotitle columnheader

# Each column is a thread name
plot '{dat_name}' using 1 w lp, for [i=2:{self.NUMCOLS}] '' using i w lp
#plot '{dat_name}' using 1:1 title columnheader(1) w lp, for [i=3:{self.NUMCOLS}] '' using i:i title columnheader(i) w lp

#set logscale y
#set output '{png_log_name}'
#plot '{dat_name}' using 1:2 title columnheader(2) w lp, for [i=3:{self.NUMCOLS}] '' using 1:i title columnheader(i) w lp
"""
        # generate dat file: order as described by self.proc_groups[pg]['sorted'][metric] 
        print(f"== Proc grp: {proc_name}:{metric} ==")
        comm_sorted = self.proc_groups[proc_name]['sorted'][metric]
        #print( comm_sorted , sep=", " )
        header ="#" +  ','.join(comm_sorted)
        print(header)
        ds = {}
        # Either use num_samples or count for each comm
        for comm in comm_sorted:
            _data = self.proc_groups[proc_name]['threads'][comm][metric]['_data']
            ds[comm] = iter(_data)

        with open(dat_name,'w') as f:
            print(header, file=f)
            for i in range(self.num_samples):
                row = []
                for comm in comm_sorted:
                    row.append(f"{next(ds[comm]):.2f}")
                    #catch exception if no data at that index: use a '-'
                print( ','.join(row), file=f)
            f.close()

        # print plot_template
        with open(plot_name,'w') as f:
           f.write(plot_template) 
           f.close()

