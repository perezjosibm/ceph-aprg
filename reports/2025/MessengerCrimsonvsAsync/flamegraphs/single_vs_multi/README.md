The following are the snippts for the post-processing of the results of the tests done.

- To start the tests:
```bash
Thu Apr 17 15:21:09 UTC 2025 -- #/root/bin/run_mesgr.sh -d /tmp/_b1e4a2b/msgr_crimson_11 -t all -b all -x # 2x14 crimson + async
```

- To get the summary of the clients IOPs, latency, etc:

```bash
# for x in msgr_async*zip; do echo "== $x =="; unzip -c $x "*_client.out" | tail -8; done > msgr_28cpu_async_bal_vs_sep_client.out
# for x in msgr_crimson*zip; do echo "== $x =="; unzip -c $x "*_client.out" | tail -8; done > msgr_28cpu_crimson_bal_vs_sep_client.out
```

- To merge the multiple reactors/workers from crimson and Async:

```bash
for x in *perf.out_fold.gz; do y=${x/_fold.gz/_merged}; echo "== $y =="; gzip -d -c $x |  sed -e 's/perf-crimson-ms/reactor/g' -e 's/reactor-[0-9]\+/reactor/g' > $y; flamegraph.pl ${y} > ${y}.svg; done
gzip -d -c left_server_msgr_async_14smp_clients_separated.perf.out_fold.gz |  sed -e 's/msgr-worker-[0-9]\+/msgr-worker/g' > left_server_msgr_async_14smp_clients_separated.perf.out_merged
```

- To produce the differential flamegraph:

```bash
# difffolded.pl  _server_msgr_async_28smp_clients_separated.perf.out_merged _03_async_crimson_lr_fg/left_server_msgr_async_14smp_clients_balanced.perf.out_merged | flamegraph.pl > server_msgr_async_28smp_sep_vs_left_14smp_bal_merged.svg 

# difffolded.pl _server_msgr_crimson_28smp_clients_separated.perf.out_merged _03_async_crimson_lr_fg/left_server_msgr_crimson_14smp_clients_separated.perf.out_merged | flamegraph.pl > server_msgr_crimson_28smp_vs_14smp_merged.svg

# difffolded.pl  _server_msgr_async_28smp_clients_separated.perf.out_merged _03_async_crimson_lr_fg/left_server_msgr_async_14smp_clients_separated.perf.out_merged | flamegraph.pl > server_msgr_async_28smp_vs_left_14smp_sep_merged.svg

o05:~/containers/c_results/_b1e4a2b/msgr_crimson_11/_04_single_async_crimson_lr_fg/
```
