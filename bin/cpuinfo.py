import json
import subprocess


def get_cpu_info():
    out = subprocess.check_output(["lscpu", "--json"])
    lscpu = json.loads(out)

    kvs = {dic["field"]: dic["data"] for dic in lscpu["lscpu"]}

    return {
        "cpu-vendor": kvs.get("Vendor ID:", "NA"),
        "cpu-model": kvs.get("Model name:", "NA"),
        "arch": kvs.get("Architecture:", "NA"),
        "cpu-count": kvs.get("CPU(s):", "NA"),
        "cpu-thread-per-core": kvs.get("Thread(s) per core:", "NA"),
        "cpu-cores-per-socket": kvs.get("Core(s) per socket:", "NA"),
        "cpu-sockets": kvs.get("Socket(s)", "NA"),
        "cpu-flags": kvs.get("Flags:", "NA"),
    }


if __name__ == "__main__":
    print(get_cpu_info())

