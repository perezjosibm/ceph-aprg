import logging
import re

__author__ = "Dave Pinkney"

logger = logging.getLogger(__name__)


class Job(object):
    JOB_PPID = "ppid"  # string
    JOB_PID = "pid"  # string
    JOB_P = "last_cpu"  # int
    JOB_USER = "user"  # string
    JOB_PR = "priority"  # string
    JOB_NI = "nice"  # int
    JOB_VIRT = "memVirtual"  # int   in KiB
    JOB_RES = "memResident"  # int   in KiB
    JOB_SHR = "memShared"  # int   in KiB
    JOB_STATUS = "status"  # string
    JOB_CPU = "cpuPercent"  # float
    JOB_MEM = "memPercent"  # float
    JOB_TIME = "cpuTotalTime"  # string
    JOB_COMMAND = "command"  # string
    RE_JOBS_COL = {
        "PPID": {"regex": re.compile(r"(\d+)"), "name": JOB_PPID},  # PPID
        "PID": {"regex": re.compile(r"(\d+)"), "name": JOB_PID},  # PID
        "P": {"regex": re.compile(r"(\d+)"), "name": JOB_P},  # last_cpu
        "PR": {"regex": re.compile(r"([-\w]+)"), "name": JOB_PR},  # priority
        "USER": {"regex": re.compile(r"(\w+)"), "name": JOB_USER},  # user
        "NI": {"regex": re.compile(r"([-\d]+)"), "name": JOB_NI},  # nice
        "VIRT": {
            "regex": re.compile(r"(\d+[.\d]*\w?)"),
            "name": JOB_VIRT,
        },  # memVirtual
        "RES": {"regex": re.compile(r"(\d+[.\d]*\w?)"), "name": JOB_RES},  # memResident
        "SHR": {"regex": re.compile(r"(\d+[.\d]*\w?)"), "name": JOB_SHR},  # memShared
        "S": {"regex": re.compile(r"(\w+)"), "name": JOB_STATUS},  # status
        "%CPU": {"regex": re.compile(r"([.\d]+)"), "name": JOB_CPU},  # cpuPercent
        "%MEM": {"regex": re.compile(r"([.\d]+)"), "name": JOB_MEM},  # memPercent
        "TIME+": {
            "regex": re.compile(r"(\d+:\d+[.\d]*)"),
            "name": JOB_TIME,
        },  # cpuTotalTime
        "COMMAND": {"regex": re.compile(r"(.+)"), "name": JOB_COMMAND},  # command
    }
    RE_JOB_RES = re.compile(r"^(\d+)$")
    RE_JOB_RES_SCALED = re.compile(r"^(\d+[.\d]*)([a-z])$")
    METRIC_MEM = [JOB_VIRT, JOB_RES, JOB_SHR]
    METRIC_FLOAT = [JOB_CPU, JOB_MEM]
    RE_DEFAULT_JOB_HEADER = (
        " PID USER      PR  NI    VIRT    RES    SHR S  %CPU %MEM     TIME+ COMMAND"
    )

    def __init__(self, columns=None):
        """ """
        self.info = {}
        if columns is not None:
            self.columns = columns
        else:
            self.columns = filter(None, re.split(r"\s+|\n", self.RE_DEFAULT_JOB_HEADER))

    def __str__(self):
        """Convert to string, for str()."""
        return "Job({0})".format(self.info)

    def getPid(self):
        """Returns the pid for this job"""
        return self.info[self.JOB_PID]

    def getPPid(self):
        """Returns the ppid for this job"""
        return self.info[self.JOB_PPID]

    def getCpu(self):
        """Returns the cpu util this job"""
        return self.info[self.JOB_CPU]

    def getLastCpu(self):
        """Returns the last cpu used by this job"""
        return self.info[self.JOB_P]

    def getMem(self):
        """Returns the mem util for this job"""
        return self.info[self.JOB_MEM]

    def getRes(self):
        """Returns the RES mem util for this job"""
        return self.info[self.JOB_RES]

    def getShr(self):
        """Returns the SHR mem util for this job"""
        return self.info[self.JOB_SHR]

    def getCommand(self):
        """Returns the comm name for this job"""
        return self.info[self.JOB_COMMAND]

    def parse(self, line: str):
        """
        Parse this job's state from a line of top output
        Sample input:
        '  662 root      20   0  273524  86820  17340 S   6.2  0.5 338:15.30 Xorg'
        '32469 dpinkney  20   0 3920412 2.403g  72804 S   6.2 15.4   2709:11 firefox'
        ' 5199 postgres  10 -10  436m   9m 7904 S  0.0  0.1   0:00.05 postmaster   '
        """
        logger.debug("Parsing job '{0}'".format(line))
        # Traverse the columns array, expect each item to match the corresponding column
        tokens = filter(None, re.split(r"\s+|\n", line))
        # Invariant: size of token list should be the same size as columns
        for col in self.columns:
            metric: dict = self.RE_JOBS_COL[col]
            col_match = metric["regex"].match(next(tokens))
            if col_match:
                groups = col_match.groups()
                # logger.debug("Got groups: {0}".format(groups))
                if metric["name"] in self.METRIC_MEM:
                    self.info[metric["name"]] = self.parseScaledMem(groups[0])
                elif metric["name"] in self.METRIC_FLOAT:
                    self.info[metric["name"]] = float(groups[0])
                elif metric["name"] == self.JOB_NI:
                    self.info[metric["name"]] = int(groups[0])
                elif metric["name"] == self.JOB_COMMAND:
                    self.info[metric["name"]] = groups[0].strip()
                else:
                    self.info[metric["name"]] = groups[0]
            else:
                logger.error(
                    "Failed to parse {0} from {1}".format(metric["name"], line)
                )
                # raise Exception ("Failed to parse {0} from {1}".format(metric['name'], line))
                return False
        return True

    def parseScaledMem(self, resStr: str):
        """
        The memory values may contain a postfix, in which case we should convert it from mb or gb to kb
        :return: The memory value in MiB
        """
        logger.debug("Parsing Mem from {0}".format(resStr))

        match = self.RE_JOB_RES.match(resStr)
        if match:
            return int(match.groups()[0]) / 1024
        else:
            match = self.RE_JOB_RES_SCALED.match(resStr)
            if match:
                groups = match.groups()
                value = float(groups[0])

                if groups[1] == "m":
                    return int(value)
                elif groups[1] == "g":
                    return int(value * 1024)
                elif groups[1] == "t":
                    return int(value * 1024 * 1024)
                else:
                    raise Exception("Unknown value in {0}".format(resStr))
            else:
                raise Exception("Unknown value in {0}".format(resStr))
