mclock in Crimson is the **OSD operation scheduler**: it decides **which queued operation to run next** by applying the dmclock algorithm so different classes of work get controlled **fairness, reservation, and limit behavior** instead of simple FIFO. In Crimson, this is implemented by `crimson::osd::scheduler::mClockScheduler`. It wraps a dmclock pull-priority queue, tracks clients/classes, and uses config-driven weights/limits to choose work. The PR you linked is part of enabling that support in Crimson, and specifically wires common mclock code into the Crimson build.  

```list type="pr" id=4a82b503-5e93-4bed-b4e3-b2482632efc0
```

### What “mclock” means here

At a high level, mclock is a **QoS scheduling algorithm**. Instead of treating all OSD ops equally, it tries to balance service among classes of requests. In Ceph’s code, Crimson’s scheduler is explicitly described as a “Scheduler implementation based on mclock.” The actual scheduling engine comes from the dmclock code under `src/dmclock/src/`, while Crimson’s adapter lives in `src/crimson/osd/scheduler/mclock_scheduler.h`.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.h#L41-L54
namespace crimson::osd::scheduler {

/**
 * Scheduler implementation based on mclock.
 *
 * TODO: explain configs
 */
class mClockScheduler : public Scheduler, md_config_obs_t {
```

### How it is structured in Crimson

Crimson’s `mClockScheduler` has a few key pieces:

- a `ClientRegistry` for looking up scheduling metadata,
- an `MclockConfig` object for config/policy,
- a dmclock `PullPriorityQueue` named `scheduler`,
- and a separate `high_priority` queue for work that bypasses normal mclock ordering.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.h#L48-L68
crimson::common::CephContext *cct;
unsigned cutoff_priority;
PerfCounters *logger;

ClientRegistry client_registry;
MclockConfig mclock_conf;

using mclock_queue_t = crimson::dmclock::PullPriorityQueue<
  scheduler_id_t,
  item_t,
  true,
  true,
  2,
  crimson_mclock_cleaning_job_t>;
mclock_queue_t scheduler;
```

That means Crimson is **not re-implementing dmclock from scratch**. It is **adapting Crimson OSD ops into dmclock queue items** and letting dmclock choose the next eligible item.

### How requests are identified for scheduling

When an item is enqueued, Crimson derives a `scheduler_id_t` from the op’s params. In the code shown, that ID includes the op’s scheduling class (`item.params.klass`) plus a client profile ID. That ID is what dmclock uses as the scheduling identity.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.h#L119-L124
static scheduler_id_t get_scheduler_id(const item_t &item) {
  return scheduler_id_t{
    item.params.klass,
    client_profile_id_t()
  };
}
```

So conceptually:

1. an OSD op arrives,
2. Crimson tags it with scheduling parameters/class,
3. the scheduler turns that into a dmclock scheduling identity,
4. dmclock decides when it should be served relative to other queued work.

### How the dmclock queue is created

The constructor shows the important wiring. `mClockScheduler` builds `MclockConfig`, then constructs the dmclock scheduler with:

- a callback to `ClientRegistry::get_info`,
- timing parameters like idle/erase/check ages,
- `AtLimit::Wait`,
- and an anticipation timeout from config.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.h#L128-L168
mClockScheduler(CephContext *cct, int whoami, uint32_t num_shards,
  int shard_id, bool is_rotational,
  std::chrono::duration<Rep,Per> idle_age,
  std::chrono::duration<Rep,Per> erase_age,
  std::chrono::duration<Rep,Per> check_time,
  bool init_perfcounter=true, MonClient *monc=nullptr)
: cct(cct),
  logger(nullptr),
  mclock_conf(cct, client_registry, num_shards, is_rotational, shard_id, whoami),
  scheduler(
    std::bind(&ClientRegistry::get_info,
              &client_registry,
              _1),
    idle_age, erase_age, check_time,
    dmc::AtLimit::Wait,
    cct->_conf.get_val<double>("osd_mclock_scheduler_anticipation_timeout"))
{
```

This is the core “how it works” path: **configuration + client/class metadata + dmclock queue policy**.

### What the periodic cleaning job does

Crimson’s mclock scheduler also has a lightweight background cleaning job. It periodically runs a body function, waits for a configured period, and can be updated or stopped. This is used to keep scheduler bookkeeping fresh.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.h#L56-L96
struct job_control_t {
  std::chrono::milliseconds period;
  std::function<void()> body;

  bool stopping = false;
  seastar::condition_variable cv;
};

static seastar::future<> run(
  seastar::lw_shared_ptr<job_control_t> control) {
  while (!control->stopping) {
    std::invoke(control->body);
    co_await control->cv.wait(control->period);
  }
}
```

This fits the lower-level dmclock machinery too: the underlying dmclock client/service tracker has cleanup logic to remove stale tracking data over time. The `ServiceTracker` comments explicitly describe periodic cleaning of old server entries.  

```c++ name=src/dmclock/src/dmclock_client.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/dmclock/src/dmclock_client.h#L221-L286
/*
 * This is being called regularly by RunEvery. Every time it's
 * called it notes the time and delta counter (mark point) in a
 * deque. It also looks at the deque to find the most recent
 * mark point that is older than clean_age. It then walks the
 * map and delete all server entries that were last used before
 * that mark point.
 */
void do_clean() {
```

### The special-case high priority queue

Crimson does **not** send every operation through pure dmclock arbitration. It also keeps a `high_priority` queue, documented in-code as being dequeued in strict order **ahead of mClock**. That means some urgent/internal work can bypass ordinary proportional scheduling.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.h#L116-L136
SubQueue high_priority;
priority_t immediate_class_priority = std::numeric_limits<priority_t>::max();

/**
 * high_priority
 *
 * Holds entries to be dequeued in strict order ahead of mClock
 * Invariant: entries are never empty
 */
```

That is an important practical detail: Crimson’s behavior is really **“mclock plus a strict-priority escape hatch”**, not an absolutely pure dmclock-only scheduler.

### What the PR changes are doing

The PR you showed, “crimson: Support mclock for crimson,” includes adding `src/common/mclock_common.cc` into `crimson-common` and adding the dmclock support include path in `src/crimson/CMakeLists.txt`. That suggests the PR is enabling Crimson to reuse Ceph’s shared mclock support/config code rather than keeping the scheduler isolated or incomplete. The shared file `src/common/mclock_common.cc` is already guarded for Crimson-specific perf-counter includes when `WITH_CRIMSON` is enabled.  

``c++ name=src/common/mclock_common.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.cc#L1-L29
#include <memory>
#include <functional>

#include "mclock_common.h"
#include "debug.h"

#ifdef WITH_CRIMSON
#include "crimson/common/perf_counters_collection.h"
#else
#include "perf_counters_collection.h"
#endif
```

So the small diff you pasted is not “the whole mclock implementation”; it is more like **build-system plumbing to make Crimson’s mclock support compile and link with common mclock code**.

### Simple mental model

A useful way to think about it is:

- **dmclock** = the generic fairness/QoS algorithm
- **mClockScheduler in Crimson** = the Crimson adapter around that algorithm
- **MclockConfig / ClientRegistry / mclock_common** = policy/config/supporting metadata
- **high_priority queue** = bypass lane for urgent ops

### Short answer to “how it works”

In operational terms:

1. Crimson receives OSD work and classifies it.
2. The work is converted into a dmclock queue item with a scheduling identity/class.
3. `MclockConfig` and `ClientRegistry` provide the parameters dmclock needs.
4. The dmclock `PullPriorityQueue` decides which normal op to dispatch next based on fairness/QoS rules.
5. Some urgent ops can go through `high_priority` and run before the dmclock-selected work.
6. Background cleanup jobs keep old scheduling/accounting state from accumulating.

If you want, I can next give you either:

1. a **code-path walkthrough** of enqueue/dequeue in Crimson mclock, or  
2. a **conceptual explanation of reservation / weight / limit** in dmclock terms.

Yes — here are both.

## 1) Code-path walkthrough: how enqueue/dequeue works in Crimson mclock

### Big picture flow

In this PR, Crimson’s OSD startup initializes the scheduler only after it knows whether the device is rotational. `OSD::_add_device_class()` sets `is_rotational`, then each shard initializes its local scheduler through `initialize_scheduler(...)`. That scheduler is created by `make_scheduler(...)`, which now constructs `mClockScheduler` when `osd_op_queue` is `mclock_scheduler`. This is the plumbing that actually turns mclock on for Crimson in this PR.  

```c++ name=src/crimson/osd/osd.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/osd.cc#L581-L594
}).then([this] {
  if (is_rotational.has_value()) {
    return shard_services.invoke_on_all([this](auto &local_service) {
      local_service.local_state.initialize_scheduler(local_service.get_cct(), *is_rotational);
    });
  } else {
    throw std::runtime_error("No device class is set");
  }
})
```

```c++ name=src/crimson/osd/shard_services.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/shard_services.h#L215-L220
void initialize_scheduler(CephContext* cct, bool is_rotational) {
  throttler.initialize_scheduler(cct, crimson::common::local_conf(), is_rotational, whoami);
  throttler.start();
}
```

```c++ name=src/crimson/osd/scheduler/scheduler.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/osd/scheduler/scheduler.cc#L144-L152
if (*type == "wpq" ) {
  ...
} else if (*type == "mclock_scheduler") {
  return std::make_unique<mClockScheduler>(cct, whoami, nshards, sid, is_rotational, perf_cnt);
}
```

### Step A: an operation asks for throttle/scheduling

Operations eventually call `OperationThrottler::acquire_throttle(...)`. That creates a scheduler `item_t`, gets a future from its promise, enqueues it into the scheduler, increments `pending`, signals the condition variable, and returns the future to the caller. The operation then waits until the scheduler wakes it.  

```c++ name=src/crimson/osd/osd_operation.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/osd_operation.cc#L232-L240
crimson::osd::scheduler::item_t item{params, seastar::promise<>()};
auto fut = item.wake.get_future();
scheduler->enqueue(std::move(item));
++pending;
wake();
return fut;
```

The scheduling metadata is carried in `params_t`: cost, priority, owner, and class. This PR adds `priority` explicitly and unifies class typing through `SchedulerClass`.  

```c++ name=src/crimson/osd/scheduler/scheduler.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/osd/scheduler/scheduler.h#L24-L37
struct params_t {
  int cost = 1;
  unsigned priority = 0;
  client_t owner;
  SchedulerClass klass;
};

struct item_t {
  params_t params;
  seastar::promise<> wake;
  int get_cost() const { return params.cost; }
  unsigned get_priority() const { return params.priority; }
};
```

### Step B: `mClockScheduler::enqueue()` decides which lane to use

The core entry point is `mClockScheduler::enqueue(item_t&&)`.

It does three-way routing:

1. if class is `immediate`, send to `high_priority`;
2. else if the explicit numeric priority is above the configured cutoff, also send to `high_priority`;
3. otherwise, scale the cost and enqueue into the dmclock scheduler.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L67-L89
void mClockScheduler::enqueue(item_t&& item)
{
  auto id = get_scheduler_id(item);
  unsigned priority = item.get_priority();

  if (SchedulerClass::immediate == item.params.klass) {
    enqueue_high(immediate_class_priority, std::move(item));
  } else if (priority >= cutoff_priority) {
    enqueue_high(priority, std::move(item));
  } else {
    auto cost = calc_scaled_cost(item.get_cost());
    scheduler.add_request(
      std::move(item),
      id,
      cost);
  }
}
```

So Crimson mclock is really **two-tiered**:

- a strict-priority front lane (`high_priority`)
- a dmclock-controlled normal lane (`scheduler`)

### Step C: what `high_priority` means

`high_priority` is a `std::map<priority_t, std::list<item_t>, std::greater<priority_t>>`, so larger priority values come first. The header comment is explicit: these entries are dequeued in strict order ahead of mClock.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.h#L105-L117
using priority_t = unsigned;
using SubQueue = std::map<priority_t,
  std::list<item_t>,
  std::greater<priority_t>>;
mclock_queue_t scheduler;
/**
 * high_priority
 *
 * Holds entries to be dequeued in strict order ahead of mClock
 */
SubQueue high_priority;
```

`enqueue_high()` inserts into the per-priority list. Normal enqueue puts new items at the front; dequeue pops from the back, giving FIFO behavior within a single priority bucket. `enqueue_front()` reverses that behavior to simulate “front of queue.”  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L123-L131
void mClockScheduler::enqueue_high(unsigned priority,
                                   item_t&& item,
				   bool front)
{
  if (front) {
    high_priority[priority].push_back(std::move(item));
  } else {
    high_priority[priority].push_front(std::move(item));
  }
}
```

### Step D: `enqueue_front()` and why it matters

If an item must be requeued “ahead” of others, Crimson cannot push it to the front of the dmclock queue directly, because dmclock does not support a generic “front insert.” So `enqueue_front()` falls back to the high-priority lane. For non-immediate/non-cutoff items, it uses priority `0` in `high_priority` to emulate a front insertion.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L108-L120
void mClockScheduler::enqueue_front(item_t&& item)
{
  unsigned priority = item.get_priority();

  if (SchedulerClass::immediate == item.params.klass) {
    enqueue_high(immediate_class_priority, std::move(item), true);
  } else if (priority >= cutoff_priority) {
    enqueue_high(priority, std::move(item), true);
  } else {
    // mClock does not support enqueue at front, so we use
    // the high queue with priority 0
    enqueue_high(0, std::move(item), true);
  }
}
```

That is an important behavioral nuance: **front-requeue bypasses pure dmclock ordering**.

### Step E: `dequeue()` prefers strict priority, then dmclock

When Crimson asks for the next item, `dequeue()` checks `high_priority` first. If anything is there, the highest numeric priority bucket wins and one item is returned from that bucket. Only when `high_priority` is empty does it ask the dmclock queue for the next request.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L134-L160
WorkItem mClockScheduler::dequeue()
{
  if (!high_priority.empty()) {
    auto iter = high_priority.begin();
    WorkItem ret{std::move(iter->second.back())};
    iter->second.pop_back();
    if (iter->second.empty()) {
      high_priority.erase(iter);
    }
    return ret;
  } else {
    mclock_queue_t::PullReq result = scheduler.pull_request();
    if (result.is_future()) {
      return result.getTime();
    } else if (result.is_none()) {
      ceph_abort_msg("Impossible, must have checked empty() first");
    } else {
      auto &retn = result.get_retn();
      return std::move(*retn.request);
    }
  }
}
```

This return type is now `WorkItem = std::variant<std::monostate, item_t, double>`, where `double` means “nothing is ready yet; come back at this time.”  

```c++ name=src/crimson/osd/scheduler/scheduler.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/osd/scheduler/scheduler.h#L34-L37
using WorkItem = std::variant<std::monostate, item_t, double>;
```

### Step F: the throttler background task handles “future” wakeups

This PR adds a background task in `OperationThrottler` specifically because mclock can say “the next eligible request is in the future.” The task waits until capacity is available and the scheduler is non-empty, calls `dequeue()`, and:

- if it gets a `double`, converts it to a time point and sleeps until then;
- if it gets an `item_t`, fulfills the promise and lets that operation run.  

```c++ name=src/crimson/osd/osd_operation.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/osd_operation.cc#L174-L214
while (available() && !scheduler->empty() && !stopped) {
  WorkItem work_item = scheduler->dequeue();
  if (auto when_ready = std::get_if<double>(&work_item)) {
    ceph::real_clock::time_point future_time = ceph::real_clock::from_double(*when_ready);
    auto now = ceph::real_clock::now();
    auto wait_duration =
      std::chrono::duration_cast<std::chrono::milliseconds>(future_time - now);
    co_await seastar::sleep(wait_duration);
    continue;
  }
  if (auto *item = std::get_if<crimson::osd::scheduler::item_t>(&work_item)) {
    item->wake.set_value();
    ++in_progress;
    --pending;
  }
}
```

So end-to-end, the actual runtime path is:

1. op requests throttle
2. scheduler item created
3. `enqueue()` routes it
4. background task calls `dequeue()`
5. either sleep-until-ready or wake promise
6. op starts running

---

## 2) Conceptual explanation: reservation / weight / limit in dmclock terms

The core dmclock `ClientInfo` has exactly three policy numbers:

- `reservation` = minimum share
- `weight` = proportional share when competing
- `limit` = maximum share cap  

That is explicit in the dmclock code.  

```c++ name=src/dmclock/src/dmclock_server.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/dmclock/src/dmclock_server.h#L89-L111
struct ClientInfo {
  double reservation;  // minimum
  double weight;       // proportional
  double limit;        // maximum
```

### Reservation: “guaranteed minimum”

Reservation says: if a class has demand, it should get at least this much service over time.

Example mental model:

- client ops reservation = 0.6
- recovery ops reservation = 0.4
- best-effort = 0.0

Then, under sustained load, client ops should get a minimum 60% share and recovery 40%, while best-effort has no guaranteed floor.

In the shipped profiles, that is exactly how the presets are described. For example `HIGH_CLIENT_OPS` gives client 60%, recovery 40%, best-effort 0 reservation.  

```c++ name=src/common/mclock_common.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.h#L94-L108
/**
 * high_client_ops
 *
 * Client Allocation:
 *   reservation: 60% | weight: 2 | limit: 0 (max) |
 * Background Recovery Allocation:
 *   reservation: 40% | weight: 1 | limit: 0 (max) |
 * Background Best Effort Allocation:
 *   reservation: 0 (min) | weight: 1 | limit: 70% |
 */
constexpr profile_t HIGH_CLIENT_OPS{
  { .6, 2,  0 },
  { .4, 1,  0 },
  {  0, 1, .7 }
};
```

### Weight: “who gets the extra capacity”

Once all active reservations are satisfied, any remaining capacity is distributed proportionally by weight.

If two classes are both eligible and have weights 2 and 1, the first should get about twice as much of the leftover capacity as the second.

That is why a profile can say, for example:

- client reservation 50%, weight 1
- recovery reservation 50%, weight 1

meaning they split both guaranteed and extra service evenly; while another profile might bias extra service toward client or recovery.

The `BALANCED` profile shows a symmetric setup.  

```c++ name=src/common/mclock_common.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.h#L126-L140
constexpr profile_t BALANCED{
  { .5, 1, 0 },
  { .5, 1, 0 },
  {  0, 1, .9 }
};
```

### Limit: “don’t let this class exceed X”

Limit caps how much service a class can consume even if the system is idle or other classes are not fully using their reservations.

A limit of `0` here means effectively “no cap / max” in Ceph’s profile comments. Best-effort often gets a nonzero limit so it cannot run away and starve more important work.  

```c++ name=src/common/mclock_common.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.h#L61-L64
constexpr double default_max = std::numeric_limits<double>::is_iec559 ?
  std::numeric_limits<double>::infinity() :
  std::numeric_limits<double>::max();
```

### How dmclock turns those into scheduling decisions

dmclock computes three tags per request:

- reservation tag
- proportion tag
- limit tag

These are stored in `RequestTag`.  

```c++ name=src/dmclock/src/dmclock_server.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/dmclock/src/dmclock_server.h#L125-L134
struct RequestTag {
  double   reservation;
  double   proportion;
  double   limit;
  uint32_t delta;
  uint32_t rho;
  Cost     cost;
  bool     ready;
  Time     arrival;
```

Very roughly:

- **reservation tag** answers: “is this request due to satisfy the minimum guarantee?”
- **proportion tag** answers: “among eligible requests, whose weighted fair turn is it?”
- **limit tag** answers: “is this request allowed to run yet, or is the client over its cap?”

### The actual selection order in dmclock

The scheduler logic in `do_next_request()` is:

1. if any request is due by reservation, return that first;
2. otherwise, mark requests whose limit has matured as ready;
3. among ready requests, choose by proportional fairness;
4. if nothing is ready, return a future timestamp for when something becomes ready.  

```c++ name=src/dmclock/src/dmclock_server.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/dmclock/src/dmclock_server.h#L1547-L1606
NextReq do_next_request(Time now) {
  ...
  auto& reserv = resv_heap.top();
  if (reserv.has_request() &&
      reserv.next_request().tag.reservation <= now) {
    return NextReq(HeapId::reservation);
  }

  auto limits = &limit_heap.top();
  while (limits->has_request() &&
         !limits->next_request().tag.ready &&
         limits->next_request().tag.limit <= now) {
    limits->next_request().tag.ready = true;
    ready_heap.promote(*limits);
    limit_heap.demote(*limits);
    limits = &limit_heap.top();
  }

  auto& readys = ready_heap.top();
  if (readys.has_request() &&
      readys.next_request().tag.ready &&
      readys.next_request().tag.proportion < max_tag) {
    return NextReq(HeapId::ready);
  }
```

So the intuitive rule is:

- **first honor guarantees**
- **then distribute spare capacity fairly**
- **but do not exceed configured caps**
- **if no request is eligible now, wait**

### Where “cost” fits in

Requests are not all treated as equal-sized. dmclock factors in `cost`, and Crimson scales the operation cost through `MclockConfig::calc_scaled_cost()`. In the scheduler code, that happens right before the item is added to dmclock.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L77-L88
auto cost = calc_scaled_cost(item.get_cost());
scheduler.add_request(
  std::move(item),
  id,
  cost);
```

And `MclockConfig` exposes both `get_cost_per_io()` and `calc_scaled_cost(int item_cost)`, so the policy is not just “one op equals one op”; it is trying to map op size/work into a normalized cost budget.  

```c++ name=src/common/mclock_common.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.h#L227-L250
double get_cost_per_io() const;
double get_capacity_per_shard() const;
...
uint32_t calc_scaled_cost(int item_cost);
```

### How classes map in Crimson

Crimson uses these scheduler classes:

- `background_recovery`
- `background_best_effort`
- `client`
- `repop`
- `immediate`  

```c++ name=src/common/mclock_common.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.h#L35-L49
enum class scheduler_class_t : uint8_t {
  background_recovery = 0,
  background_best_effort,
  client,
  repop,
  immediate,
};

#ifdef WITH_CRIMSON
using SchedulerClass = scheduler_class_t;
```

In practice:

- `immediate` bypasses dmclock
- high numeric priorities above cutoff also bypass dmclock
- the rest go through dmclock with their class-specific reservation/weight/limit policy

### One subtle but important caveat

In this Crimson implementation, `get_scheduler_id()` currently uses the class but constructs a default `client_profile_id_t()`, not a real per-owner profile. So the primary distinction appears to be **class-based**, not rich per-client differentiation, at least in the code path shown here.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.h#L119-L124
static scheduler_id_t get_scheduler_id(const item_t &item) {
  return scheduler_id_t{
    item.params.klass,
    client_profile_id_t()
  };
}
```

So today, the strongest reading is: Crimson mclock here is mainly **class-of-service scheduling** with dmclock semantics, plus cost scaling, plus a strict-priority bypass path.

## Short summary

If I compress all of this:

- **Code path:** op -> `acquire_throttle()` -> `scheduler.enqueue()` -> either `high_priority` or dmclock queue -> throttler background task calls `dequeue()` -> wake item now or sleep until dmclock says it is eligible.
- **Conceptually:** mclock gives each class a **minimum share** (`reservation`), a **fair-share weight** (`weight`), and a **maximum cap** (`limit`), then schedules by reservation first, proportional fairness second, and time-delays requests that are not yet eligible.

If you want, I can do one more pass and turn this into a **worked example** with three classes — e.g. “10 client ops, 10 recovery ops, 10 best-effort ops” — showing who runs first and why.

Sure — here’s a concrete worked example.

## Worked example: how Crimson mclock would choose ops

I’ll use the `BALANCED` profile because it’s the easiest to reason about:

- **client**: reservation 50%, weight 1, limit max
- **background_recovery**: reservation 50%, weight 1, limit max
- **background_best_effort**: reservation 0%, weight 1, limit 90%  

Those profile values are defined in Ceph’s shared mclock config code.  

```c++ name=src/common/mclock_common.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.h#L126-L140
constexpr profile_t BALANCED{
  { .5, 1, 0 },
  { .5, 1, 0 },
  {  0, 1, .9 }
};
```

And the underlying dmclock `ClientInfo` fields are exactly `reservation`, `weight`, and `limit`.  

```c++ name=src/dmclock/src/dmclock_server.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/dmclock/src/dmclock_server.h#L89-L111
struct ClientInfo {
  double reservation;  // minimum
  double weight;       // proportional
  double limit;        // maximum
```

---

## Scenario setup

Assume one Crimson shard has these queued items:

- 10 **client** ops
- 10 **background_recovery** ops
- 10 **background_best_effort** ops

Assume for simplicity:

- none are `immediate`
- none have numeric priority above the cutoff
- all go into the dmclock queue, not the strict `high_priority` queue
- all have equal scaled cost = 1

That matches the `enqueue()` branch where normal work gets passed to `scheduler.add_request(...)`.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L72-L89
if (SchedulerClass::immediate == item.params.klass) {
  enqueue_high(immediate_class_priority, std::move(item));
} else if (priority >= cutoff_priority) {
  enqueue_high(priority, std::move(item));
} else {
  auto cost = calc_scaled_cost(item.get_cost());
  scheduler.add_request(
    std::move(item),
    id,
    cost);
}
```

---

## Intuition first: what should happen?

With `BALANCED`, you should expect:

- client and recovery each get their guaranteed minimum share
- best-effort gets no guarantee
- if there is spare capacity beyond the guarantees, best-effort can get some too
- but client and recovery should not be starved by best-effort

So if the system is continuously busy with all three classes active, the rough long-run behavior is:

- about half the service goes to **client**
- about half goes to **background_recovery**
- **best_effort** runs only when there is room under the dmclock rules

That follows from the profile: client 0.5 reservation, recovery 0.5 reservation, best_effort 0.0 reservation.  

---

## Phase 1: guaranteeing the reserved shares

dmclock first checks the **reservation queue**. If a request’s reservation tag says it is due now, that request is chosen first. This is the first branch in `do_next_request()`.  

```c++ name=src/dmclock/src/dmclock_server.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/dmclock/src/dmclock_server.h#L1551-L1558
auto& reserv = resv_heap.top();
if (reserv.has_request() &&
    reserv.next_request().tag.reservation <= now) {
  return NextReq(HeapId::reservation);
}
```

### What that means in plain English

At the beginning, client and recovery both have unmet reservation entitlement. Best-effort has none.

So the scheduler will tend to alternate between:

- a client op
- a recovery op
- a client op
- a recovery op

Not necessarily strictly alternating one-by-one, but close to that over time.

### Simplified first 10 dispatches

A plausible simplified sequence is:

1. client
2. recovery
3. client
4. recovery
5. client
6. recovery
7. client
8. recovery
9. client
10. recovery

Why no best-effort yet?

Because best-effort has **reservation = 0**, so while client and recovery still have guaranteed work due, dmclock keeps honoring those guarantees first.

---

## Phase 2: weighted sharing after reservations are satisfied

Once no request is currently due on reservation, dmclock starts looking at the **ready/proportional** path. It marks requests whose limit allows them to run, then chooses among ready requests by proportional tag.  

```c++ name=src/dmclock/src/dmclock_server.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/dmclock/src/dmclock_server.h#L1563-L1576
auto limits = &limit_heap.top();
while (limits->has_request() &&
       !limits->next_request().tag.ready &&
       limits->next_request().tag.limit <= now) {
  limits->next_request().tag.ready = true;
  ready_heap.promote(*limits);
  limit_heap.demote(*limits);
  limits = &limit_heap.top();
}

auto& readys = ready_heap.top();
if (readys.has_request() &&
    readys.next_request().tag.ready &&
    readys.next_request().tag.proportion < max_tag) {
  return NextReq(HeapId::ready);
}
```

With `BALANCED`, client, recovery, and best-effort all have weight 1. So if all three are competing in the proportional stage, they share extra bandwidth evenly.

### What does “extra” mean?

Suppose client and recovery are only using their guaranteed minimums part of the time, or their request timing leaves slack. Then best-effort can take some of that slack.

So a later sequence might look like:

11. best_effort
12. client
13. recovery
14. best_effort
15. client
16. recovery

Again, not exact literal order, but this is the right intuition.

---

## Best-effort under load vs under slack

### Case A: heavy sustained load from client + recovery

If client and recovery are both always backlogged, and their combined reservations already consume the shard’s effective service budget, then best-effort may get very little or nothing.

Because:

- client reservation = 50%
- recovery reservation = 50%
- total reserved = 100%

So there may be no “spare” service for best-effort in the long run.

### Case B: client traffic drops

Now say client traffic slows down:

- only 2 client ops remain
- 10 recovery ops remain
- 10 best_effort ops remain

What happens?

- client no longer needs its full 50% reserved share
- recovery still has demand
- best_effort can use some leftover capacity too, according to weight/limit rules

So dispatches might look more like:

1. recovery
2. best_effort
3. recovery
4. best_effort
5. recovery
6. recovery
7. best_effort

The exact interleaving depends on tags and timing, but the key idea is: **unused reservation from one class doesn’t disappear; other eligible classes can use the capacity**.

---

## Now add a limit example

Let’s switch to `HIGH_CLIENT_OPS`:

- client: reservation 60%, weight 2, limit max
- recovery: reservation 40%, weight 1, limit max
- best_effort: reservation 0%, weight 1, limit 70%  

```c++ name=src/common/mclock_common.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.h#L94-L108
constexpr profile_t HIGH_CLIENT_OPS{
  { .6, 2,  0 },
  { .4, 1,  0 },
  {  0, 1, .7 }
};
```

### Suppose only best-effort is backlogged

Imagine:

- client queue empty
- recovery queue empty
- 100 best-effort ops waiting

Since best-effort has no reservation, it is not guaranteed service. But if it’s the only active class, it can still run — up to its **limit**.

That means it can consume service, but dmclock can delay it if running more would exceed the configured cap behavior for that class.

In code, the queue can respond “nothing eligible right now; come back later” by returning a future time. Crimson’s scheduler exposes that as a `double` time value, and the throttler sleeps until that time.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L148-L150
if (result.is_future()) {
  return result.getTime();
}
```

```c++ name=src/crimson/osd/osd_operation.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/osd_operation.cc#L186-L196
if (auto when_ready = std::get_if<double>(&work_item)) {
  ceph::real_clock::time_point future_time = ceph::real_clock::from_double(*when_ready);
  auto now = ceph::real_clock::now();
  auto wait_duration = std::chrono::duration_cast<std::chrono::milliseconds>(future_time - now);
  co_await seastar::sleep(wait_duration);
  continue;
}
```

So in practice, “limit” can look like this:

- best-effort runs a while
- then it becomes temporarily not eligible
- scheduler returns a future timestamp
- Crimson sleeps
- best-effort becomes eligible again later

That is how rate limiting shows up operationally.

---

## Example with priorities in Crimson

Everything above assumes the item goes through dmclock.

But Crimson also has a **strict-priority bypass path**. If an item is:

- class `immediate`, or
- has numeric priority `>= cutoff_priority`

then it goes into `high_priority`, not dmclock.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L72-L76
if (SchedulerClass::immediate == item.params.klass) {
  enqueue_high(immediate_class_priority, std::move(item));
} else if (priority >= cutoff_priority) {
  enqueue_high(priority, std::move(item));
}
```

And `dequeue()` always serves `high_priority` before dmclock items.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L134-L147
if (!high_priority.empty()) {
  auto iter = high_priority.begin();
  WorkItem ret{std::move(iter->second.back())};
  iter->second.pop_back();
  if (iter->second.empty()) {
    high_priority.erase(iter);
  }
  return ret;
}
```

### So if one urgent item arrives...

Suppose your current dmclock queues are:

- 10 client
- 10 recovery
- 10 best_effort

Then an `immediate` op arrives.

What happens?

- that op is put into `high_priority`
- next `dequeue()` returns the immediate op first
- only after `high_priority` empties does dmclock resume normal reservation/weight/limit selection

So the actual observed sequence might be:

1. client
2. recovery
3. immediate
4. client
5. recovery
6. best_effort

That’s a Crimson-specific nuance layered on top of dmclock.

---

## Example with different costs

This PR also makes cost matter more explicitly. Items carry `params.cost`, and Crimson scales it before submitting to dmclock.  

```c++ name=src/crimson/osd/scheduler/scheduler.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/osd/scheduler/scheduler.h#L24-L33
struct params_t {
  int cost = 1;
  unsigned priority = 0;
  client_t owner;
  SchedulerClass klass;
};
```

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L78-L88
auto cost = calc_scaled_cost(item.get_cost());
scheduler.add_request(
  std::move(item),
  id,
  cost);
```

So if:

- one recovery op has cost 8
- one client op has cost 1

the recovery op “consumes” more of its class’s service budget when it runs. Over time, that affects how soon more recovery ops become eligible compared to cheap client ops.

A useful intuition is:

- **costlier ops advance that class’s virtual service clock more**
- so many heavy recovery ops may space themselves out more than many cheap client ops, even under the same class policy

---

## Final intuition cheat sheet

If you want the shortest possible mental model:

- **reservation** = “this class gets at least this much”
- **weight** = “if there’s extra room, divide it proportionally like this”
- **limit** = “this class can’t exceed this rate”
- **cost** = “big ops count more than small ops”
- **high_priority in Crimson** = “urgent ops can bypass dmclock entirely”

## One-line example summary

With `BALANCED` and equal-cost ops:

- if client and recovery are both busy, they dominate service roughly 50/50;
- best-effort gets little or no service unless there is slack;
- urgent/high-priority ops jump ahead of all of them.

If you want, I can next give you a **timeline table** with, say, 20 dispatch decisions step-by-step, or a **mapping from specific Ceph operation types** to likely scheduler classes in Crimson.


Yes — there **is intended support** for mclock-specific performance counters in Crimson, but in the PR/code path you pointed at, they appear to be **only partially wired up**.

## What counters exist for mclock in Crimson?

The shared mclock code defines a per-shard logger named like:

- `mclock-shard-queue-<shard_id>`

and creates these counters:

- `mclock_immediate_queue_len`
- `mclock_client_queue_len`
- `mclock_recovery_queue_len`
- `mclock_best_effort_queue_len`
- `mclock_all_type_queue_len`  

These are all queue-length counters, i.e. counts of ops currently in the mclock queues by class and in total.  

```c++ name=src/common/mclock_common.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.cc#L240-L264
void MclockConfig::init_logger()
{
  PerfCountersBuilder m(cct, "mclock-shard-queue-" + std::to_string(shard_id),
                        l_mclock_first, l_mclock_last);

  m.add_u64_counter(l_mclock_immediate_queue_len, "mclock_immediate_queue_len",
                    "high_priority op count in mclock queue");
  m.add_u64_counter(l_mclock_client_queue_len, "mclock_client_queue_len",
                    "client type op count in mclock queue");
  m.add_u64_counter(l_mclock_recovery_queue_len, "mclock_recovery_queue_len",
                    "background_recovery type op count in mclock queue");
  m.add_u64_counter(l_mclock_best_effort_queue_len, "mclock_best_effort_queue_len",
                    "background_best_effort type op count in mclock queue");
  m.add_u64_counter(l_mclock_all_type_queue_len, "mclock_all_type_queue_len",
                    "all type op count in mclock queue");
```

The corresponding enum IDs are also declared in the shared header.  

```c++ name=src/common/mclock_common.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.h#L51-L59
enum {
  l_mclock_first = 15000,
  l_mclock_immediate_queue_len,
  l_mclock_client_queue_len,
  l_mclock_recovery_queue_len,
  l_mclock_best_effort_queue_len,
  l_mclock_all_type_queue_len,
  l_mclock_last,
};
```

## How are they updated?

The same shared code defines two helper methods:

- `get_mclock_counter(id)` → increments counters when an op enters the mclock queue
- `put_mclock_counter(id)` → decrements counters when an op leaves the mclock queue  

```c++ name=src/common/mclock_common.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.cc#L266-L323
void MclockConfig::get_mclock_counter(scheduler_id_t id)
{
  if (!logger) {
    return;
  }

  logger->inc(l_mclock_all_type_queue_len);

  switch (id.class_id) {
  case SchedulerClass::immediate:
    logger->inc(l_mclock_immediate_queue_len);
    break;
  case SchedulerClass::client:
    logger->inc(l_mclock_client_queue_len);
    break;
  case SchedulerClass::background_recovery:
    logger->inc(l_mclock_recovery_queue_len);
    break;
  case SchedulerClass::background_best_effort:
    logger->inc(l_mclock_best_effort_queue_len);
    break;
```

So the design intent is clear: these counters should reflect the current queued depth by mclock class.

## But are they actually active in Crimson in this PR?

Here’s the important part: in the Crimson `mClockScheduler` code from this PR path, `MclockConfig` is constructed, but I do **not** see `init_logger()` being called, and I also do **not** see `get_mclock_counter()` / `put_mclock_counter()` being called from the Crimson scheduler implementation.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.h#L133-L145
mClockScheduler(CephContext *cct, int whoami, uint32_t num_shards,
  int shard_id, bool is_rotational,
  ...
  bool init_perfcounter=true, MonClient *monc=nullptr)
: cct(cct),
  logger(nullptr),
  mclock_conf(cct, client_registry, num_shards, is_rotational, shard_id, whoami),
  scheduler(
```

And the Crimson implementation of `enqueue()` / `dequeue()` in the file you pointed at does queueing logic, but no perf-counter updates.  

```c++ name=src/crimson/osd/scheduler/mclock_scheduler.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/osd/scheduler/mclock_scheduler.cc#L67-L89
void mClockScheduler::enqueue(item_t&& item)
{
  auto id = get_scheduler_id(item);
  unsigned priority = item.get_priority();

  if (SchedulerClass::immediate == item.params.klass) {
    enqueue_high(immediate_class_priority, std::move(item));
  } else if (priority >= cutoff_priority) {
    enqueue_high(priority, std::move(item));
  } else {
    auto cost = calc_scaled_cost(item.get_cost());
    scheduler.add_request(
      std::move(item),
      id,
      cost);
  }
}
```

By contrast, the classic OSD mclock scheduler **does** call `mclock_conf.get_mclock_counter(id)` on enqueue. That suggests the shared counter machinery is real, but the Crimson path may not yet fully hook it up in this commit range.  

```c++ name=src/osd/scheduler/mClockScheduler.cc url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/osd/scheduler/mClockScheduler.cc#L67-L80
scheduler.add_request(
  std::move(item),
  id,
  cost);
mclock_conf.get_mclock_counter(id);
```

So the best reading is:

- **Defined**: yes
- **Intended for Crimson**: yes, because `mclock_common.cc` was added to `crimson-common`
- **Clearly wired and updated in this PR’s Crimson scheduler path**: not fully, based on the code shown

## How do these relate to other Crimson perf counters?

They are **not special or isolated**. They use the same Crimson perf-counter collection infrastructure as other Crimson components. `mclock_common.cc` includes Crimson’s perf-counter collection when built with `WITH_CRIMSON`, and adds/removes the logger from the `CephContext` perf collection.  

```c++ name=src/common/mclock_common.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.cc#L22-L26
#ifdef WITH_CRIMSON
#include "crimson/common/perf_counters_collection.h"
#else
#include "perf_counters_collection.h"
#endif
```

```c++ name=src/common/mclock_common.cc url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/common/mclock_common.cc#L256-L258
logger = m.create_perf_counters();
cct->get_perfcounters_collection()->add(logger);
```

And Crimson’s perf-collection object is the common sharded collection used by other Crimson subsystems too.  

```c++ name=src/crimson/common/perf_counters_collection.h url=https://github.com/ceph/ceph/blob/d7f55f50c3a8e6c10e341559c38b83c3ffba624d/src/crimson/common/perf_counters_collection.h#L12-L29
class PerfCountersCollection: public seastar::sharded<PerfCountersCollection>
{
  ...
  void dump_formatted(ceph::Formatter *f, bool schema,
                      select_labeled_t dump_labeled,
                      const std::string &logger = "",
                      const std::string &counter = "");
};
```

So relationship-wise:

- mclock counters are **peer metrics** in the same perf-counter registry
- they are **not derived from** cache counters
- they are **not automatically correlated with** cache counters
- correlation would be operational/analytical, not hard-coded

## What about cache counters specifically?

From the Crimson code I checked, caches like OSD map caches and extent-cache LRUs are implemented as normal data structures in Crimson, but I do not see them being intrinsically tied to mclock metrics. For example, `OSDSingletonState` has `SharedLRU` / `SimpleLRU` caches for OSD maps, and `PerShardState` has an `ECExtentCache::LRU`, but these are separate concerns from the scheduler.  

```c++ name=src/crimson/osd/shard_services.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/osd/shard_services.h#L203-L211
// This is an extent cache for the erasure coding. Specifically, this acts as
// a least-recently-used cache invalidator, allowing for cache shards to last
// longer than the most recent IO in each object.
ECExtentCache::LRU ec_extent_cache_lru;
```

```c++ name=src/crimson/osd/shard_services.h url=https://github.com/ceph/ceph/blob/fd4c2993649ed3b174586a6c79cc7e6f861869c6/src/crimson/osd/shard_services.h#L248-L256
SharedLRU<epoch_t, OSDMap> osdmaps;
SimpleLRU<epoch_t, bufferlist, false> map_bl_cache;
SimpleLRU<epoch_t, bufferlist, false> inc_map_bl_cache;
```

That means if you’re asking “are mclock queue counters mathematically combined with cache hit/miss counters?”, the answer is **no, not in the code shown**.

## Practical interpretation

If these counters are fully wired in your build/version, they tell you:

- how many ops are queued in the mclock scheduler,
- broken down by class,
- per shard.

They would be useful alongside cache counters, throttler counters, and general OSD perf stats to answer questions like:

- Are client ops piling up while recovery is active?
- Is the high-priority path being used heavily?
- Is queue buildup happening even when caches look healthy?

But that relationship is **diagnostic correlation**, not a built-in coupling in the implementation.

## One more caveat

The code-search results I used are limited and may be incomplete. To inspect more references in GitHub’s UI, use the repository code search for the relevant symbols, for example the PR changes view you already opened, or GitHub code search for `l_mclock_`, `get_mclock_counter`, and `PerfCountersBuilder` in `ceph/ceph`.

## Bottom line

**Answer:** Crimson mclock has shared perf-counter definitions for per-shard queue depth:

- `mclock_immediate_queue_len`
- `mclock_client_queue_len`
- `mclock_recovery_queue_len`
- `mclock_best_effort_queue_len`
- `mclock_all_type_queue_len`

They live in the same Crimson perf-counter collection as other subsystem counters, but they are **scheduler-local queue metrics**, not directly tied to cache metrics. Also, in the specific Crimson PR/code path you linked, the perf-counter support looks **present in shared code but not obviously fully hooked up in the Crimson scheduler implementation itself**.

If you want, I can next trace **exactly where these mclock counters would appear in admin-socket / perf dump output**, or I can compare them against the **classic OSD mclock implementation** line by line.

