## Table of Contents

- [Project Structure](#project-structure)
- [First-Time Startup](#first-time-startup)
- [Function 1.0 : CPU Monitoring](#function-10--cpu-monitoring)
- [Function 1.1 : CPU Plotting](#function-11--cpu-plotting)
- [Function 2.0 Thread Monitoring](#function-20-thread-monitoring)
- [Function 2.1 Thread Plotting](#function-21-thread-plotting)
- [Common Operation Flow](#common-operation-flow)
  - [Long-Term Monitoring, for Example Two Days](#long-term-monitoring-for-example-two-days)
  - [Continue After Restarting the Container](#continue-after-restarting-the-container)
- [Notes](#notes)

---

## Project Structure

```text
CPU-collector/
├── cpu_service.py       # Main program
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── cpu_log_<timestamp>.xlsx        # CPU monitoring data, automatically generated
├── thread_log_<timestamp>.xlsx     # Thread monitoring data, automatically generated
├── cpu_plot_heatmap_<timestamp>.png
├── cpu_plot_timeseries_<timestamp>.png
├── thread_plot_affinity_<timestamp>.png
└── thread_plot_cpu_usage_<timestamp>.png
```

---

## First-Time Startup

Clone the Project

```bash
git clone https://github.com/shuchu11/CPU-collector.git
cd CPU-collector
```

Build and Start the Container

```bash
sudo docker compose up -d --build
```

Verify That the Service Is Running

```bash
curl http://localhost:5001/health
# Expected response: {"status": "ok"}
```

---

## Function 1.0 : CPU Monitoring

### Start Monitoring

```bash
curl -X POST http://localhost:5001/cpu/monitor/start \
  -H "Content-Type: application/json" \
  -d '{}'
```


Check Monitoring Status
```bash
curl http://localhost:5001/cpu/monitor/status


#### Log ####
json
{
  "running": true,
  "xlsx": "/app/cpu_log_20260504_100000.xlsx",   <-------- This is your file's name. Please remember it .
  "rows_written": 3600,
  "started_at": "2026-05-04T10:00:00"
}
```
> Data in the cpu_log_XXXX.xlsx will be stored as the format
> | timestamp           | cpu0 | cpu1 | ... | cpu47 |
> | ------------------- | ---- | ---- | --- | ----- |
> | 2026-05-04 10:00:01 | 4.5  | 12.3 | ... | 21.7  |
> 
> Each row represents one second. Each cell records the CPU usage percentage of the corresponding CPU core.

How to Stop Monitoring

```bash
curl -X POST http://localhost:5001/cpu/monitor/stop
```

---

## Function 1.1 : CPU Plotting

Generate plots from the Excel data. This will create two separate image files:

```bash
curl -X POST http://localhost:5001/cpu/plot \
  -H "Content-Type: application/json" \
  -d '{
    "xlsx": "/app/cpu_log_20260504_100000.xlsx",
    "label": "my run"
  }'
```

Example response:

```json
{
  "heatmap": "/app/cpu_plot_heatmap_20260504_102300.png",
  "timeseries": "/app/cpu_plot_timeseries_20260504_102300.png"
}
```

The two image files will be saved directly in the project directory.

### Optional Parameters

| Parameter | Description               | Example                 |
| --------- | ------------------------- | ----------------------- |
| `xlsx`    | Excel file path, required | `/app/cpu_log_xxx.xlsx` |
| `label`   | Chart title suffix        | `"Day 1 Test"`          |
| `start`   | Start time filter         | `"2026-05-04 10:00:00"` |
| `end`     | End time filter           | `"2026-05-04 12:00:00"` |


> `cpu_plot_heatmap_*.png` : 
> Provides an overview of the minimum, average, and maximum CPU usage for each CPU core.
> 
> `cpu_plot_timeseries_*.png` :
> Shows the CPU usage trend of each CPU core over time.

## Function 2.0 Thread Monitoring

Monitor all threads of a specified process and record CPU usage and core affinity.

### Start Monitoring

Please replace `{{Proccess name}}` with your proccess's name. ( List all proccess running on your server : `sudo docker exec cpu-service nsenter -t 1 -m -u -n -i bash -c "ps aux --no-header | awk '{print \$11}' | sort -u"` )
```bash
curl -X POST http://localhost:5001/thread/monitor/start \
  -H "Content-Type: application/json" \
  -d '{
    "pgrep": "{Proccess name}",
    "sample_interval": 10
  }'
```

| Parameter         | Description                  | Default Value           |
| ----------------- | ---------------------------- | ----------------------- |
| `pgrep`           | Process name to monitor      | `nr-softmodem`          |
| `sample_interval` | Sampling interval in seconds | `10`                    |
| `xlsx`            | Output file path             | Automatically generated |


How to Stop Monitoring
```bash
curl -X POST http://localhost:5001/thread/monitor/stop
```

> **Excel Data Format**
> 
> Sheet name:
> 
> ```text
> thread_cpu
> ```
> 
> | timestamp           | tid    | name         | avg_cpu | min_cpu | max_cpu | primary_core | core_0 | core_1 | ... |
> | ------------------- | ------ | ------------ | ------- | ------- | ------- | ------------ | ------ | ------ | --- |
> | 2026-05-04 10:00:10 | 282158 | nr-softmodem | 45.2    | 30.1    | 89.3    | 3            | 0      | 0      | ... |
> 
> A batch of data is written every `sample_interval` seconds. Each thread is recorded as one row.
>
> The values in the core columns represent the percentage of time that the thread ran on each CPU core.

---

## Function 2.1 Thread Plotting

Please replace `{thread_log_xxx.xlsx}` with your `.xlsl` name ( You can use `curl http://localhost:5001/thread/monitor/status
` to check your thread_log file's name)  
```bash
curl -X POST http://localhost:5001/thread/plot \
  -H "Content-Type: application/json" \
  -d '{
    "xlsx": "/app/{thread_log_xxx.xlsx}",
    "label": "Lavoisier Run 1"
  }'
```

Example response:

```json
{
  "affinity": "/app/thread_plot_affinity_20260504_102300.png",
  "cpu_usage": "/app/thread_plot_cpu_usage_20260504_102300.png"
}
```

**Plot Description**
`thread_plot_affinity_*.png` : 
Shows the runtime distribution of each thread across CPU cores. This corresponds to the original Thread-to-Core Affinity plot from `bitrate_sweep.py`.

`thread_plot_cpu_usage_*.png` : Shows the minimum, average, and maximum CPU usage of each thread.

---

## Common Operation Flow

### Long-Term Monitoring, for Example Two Days

```bash
# 1. Start CPU and Thread monitoring at the same time
curl -X POST http://localhost:5001/cpu/monitor/start \
  -H "Content-Type: application/json" \
  -d '{}'

curl -X POST http://localhost:5001/thread/monitor/start \
  -H "Content-Type: application/json" \
  -d '{"pgrep": "nr-softmodem", "sample_interval": 10}'

# 2. Check status at any time
curl http://localhost:5001/cpu/monitor/status
curl http://localhost:5001/thread/monitor/status

# 3. Generate plots at any time without stopping monitoring
curl -X POST http://localhost:5001/cpu/plot \
  -H "Content-Type: application/json" \
  -d '{"xlsx": "/app/cpu_log_<timestamp>.xlsx", "label": "intermediate check"}'

# 4. Stop monitoring
curl -X POST http://localhost:5001/cpu/monitor/stop
curl -X POST http://localhost:5001/thread/monitor/stop

# 5. Generate final plots
curl -X POST http://localhost:5001/thread/plot \
  -H "Content-Type: application/json" \
  -d '{"xlsx": "/app/thread_log_<timestamp>.xlsx", "label": "final result"}'
```

### Continue After Restarting the Container

The Excel data is stored in the host project directory, so it will not be lost after restarting the container.

```bash
sudo docker compose down
sudo docker compose up -d

# Call /cpu/monitor/start again to continue monitoring.
# A new xlsx file will be generated.
```

---

## Notes

* The container requires `--privileged` permission, which is already configured in `docker-compose.yml`, so it can use `nsenter` to read the host `/proc`.
* Thread monitoring depends on `pgrep` to find the target process. Make sure the target process is running before starting monitoring.
* When specifying a `start` or `end` time range for plotting, the time format must be:

```text
YYYY-MM-DD HH:MM:SS
```

* For long-term monitoring, such as two days, the CPU Excel file is approximately 30–40 MB.
* The Thread Excel file size depends on the number of threads and the configured `sample_interval`.

