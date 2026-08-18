When you set the `latency_target` option, FIO switches from a fixed-queue depth model to a dynamic, closed-loop feedback loop. Instead of keeping the IO depth pinned to whatever you specified via `iodepth`, FIO treats your `iodepth` setting as the **maximum ceiling** it is allowed to reach while it actively searches for the sweet spot.

Here is the step-by-step breakdown of how FIO manages, ramps up, and scales back the IO depth to hit your latency goal.

---

## 1. The Core Metrics Involved

To make this work, FIO tracks three main variables behind the scenes:

* **`latency_target`**: The maximum duration (e.g., `250ms`) you want an IO operation to take.
* **`latency_window`**: The timeframe (e.g., `5s`) over which FIO averages its tracking data before making a decision.
* **`latency_percentile`**: The percentage of IOs that *must* fall under the target (e.g., `95.0%`). If 95% of requests are faster than 250ms, the test is considered successful.

---

## 2. The Dynamic Adjusting Algorithm

FIO uses an additive-increase/multiplicative-decrease (AIMD) logic—very similar to how TCP congestion control handles network bandwidth.

```
  [ Start at iodepth=1 ]
            │
            ▼
┌──────────────────────────┐
│ Measure Latency Window   │◄──────────────────────────────┐
└───────────┬──────────────┘                               │
            │                                              │
            ▼                                              │
   Did X% of IOs finish                                    │
   under Latency Target?                                   │
      /              \                                     │
    YES               NO                                   │
    /                  \                                   │
   ▼                    ▼                                  │
[ Increase Queue ]   [ Decrease Queue ]                    │
 iodepth = iodepth+1   iodepth = iodepth * stability_factor │
   │                    │                                  │
   └────────────┬───────┘                                  │
                │                                          │
                ▼                                          │
    [ Cap at Max Max_iodepth ] ────────────────────────────┘

```

### Phase A: The Discovery Phase (Ramping Up)

1. FIO always starts the benchmark with an effective IO depth of **1**.
2. It runs the workload for the duration specified by `latency_window`.
3. At the end of the window, FIO checks the latency distribution.
4. If the target is met (i.e., 95% of the requests were faster than 250ms), FIO says, *"The storage engine can handle more."*
5. FIO **increases the queue depth by 1** (`iodepth++`) and starts a new window.

### Phase B: Trimming Back (The Ceiling)

FIO will keep stepping up the queue depth window-by-window until one of two things happens:

* **Scenario 1: You hit the hard limit.** The queue depth reaches the `iodepth` value specified in your command line/job file. FIO will sit comfortably at this ceiling because it hasn't violated the latency target.
* **Scenario 2: The latency budget breaks.** The queue depth becomes too high, the storage system bottlenecks, and the latency spikes. At the end of the window, FIO realizes that fewer than 95% of the IOs made the cut.

When Scenario 2 happens, FIO immediately scales back the queue depth. It multiplies the current IO depth by a hardcoded stabilization factor (usually scaling it down by a percentage) to pull the system out of saturation.

---

## 3. The Continuous Equilibrium

For the remainder of the test, FIO enters an equilibrium cycle. It will constantly dance around the maximum possible IO depth:

* It drops the IO depth down to let the queue clear.
* If latency recovers, it begins creeping the IO depth back up by 1.
* If latency spikes again, it drops it back down.

At the very end of the test execution, FIO's final output report will show you the results of this experiment. It breaks down a histogram of the queue depths it utilized, showing you exactly what percentage of the test duration was spent at `iodepth=1`, `iodepth=4`, `iodepth=12`, etc., giving you a clear picture of the maximum sustainable concurrency your cluster could handle under load.
