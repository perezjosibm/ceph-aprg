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

    osd_type: str  # classic or crimson
    osd_backend: str  # default bluestore for classic, bluestore or seastore for crimson
    osd_range: List[int]
    store_devs: List[str]
    vstart_cpu_set: List[str]
    pool_name: str  # rados, or rdb
    pool_size: int
    rbd_num_images: int
    rbd_image_size: str


@dataclass
class CrimsonClusterConfiguration(BaseClusterConfiguration):
    """Crimson OSD -specific configuration."""

    reactor_range: List[int] = field(default_factory=list)
    balance_strategy: Optional[str] = "default"


@dataclass
class ClassicClusterConfiguration(BaseClusterConfiguration):
    """Classic (BlueStore) OSD configuration."""

    # classic_cpu_set: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Benchmark types
# ---------------------------------------------------------------------------


@dataclass
class FioWorkload:
    """
    A single FIO workload definition.
    Attributes are parameters that will be passed to FIO when running the
    workload. The fio_name attribute is optional and can be used to specify a
    custom name for the .fio workload when running FIO. The fio_catalog is a
    string for arguments to the run_fio.py modules, which executes the four
    typical workloads: randread4k, randwrite4k, seqwrite64k and seqread64k. At
    least one must be provided.
    """

    rw: str
    bs: str
    runtime: int
    iodepth: List[int]
    numjobs: List[int]
    rwmixread: Optional[int] = None
    fio_name: Optional[str] = None
    fio_catalog: Optional[List[str]] = None


@dataclass
class FioEngine:
    """
    Librbdfio|RADOS benchmark engine parameters.
    We might extend further for FIO engines, like AIO, etc.
    """

    cmd_path: str
    fio_cpu_set: List[str]
    workloads: Dict[str, FioWorkload]


@dataclass
class Benchmarks:
    """All benchmark specifications for a test plan."""

    benchmarks: Dict[str, FioEngine]


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
        "crimson": CrimsonClusterConfiguration,
        "classic": ClassicClusterConfiguration,
    }
    if osd_type not in mapping:
        raise ValueError(
            f"Unknown osd_type '{osd_type}'. Expected one of: {sorted(mapping)}"
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
    TODO: refactor to select the pool details (eg. rbd, rados) and pass to the
    configuration, instead of hardcoding rbd details in the configuration. This
    will allow us to support more pool types and decouple the pool details from
    the cluster configuration.
    """
    osd_type = raw.get("osd_type", "")
    cls = factory(osd_type)

    base_kwargs = dict(
        osd_type=osd_type,
        osd_range=raw["osd_range"],
        store_devs=raw["store_devs"],
        vstart_cpu_set=raw["vstart_cpu_set"],
        pool_name=raw["pool_name"],  # rbd or rados
        pool_size=raw["pool_size"],
        # We need a subclass tha tuses RBD to support RBD-specific parameters
        # like num_images and image_size, but for now we can just include them
        # in the base class and ignore them for non-RBD configurations.
        rbd_num_images=raw.get("rbd_num_images", 0),
        rbd_image_size=raw.get("rbd_image_size", "0G"),
    )

    if cls is CrimsonClusterConfiguration:
        return CrimsonClusterConfiguration(
            **base_kwargs,
            reactor_range=raw.get("reactor_range", []),
            osd_backend=raw.get("osd_backend", "seastore"),
            balance_strategy=raw.get("balance_strategy", "default"),
        )
    # ClassicClusterConfiguration
    return ClassicClusterConfiguration(
        **base_kwargs,
        osd_backend=raw.get("osd_backend", "bluestore"),
        # classic_cpu_set=raw.get("classic_cpu_set", []),
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


def _parse_fio_engines(raw: dict) -> Dict[str, FioEngine]:
    """Parse the FIO engine definitions from the benchmarks section."""
    engines: Dict[str, FioEngine] = {}
    for engine_name, engine_data in raw.items():
        workloads = {
            wl_name: FioWorkload(
                rw=wl_data["rw"],
                bs=wl_data["bs"],
                runtime=wl_data["runtime"],
                iodepth=wl_data["iodepth"],
                numjobs=wl_data["numjobs"],
                rwmixread=wl_data.get("rwmixread"),
                fio_name=wl_data.get("fio_name"),
                fio_catalog=wl_data.get("fio_catalog"),
            )
            for wl_name, wl_data in engine_data.get("workloads", {}).items()
        }
        engines[engine_name] = FioEngine(
            cmd_path=engine_data["cmd_path"],
            fio_cpu_set=engine_data["fio_cpu_set"],
            workloads=workloads,
        )
    return engines


def _parse_benchmarks(raw: dict) -> Benchmarks:
    """Parse the ``benchmarks`` section of the JSON."""
    return Benchmarks(benchmarks=_parse_fio_engines(raw))


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
            raise ValueError(f"Configuration '{cfg_name}': osd_range must not be empty")
        if not cfg.vstart_cpu_set:
            raise ValueError(
                f"Configuration '{cfg_name}': vstart_cpu_set must not be empty"
            )
        if not all(isinstance(n, int) for n in cfg.osd_range):
            raise ValueError(
                f"Configuration '{cfg_name}': osd_range must contain integers"
            )
        if isinstance(cfg, CrimsonClusterConfiguration):
            if not cfg.reactor_range:
                raise ValueError(
                    f"Configuration '{cfg_name}': reactor_range must not be empty"
                )
            if not all(isinstance(n, int) for n in cfg.reactor_range):
                raise ValueError(
                    f"Configuration '{cfg_name}': reactor_range must contain integers"
                )
        # elif isinstance(cfg, ClassicClusterConfiguration):

    # Benchmarks
    if not plan.benchmarks:
        raise ValueError("TestPlan.benchmarks must not be empty")


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
