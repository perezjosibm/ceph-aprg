#!/usr/bin/env python3
"""
perf_test_plan module – load a performance-test-plan JSON and expose typed dataclasses.

Schema
------
PerfTestPlan
    cluster: Cluster
    benchmarks: Benchmarks
Cluster
    name, user, head, ceph_conf, ceph_keyring, clients, osds
    configurations: dict[str, BaseClusterConfiguration]
BaseClusterConfiguration
    osd_type, osd_range, store_devs, pool_type, num_rbd_images, rbd_image_size, vstart_cpu_set
SeastoreClusterConfiguration(BaseClusterConfiguration)
    reactor_range  (JSON key: reactor_core_range)
ClassicClusterConfiguration(BaseClusterConfiguration)
    classic_cpu_set -- deprecated
Benchmarks
    librbdfio: LibrbdFio
    workloads: dict[str, Workload]
LibrbdFio
    cmd_path, fio_cpu_range, fio_workload, runtime
Workload
    name, rw, bs, rwmixread (optional)

Key API
-------
load_test_plan(path)  – read JSON, return TestPlan
validate_plan(plan)   – raise ValueError on schema problems
factory(osd_type)     – return the correct configuration class for *osd_type*
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type


# ---------------------------------------------------------------------------
# Cluster configuration hierarchy
# ---------------------------------------------------------------------------

@dataclass
class BaseClusterConfiguration:
    """Shared fields for every cluster configuration entry."""
    osd_type: str
    osd_range: List[int]
    store_devs: List[str]
    pool_type: str
    num_rbd_images: int
    rbd_image_size: str
    vstart_cpu_set: List[str]


@dataclass
class SeastoreClusterConfiguration(BaseClusterConfiguration):
    """Seastore-specific configuration (crimson OSD)."""
    # JSON key is ``reactor_core_range``; exposed as ``reactor_range``
    reactor_range: List[int] = field(default_factory=list)


@dataclass
class ClassicClusterConfiguration(BaseClusterConfiguration):
    """Classic (BlueStore) OSD configuration."""
    #classic_cpu_set: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Benchmark types
# ---------------------------------------------------------------------------

@dataclass
class Workload:
    """A single FIO workload definition."""
    name: str
    rw: str
    bs: str
    rwmixread: Optional[int] = None


@dataclass
class LibrbdFio:
    """
    Librbdfio benchmark engine parameters.
    We might extend for furthe FIO engines, like AIO, etc.
    """
    cmd_path: str
    fio_cpu_range: List[str]
    fio_workload: List[str]
    runtime: int


@dataclass
class Benchmarks:
    """All benchmark specifications for a test plan."""
    librbdfio: LibrbdFio
    workloads: Dict[str, Workload]


# ---------------------------------------------------------------------------
# Cluster and top-level TestPlan
# ---------------------------------------------------------------------------

@dataclass
class Cluster:
    """Cluster-level metadata and per-OSD-type configurations."""
    name: str
    user: str
    head: str
    ceph_conf: str
    ceph_keyring: str
    clients: List[str]
    osds: List[str]
    configurations: Dict[str, BaseClusterConfiguration]


@dataclass
class PerfTestPlan:
    """Root dataclass representing a complete performance test plan."""
    cluster: Cluster
    benchmarks: Benchmarks


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def factory(osd_type: str) -> Type[BaseClusterConfiguration]:
    """Return the configuration class that matches *osd_type*.

    Raises
    ------
    ValueError
        When *osd_type* is not recognised.
    """
    mapping: Dict[str, Type[BaseClusterConfiguration]] = {
        "seastore": SeastoreClusterConfiguration,
        "classic":  ClassicClusterConfiguration,
    }
    if osd_type not in mapping:
        raise ValueError(
            f"Unknown osd_type '{osd_type}'. "
            f"Expected one of: {sorted(mapping)}"
        )
    return mapping[osd_type]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_configuration(
    name: str,
    raw: dict,
) -> BaseClusterConfiguration:
    """Parse one entry from ``cluster.configurations`` and return the
    appropriate :class:`BaseClusterConfiguration` subclass instance.
    """
    osd_type = raw.get("osd_type", "")
    cls = factory(osd_type)

    base_kwargs = dict(
        osd_type=osd_type,
        osd_range=raw["osd_range"],
        store_devs=raw["store_devs"],
        pool_type=raw["pool_type"],
        num_rbd_images=raw["num_rbd_images"],
        rbd_image_size=raw["rbd_image_size"],
        vstart_cpu_set=raw["vstart_cpu_set"],
    )

    if cls is SeastoreClusterConfiguration:
        return SeastoreClusterConfiguration(
            **base_kwargs,
            reactor_range=raw["reactor_core_range"],
        )
    # ClassicClusterConfiguration
    return ClassicClusterConfiguration(
        **base_kwargs,
        #classic_cpu_set=raw.get("classic_cpu_set", []),
    )


def _parse_cluster(raw: dict) -> Cluster:
    """Parse the ``cluster`` section of the JSON."""
    configurations: Dict[str, BaseClusterConfiguration] = {
        cfg_name: _parse_configuration(cfg_name, cfg_data)
        for cfg_name, cfg_data in raw.get("configurations", {}).items()
    }
    return Cluster(
        name=raw["name"],
        user=raw["user"],
        head=raw["head"],
        ceph_conf=raw["ceph_conf"],
        ceph_keyring=raw["ceph_keyring"],
        clients=raw["clients"],
        osds=raw["osds"],
        configurations=configurations,
    )


def _parse_benchmarks(raw: dict) -> Benchmarks:
    """Parse the ``benchmarks`` section of the JSON."""
    librbdfio_raw = raw["librbdfio"]
    invariant = librbdfio_raw.get("invariant", {})
    librbdfio = LibrbdFio(
        cmd_path=librbdfio_raw["cmd_path"],
        fio_cpu_range=invariant.get("fio_cpu_range", []),
        fio_workload=invariant.get("fio_workload", []),
        runtime=invariant.get("runtime", 180),
    )

    workloads: Dict[str, Workload] = {
        wl_name: Workload(
            name=wl_data["name"],
            rw=wl_data["rw"],
            bs=wl_data["bs"],
            rwmixread=wl_data.get("rwmixread"),
        )
        for wl_name, wl_data in raw.get("workloads", {}).items()
    }

    return Benchmarks(librbdfio=librbdfio, workloads=workloads)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_plan(plan: PerfTestPlan) -> None:
    """Validate a :class:`PerfTestPlan` instance.

    Raises
    ------
    ValueError
        When required fields are missing or contain unexpected values.
    """
    # Cluster must have at least one configuration
    if not plan.cluster.configurations:
        raise ValueError("PerfTestPlan.cluster.configurations must not be empty")

    for cfg_name, cfg in plan.cluster.configurations.items():
        if not cfg.osd_range:
            raise ValueError(
                f"Configuration '{cfg_name}': osd_range must not be empty"
            )
        if not cfg.vstart_cpu_set:
            raise ValueError(
                f"Configuration '{cfg_name}': vstart_cpu_set must not be empty"
            )
        if not all(isinstance(n, int) for n in cfg.osd_range):
            raise ValueError(
                f"Configuration '{cfg_name}': osd_range must contain integers"
            )
        if isinstance(cfg, SeastoreClusterConfiguration):
            if not cfg.reactor_range:
                raise ValueError(
                    f"Configuration '{cfg_name}': reactor_range must not be empty"
                )
            if not all(isinstance(n, int) for n in cfg.reactor_range):
                raise ValueError(
                    f"Configuration '{cfg_name}': reactor_range must contain integers"
                )
        #elif isinstance(cfg, ClassicClusterConfiguration):

    # Benchmarks
    if not plan.benchmarks.workloads:
        raise ValueError("TestPlan.benchmarks.workloads must not be empty")


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_test_plan(json_file: str) -> PerfTestPlan:
    """Read *json_file* and return a fully populated :class:`PerfTestPlan`.

    Parameters
    ----------
    json_file:
        Path to a test-plan JSON file.

    Raises
    ------
    FileNotFoundError
        When *json_file* does not exist.
    KeyError / ValueError
        When required JSON keys are absent or contain invalid values.
    """
    with open(json_file, "r") as fh:
        data = json.load(fh)

    cluster = _parse_cluster(data["cluster"])
    benchmarks = _parse_benchmarks(data["benchmarks"])
    plan = PerfTestPlan(cluster=cluster, benchmarks=benchmarks)
    validate_plan(plan)
    return plan

