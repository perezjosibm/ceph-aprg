import datetime
import logging
import re
from enum import Enum
#import json
from json import JSONEncoder

from job import Job

__author__ = "Dave Pinkney"

logger = logging.getLogger(__name__)
# logger = logging.getLogger('spam_application')


class _CpuLineType(Enum):
    SINGLE = "single"
    MULTI = "multi"

    @classmethod
    def get_type(cls, line: str) -> "_CpuLineType":
        if re.match(r"\(s\)", line):
            return cls.SINGLE
        elif re.match(r"\d+", line):
            return cls.MULTI
        else:
            return None


class TopEntry(object):
    """
    This class represents the output from one iteration of top.
    It will read top output from a file and initialize itself from it.
    """

    YEAR = datetime.date.today().year

    # Define Regular Expressions and Header Field Names

    # Date / Timestamp header - Not part of standard top output. Will be manufactured if needed using uptime.
    DATE = "date"  # int:  the datetime.date ordinal value (days since 70)
    RE_DATE = re.compile(r"^(\d+)/(\d+)")

    # Uptime
    TIME_OF_DAY = "timeOfDay"  # string
    UPTIME_MINUTES = "uptimeMinutes"  # int
    NUM_USERS = "numUsers"  # int
    LOAD_1_MINUTE = "1 minute load"  # float
    LOAD_5_MINUTES = "5 minute load"  # float
    LOAD_15_MINUTES = "15 minute load"  # float
    # Uptime has variable time unit output, might be days, minutes, or just hours:min

    RE_UPTIME = re.compile(
        r"""^top\s+-
                              \s+(\d+):(\d+):(\d+)                                 # time of day
                              \s+up
                              \s+(\d+\s+days?,\s+\d+:\d+                           # uptime
                                  |\d+\s+days?,\s+\d+\s+mins?                      # uptime
                                  |\d+\s+mins?                                     # uptime
                                  |\d+:\d+),                                       # uptime
                              \s+(\d+)\s+users?,                                   # num users
                              \s+load\s+average:
                              \s+([\d.]+),\s+([\d.]+),\s+([\d.]+)                  # load
                              """,
        re.VERBOSE,
    )

    RE_UPTIME_DAYS = re.compile(r"(\d+)\s+days?,\s+(\d+):(\d+)")
    RE_UPTIME_DAYS_MIN = re.compile(r"(\d+)\s+days?,\s+(\d+)\s+mins?")
    RE_UPTIME_HOUR = re.compile(r"(\d+):(\d+)")
    RE_UPTIME_MIN = re.compile(r"(\d+)\s+mins?")

    # Tasks
    TASKS_TOTAL = "tasksTotal"  # int
    TASKS_RUNNING = "tasksRunning"  # int
    TASKS_SLEEPING = "tasksSleeping"  # int
    TASKS_STOPPED = "tasksStopped"  # int
    TASKS_ZOMBIE = "tasksZombie"  # int
    RE_TASKS = re.compile(
        r"^(?:Tasks|Threads):\s+(\d+) total,\s+(\d+)\s+running,\s+(\d+)\s+sleeping,\s+(\d+)\s+stopped,\s+(\d+)\s+zombie"
    )

    # CPU
    CPU_UNNICED = "cpuUser"  # float
    CPU_SYSTEM = "cpuSystem"  # float
    CPU_NICED = "cpuNiced"  # float
    CPU_IDLE = "cpuIdle"  # float
    CPU_WAIT = "cpuIoWait"  # float
    CPU_HI = "cpuHardwareInt"  # float
    CPU_SI = "cpuSoftwareInt"  # float
    CPU_ST = "cpuStolen"  # float
    RE_CPU = re.compile(
        r"""^[%]?Cpu(\d+|\(s\))\s*:   # single line or multi-core
                        \s*([.\d]+)[%\s]us,          # user
                        \s*([.\d]+)[%\s]sy,          # system
                        \s*([.\d]+)[%\s]ni,          # nice
                        \s*([.\d]+)[%\s]id,          # idle
                        \s*([.\d]+)[%\s]wa,          # wait
                        \s*([.\d]+)[%\s]hi,          # hard int
                        \s*([.\d]+)[%\s]si,          # sw int
                        \s*([.\d]+)[%\s]st""",  # stolen
        re.VERBOSE,
    )

    # Memory
    MEM_TOTAL = "memTotal"
    MEM_USED = "memUsed"
    MEM_FREE = "memFree"
    MEM_BUFFERS = "memBuffers"
    RE_MEM = re.compile(
        r"""^(?:[KMG]iB\s)?Mem:
                        \s+([.\d]+)k?\stotal,
                        \s+([.\d]+)k?\s(used|free),
                        \s+([.\d]+)k?\s(free|used),
                        \s+([.\d]+)k?\s(buffers?|buff/cache)
                        """,
        re.VERBOSE,
    )  # |re.DEBUG)

    # Swap
    SWAP_TOTAL = "swapTotal"
    SWAP_USED = "swapUsed"
    SWAP_FREE = "swapFree"
    SWAP_CACHED = "swapCached"

    RE_SWAP = re.compile(
        r"""^(?:[KMG]iB\s)?Swap:
                        \s+([.\d]+)k?\s+total,
                        \s+([.\d]+)k?\s(free|used),
                        \s+([.\d]+)k?\s(used|free)[.,]
                        \s+([.\d]+)k?\s(cached|avail Mem)$""",
        re.VERBOSE,
    )  # |re.DEBUG)
    # Jobs
    # RE_JOB_HEADER = re.compile(r'^\s+PID\s+USER\s+PR\s+NI\s+VIRT\s+RES\s+SHR\s+S\s+%CPU\s+%MEM\s+TIME\+\s+COMMAND')
    RE_JOB_HEADER = re.compile(
        r"^\s+(PPID|PID|P|PR|NI|VIRT|RES|SHR|S|%CPU|%MEM|TIME|COMMAND)"
    )

    def __init__(self, hasDate=None):
        """
        : hasDate - boolean - True if we should parse a date before parsing the topEntry, false if we shouldn't,
                              None if not known.
        """
        self.header = {}
        self.header_columns = []
        self.jobs = {}
        self.cores = {}
        self.hasDate = hasDate

    def __str__(self):
        """Convert to string, for str()."""
        return "Header = {0}, {1} Jobs ".format(self.header, len(self.jobs))

    def parse(self, firstLine, f):
        """
        Reads a top entry from f and initializes this object from it.
        :type string: The first line of input read from f, it should be the top header
        :type f - File of top output, with one line consumed
        :throws: Exception if at EOF
        : return: this instance
        """
        self.parseHeader(firstLine, f)
        self.eatBlankLine(f)
        self.parseBody(f)
        logger.debug("Parsed entry: {0}".format(self))
        return self

    def parseHeader(self, firstLine, f):
        """
        :type string: The first line of input read from f, it should be the top header
        :type f - File of top output, with one line consumed

        top - 05:58:39 up 27 days, 16:32, 17 users,  load average: 0.01, 0.04, 0.05
        Tasks: 285 total,   1 running, 284 sleeping,   0 stopped,   0 zombie
        %Cpu(s):  2.4 us,  0.5 sy,  0.0 ni, 96.8 id,  0.1 wa,  0.1 hi,  0.0 si,  0.0 st
        KiB Mem:  16355800 total, 15649032 used,   706768 free,   294280 buffers
        KiB Swap:  4095996 total,    96600 used,  3999396 free, 10201704 cached

        or:

        05/27 05:58:39
        top - 05:58:39 up 27 days, 16:32, 17 users,  load average: 0.01, 0.04, 0.05
        Tasks: 285 total,   1 running, 284 sleeping,   0 stopped,   0 zombie
        %Cpu(s):  2.4 us,  0.5 sy,  0.0 ni, 96.8 id,  0.1 wa,  0.1 hi,  0.0 si,  0.0 st
        KiB Mem:  16355800 total, 15649032 used,   706768 free,   294280 buffers
        KiB Swap:  4095996 total,    96600 used,  3999396 free, 10201704 cached

        or:

        top - 07:52:14 up 1177 days, 20:47,  0 users,  load average: 3.23, 3.04, 3.57
        Threads:  41 total,   3 running,  38 sleeping,   0 stopped,   0 zombie
        %Cpu0  : 57.1 us, 35.7 sy,  0.0 ni,  0.0 id,  0.0 wa,  0.0 hi,  7.1 si,  0.0 st
        :
        %Cpu111:  0.0 us,  0.0 sy,  0.0 ni,100.0 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st
        MiB Mem : 385320.3 total, 253208.1 free,  17781.7 used, 120940.3 buff/cache
        MiB Swap:   4096.0 total,   4094.7 free,      1.2 used. 367538.6 avail Mem


        :return: a TopEntry instance
        :throws: Exception if at EOF
        """

        if self.hasDate is None or self.hasDate:
            firstLine = self.parseDate(f, firstLine)

        self.parseUptime(firstLine)
        self.parseTasks(self.readline(f))

        flag = True
        cpu_type = None
        line = ""
        while flag:
            line = self.readline(f)
            cpu_type = self.parseCpu(line)
            if cpu_type != _CpuLineType.MULTI:
                flag = False

        if cpu_type == _CpuLineType.SINGLE:
            self.parseMem(self.readline(f))
        else:
            self.parseMem(line)
        self.parseSwap(self.readline(f))

    def parseDate(self, f, line):
        """
        Try to parse the DATE from line.  If we find the DATE, store it in this object,
        set HAS_DATE_INFO, and return the next line of input from f
        :return: The next line of text, or line if it did not match DATE
        """
        match = self.RE_DATE.match(line)
        if match:
            groups = match.groups()
            self.hasDate = True
            self.header[self.DATE] = datetime.date(
                TopEntry.YEAR, int(groups[0]), int(groups[1])
            ).toordinal()
            line = self.readline(f)
        else:
            self.hasDate = False

        return line

    def getDateFromUptimeMinutes(self, uptimeMinutes):
        """
        For cases where we don't have a timestamp set, fake a date using the
        uptime minutes, so we can always plot with a date and time.  Starting day 1 at 1/1/2000 so that
        plotting software won't break
        : uptimeMinutes:  int - The uptime minutes
        : return - int - the ordinal date to use
        """
        days = int(uptimeMinutes / (24 * 60))
        return datetime.date(2000, 1, 1).toordinal() + days

    def parseUptime(self, line):
        """
        Parse uptime state out of a string and update this object's state.
        The string format can be one of:
        top - 05:58:39 up 27 days, 16:32, 17 users,  load average: 0.01, 0.04, 0.05
        top - 10:53:52 up 2 min,  1 user,  load average: 0.21, 0.16, 0.06
        top - 11:51:42 up  1:00,  1 user,  load average: 0.00, 0.01, 0.05
        """
        logger.debug("Parsing uptime from '{0}'".format(line))
        match = self.RE_UPTIME.match(line)
        if match:
            groups = match.groups()
            # logger.debug("Got groups: {0}".format(groups))

            # Can't recall why I broke this out into components - to plot time as #seconds?
            hours = groups[0]
            minutes = groups[1]
            seconds = groups[2]
            self.header[self.TIME_OF_DAY] = "{0}:{1}:{2}".format(
                hours, minutes, seconds
            )

            self.header[self.UPTIME_MINUTES] = self.parseUptimeMinutes(groups[3])
            self.header[self.NUM_USERS] = int(groups[4])
            self.header[self.LOAD_1_MINUTE] = float(groups[5])
            self.header[self.LOAD_5_MINUTES] = float(groups[6])
            self.header[self.LOAD_15_MINUTES] = float(groups[7])

            if not self.hasDate:
                # Set a fake date using uptime,
                self.header[self.DATE] = self.getDateFromUptimeMinutes(
                    self.header[self.UPTIME_MINUTES]
                )

    def parseUptimeMinutes(self, line):
        """
        Parse the uptime minutes and return it.
        The uptime unit varies from seconds to minutes, hour and days.
        Example:
        '27 days, 16:32'
        '1:00'
        '2 min'
        """
        match = self.RE_UPTIME_DAYS.match(line)
        if match:
            groups = match.groups()
            # logger.debug("Parsed groups: {0}".format(groups))
            minutes = int(groups[0]) * 24 * 60 + int(groups[1]) * 60 + int(groups[2])
            return minutes

        match = self.RE_UPTIME_DAYS_MIN.match(line)
        if match:
            groups = match.groups()
            # logger.debug("Parsed groups: {0}".format(groups))
            minutes = int(groups[0]) * 24 * 60 + int(groups[1])
            return minutes

        match = self.RE_UPTIME_HOUR.match(line)
        if match:
            groups = match.groups()
            # logger.debug("Parsed groups: {0}".format(groups))
            minutes = int(groups[0]) * 60 + int(groups[1])
            return minutes

        match = self.RE_UPTIME_MIN.match(line)
        if match:
            groups = match.groups()
            # logger.debug("Parsed groups: {0}".format(groups))
            minutes = int(groups[0])
            return minutes

        raise Exception("Could not parse uptime: {0}".format(line))

    def parseTasks(self, line):
        """
        Parse the tasks info from line and update this object's state
        Example input:
        Tasks: 285 total,   1 running, 284 sleeping,   0 stopped,   0 zombie
        """
        logger.debug("Parsing tasks from [{0}]".format(line))

        match = self.RE_TASKS.match(line)
        if match:
            groups = match.groups()
            # logger.debug("Got groups: {0}".format(groups))

            self.header[self.TASKS_TOTAL] = int(groups[0])
            self.header[self.TASKS_RUNNING] = int(groups[1])
            self.header[self.TASKS_SLEEPING] = int(groups[2])
            self.header[self.TASKS_STOPPED] = int(groups[3])
            self.header[self.TASKS_ZOMBIE] = int(groups[4])

    def getCpuLine(self, groups):
        """
        Use the array to traverse the expected keys over the matching groups
        """
        columns = [
            self.CPU_UNNICED,
            self.CPU_SYSTEM,
            self.CPU_NICED,
            self.CPU_IDLE,
            self.CPU_WAIT,
            self.CPU_HI,
            self.CPU_SI,
            self.CPU_ST,
        ]
        cpu_entry = {}
        for col in columns:
            cpu_entry[col] = float(next(groups))
        return cpu_entry

    def parseCpu(self, line):
        """
        Parse the cpu information from line and store it in this object's state
        %Cpu(s):  2.4 us,  0.5 sy,  0.0 ni, 96.8 id,  0.1 wa,  0.1 hi,  0.0 si,  0.0 st

        or

        %Cpu25 :  0.0 us,  0.0 sy,  0.0 ni,100.0 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st
        Returns relevant cpu_type, none otherwise
        """
        logger.debug("Parsing Cpu from: {0}".format(line))

        match = self.RE_CPU.match(line)
        if match:
            groups = match.groups()
            cpu_type = _CpuLineType.get_type(groups[0])
            # logger.debug(f"Got type: {cpu_type}")
            cpu_line = self.getCpuLine(iter(groups[1:]))
            if cpu_type is not None:
                if cpu_type == _CpuLineType.SINGLE:
                    # self.header = { **self.header, **cpu_line }
                    for k in cpu_line.keys():
                        self.header[k] = cpu_line[k]
                else:  # _CpuLineType.MULTI:
                    self.cores[groups[0]] = cpu_line
                logger.debug(f"Got {cpu_type}: {cpu_line}")  # {self.header}
                return cpu_type
        logger.debug(f"Did not match valid CPU line: {line}")
        return None

    def parseMem(self, line):
        """
        Parse the mem information from line and store it in this object's state
        KiB Mem:  16355800 total, 15649032 used,   706768 free,   294280 buffers
          or
        Mem:   8170096k total,  7148836k used,  1021260k free,   337592k buffers
        """
        logger.debug("Parsing Mem from {0}".format(line))

        match = self.RE_MEM.match(line)
        if match:
            groups = match.groups()
            # logger.debug("Got groups: {0}".format(groups))
            self.header[self.MEM_TOTAL] = int(groups[0])
            if groups[2] == "free":
                self.header[self.MEM_FREE] = int(groups[1])
            elif groups[2] == "used":
                self.header[self.MEM_USED] = int(groups[1])
            if groups[4] == "free":
                self.header[self.MEM_FREE] = int(groups[3])
            elif groups[4] == "used":
                self.header[self.MEM_USED] = int(groups[3])
            self.header[self.MEM_BUFFERS] = int(groups[5])
        else:
            logger.debug(f"Did not match mem: {line}")

    def parseSwap(self, line):
        """
        Parse the swap information from line and store it in this object's state
        KiB Swap:  4095996 total,    96600 used,  3999396 free, 10201704 cached
          or
        Swap:  3146748k total,   905060k used,  2241688k free,  1705700k cached'
        """
        logger.debug("Parsing Swap from: {0}".format(line))

        match = self.RE_SWAP.match(line)
        if match:
            groups = match.groups()
            # logger.debug("Got groups: {0}".format(groups))
            self.header[self.SWAP_TOTAL] = int(groups[0])
            if groups[2] == "free":
                self.header[self.SWAP_FREE] = int(groups[1])
            elif groups[2] == "used":
                self.header[self.SWAP_USED] = int(groups[1])
            if groups[4] == "free":
                self.header[self.SWAP_FREE] = int(groups[3])
            elif groups[4] == "used":
                self.header[self.SWAP_USED] = int(groups[3])
            self.header[self.SWAP_CACHED] = int(groups[5])

    def parseBody(self, f):
        """
          :type f - File of top output. Next line should be the header for the jobs section
          :return: a TopEntry instance
          Expected input format:

            PID USER      PR  NI    VIRT    RES    SHR S  %CPU %MEM     TIME+ COMMAND
           1453 dpinkney  20   0 1983544 409608  44200 S  12.5  2.5 480:36.59 cinnamon
            662 root      20   0  273524  86820  17340 S   6.2  0.5 338:15.30 Xorg

            or (thread view)

        PPID     PID   P  PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
        1  170737   1  20   0 2822456   2.0g  34048 R  62.5   0.5   0:18.35 msgr-worker-0
        1  170738  86  20   0 2822456   2.0g  34048 R  62.5   0.5   0:39.40 msgr-worker-1

        """
        # strip off the header
        self.readHeader(f)

        while True:
            line = f.readline()
            logger.debug("read line: {0}".format(line))
            # Needs an empty line to indicate the end of the jobs section
            if not line or len(line) == 1:
                break
            else:
                job = Job(self.header_columns)
                if job.parse(line):
                    logger.debug("read job: {0}".format(job))
                    if job.getPid() in self.jobs:
                        raise Exception("Duplicate pid: {0}".format(job.getPid()))
                    else:
                        self.jobs[job.getPid()] = job
                else:
                    logger.debug("Did not parse job: {0}".format(line))
                    # raise Exception("Did not parse job: {0}".format(line))
                    break

    def readHeader(self, f):
        """
        Reads the header line from the job section of the top output.
        This just validates that we're at the right point in the file.
        Returns the list of the column names to be identified for each job entry
        """
        line = self.readline(f)
        match = self.RE_JOB_HEADER.match(line)
        if not match:
            raise Exception("Did not parse header correctly: '{0}'".format(line))
        else:
            self.header_columns = list(filter(None, re.split(r"\s+|\n", line)))

    def eatBlankLine(self, f):
        """
        Read a line from f and verify that is it empty.
        Throws an exception if the line has content.
        """
        line = self.readline(f).strip()
        if line:
            raise Exception("Expected blank line, but got: '{0}'".format(line))

    def readline(self, f):
        """
        :type f - File to read from
        :throws: Exception if at EOF
        :return: String - the line that was read
        """
        line = f.readline()
        if line:
            return line
        else:
            raise Exception("Encountered unexpected EOF - discarding incomplete entry.")


class TopEntryJSONEncoder(JSONEncoder):
    def default(self, obj):
        return obj.__dict__
