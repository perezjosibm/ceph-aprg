#!env/python3
"""
    TestPlan
        cluster: Cluster
        benchmarks: Benchmarks
    Cluster
        name, user, head, ceph_conf, ceph_keyring, clients, osds
        configurations: dict[str, BaseClusterConfiguration]
    BaseClusterConfiguration
        osd_type, osd_range, store_devs, pool_type, num_rbd_images, rbd_image_size
    SeastoreClusterConfiguration(BaseClusterConfiguration)
        reactor_core_range
    ClassicClusterConfiguration(BaseClusterConfiguration)
        classic_cpu_set
    Benchmarks
        librbdfio: LibrbdFio
        workloads: Workloads
    Workloads
        precondition, randwrite, randread, seqwrite, seqread (each a Workload)


Key points to implement:

    TestPlan.load(path) (or load_test_plan(path)) reads JSON and returns a TestPlan.
    cluster.configurations becomes dict[str, ClusterConfiguration].
    osd_type selects subclass:
        "classic" → ClassicClusterConfiguration (expects classic_cpu_set)
        "seastore" → SeastoreClusterConfiguration (expects reactor_core_range, but per your answer it should be reactor counts, so rename to reactor_range or keep name but parse as List[int])
    benchmarks.workloads becomes a dict of Workload objects (precondition/randwrite/randread/seqwrite/seqread).
    Validation: fail fast if required keys are missing or if reactor_core_range contains non-integers.

Suggested schema adjustments (minimal impact):

    Keep JSON key reactor_core_range, but change its values to ints, e.g. [56] or [8,16,32,56].
    In code, expose it as reactor_range: list[int] for clarity.


"""

import jsonrom dataclasses import dataclass, field
from typing import List, Type, Dict, Any


@dataclass
class TestPlan:
    clusters: List['Cluster']


@dataclass
class Cluster:
    name: str
    benchmarks: List['Benchmarks']


@dataclass
class Benchmarks:
    rbdfio: 'LibrbdFio'


@dataclass
class LibrbdFio:
    workload: 'Workload'


@dataclass
class Workload:
    name: str


class ClusterConfiguration:
    pass


class ClassicClusterConfiguration(ClusterConfiguration):
    pass


class SeastoreClusterConfiguration(ClusterConfiguration):
    pass


def load_test_plan(json_file: str) -> TestPlan:
    with open(json_file, 'r') as f:
        data = json.load(f)
    # Parsing logic here
    return TestPlan(clusters=[])  # Populate clusters accordingly


def validate_plan(test_plan: TestPlan) -> None:
    # Add validation logic
    pass


def factory(osd_type: str) -> Type[ClusterConfiguration]:
    if osd_type == "classic":
        return ClassicClusterConfiguration
    elif osd_type == "seastore":
        return SeastoreClusterConfiguration
    else:
        raise ValueError(f"Unknown osd_type: {osd_type}")

