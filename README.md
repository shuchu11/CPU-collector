# Structure
```
CPU-collector/
├── cpu_service.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── cpu_log.xlsx          ← Excel 就直接存在這裡
```

## Build your image
```
docker compose up -d --build
```

## Steps

```
# 開始監測
curl -X POST http://localhost:5001/cpu/monitor/start \
  -H "Content-Type: application/json" \
  -d '{"xlsx": "/app/cpu_log.xlsx"}'







##### Excel 檔案結構會長這樣( Excel 存在 host 的 CPU-collector/ 目錄 ) :

#####################################################################
**規則：**
- 每秒一行
- 第一欄永遠是 timestamp（格式 YYYY-MM-DD HH:MM:SS）
- 之後每欄是一個 CPU core，順序從 cpu0 到 cpu47（按數字排序）
- 每格的值是該秒該 core 的使用率（%），例如 4.5 代表 4.5%
#####################################################################

| timestamp           | cpu0 | cpu1 | cpu2 | ... | cpu47 |
| ------------------- | ---- | ---- | ---- | --- | ----- |
| 2026-05-04 10:00:01 | 4.5  | 12.3 | 0.0  | ... | 21.7  |
| 2026-05-04 10:00:02 | 3.9  | 8.1  | 1.0  | ... | 19.2  |
| 2026-05-04 10:00:03 | 5.2  | 15.6 | 0.0  | ... | 23.4  |
| ...                 | ...  | ...  | ...  | ... | ...   |
```



```
# 畫圖
curl -X POST http://localhost:5001/cpu/plot \
  -H "Content-Type: application/json" \
  -d '{"xlsx": "/app/cpu_log.xlsx", "type": "both"}' \
  --output cpu_plot.png
```


```
# 確認 container 狀態
sudo docker compose ps

# 測試 health check
curl http://localhost:5001/health

# 停止監測
sudo docker compose down
```


## Appendix


# BMC 與 OS 可收集的 CPU 資料與量測方式整理

## 1. 總覽

伺服器上的 CPU 資訊大致可以分成兩類來源：

| 來源 | 主要用途 | 是否依賴 OS 開機 |
|---|---|---|
| BMC / iDRAC / iLO / XCC | 硬體健康監控、遠端管理、溫度、電源、風扇、硬體狀態 | 大多不依賴 OS |
| OS / Linux | CPU 使用率、Load Average、Process 使用量、Core 使用率、排程狀態 | 需要 OS 正常運作 |

BMC 主要負責「硬體層級監控」，OS 主要負責「系統運作與 CPU loading 分析」。

---

## 2. BMC 可以收集哪些 CPU 相關資料？

BMC 是主機板上的獨立管理控制器，即使 OS 沒有開機，BMC 通常仍然可以運作。

常見 BMC 可收集的 CPU / 系統硬體資料如下：

| 資料類型 | 說明 | 常見來源 |
|---|---|---|
| CPU Temperature | CPU 溫度 | CPU 內建溫度感測器、CPU socket 附近溫度感測器 |
| CPU Power | CPU 功耗 | VRM、CPU RAPL、PMBus、平台電源監控 |
| CPU Voltage | CPU 電壓 | VRM 電壓監控、主機板硬體監控晶片 |
| Fan Speed | 風扇轉速 | 風扇 Tachometer 訊號 |
| System Power | 整機功耗 | PSU 電源供應器、PMBus、主機板電源監控 |
| CPU Hardware Status | CPU 是否存在、錯誤狀態、硬體告警 | BMC Sensor / SEL / Redfish |

---

## 3. BMC 的資料是怎麼量測的？

BMC 多數硬體資料不是從 OS 檔案讀取，而是透過硬體感測器與管理匯流排取得。

整體概念如下：

```text
CPU / VRM / PSU / Fan / Sensor Chip
        ↓
I2C / SMBus / PMBus / PECI / IPMI Sensor Interface
        ↓
BMC
        ↓
Web UI / IPMI / Redfish
```

- BMC picture
<img width="1227" height="866" alt="image" src="https://github.com/user-attachments/assets/19f188d5-0b5b-4a39-89f6-b5837b53ffb3" />
<img width="1283" height="681" alt="image" src="https://github.com/user-attachments/assets/02ba2955-2994-4db4-8401-6b220855da6f" />

# OS 如何計算 CPU Usage？

Linux 的 CPU usage 通常是從以下檔案計算：

```bash
/proc/stat
```

查看內容：

```bash
cat /proc/stat | head
```

會看到類似以下內容：

```text
cpu  123456 789 34567 987654 1234 0 456 0 0 0
cpu0 12345 67 3456 98765 123 0 45 0 0 0
cpu1 12456 70 3460 98600 130 0 50 0 0 0
```

其中：

- 第一行 `cpu` 是所有 CPU core 的總和
- `cpu0`, `cpu1`, `cpu2` 則是每個 CPU core 的統計

---

## `/proc/stat` CPU 欄位說明

`/proc/stat` 中 CPU 欄位格式如下：

```text
cpu  user nice system idle iowait irq softirq steal guest guest_nice
```

| 欄位 | 說明 |
|---|---|
| `user` | 使用者程式使用 CPU 的時間 |
| `nice` | nice priority 的使用者程式時間 |
| `system` | kernel 使用 CPU 的時間 |
| `idle` | CPU 閒置時間 |
| `iowait` | CPU 等待 I/O 的時間 |
| `irq` | 硬體中斷時間 |
| `softirq` | 軟體中斷時間 |
| `steal` | VM 環境中被 hypervisor 拿走的 CPU 時間 |
| `guest` | Guest VM 使用的 CPU 時間 |
| `guest_nice` | nice priority guest VM 使用時間 |

這些數值不是百分比，而是累積的 **jiffies / clock ticks**。

---

## CPU Usage 計算方式

CPU 使用率不能只讀一次 `/proc/stat`，需要讀兩次，取時間差後計算。

公式如下：

```text
total = user + nice + system + idle + iowait + irq + softirq + steal
```

```text
idle_all = idle + iowait
```

```text
cpu_usage = (total_delta - idle_all_delta) / total_delta * 100
```

也可以理解成：

```text
CPU 使用率 = 非 idle 時間 / 總 CPU 時間
```

---

## CPU Usage 計算範例

### 第一次讀取

```text
cpu  100 0 50 850 0 0 0 0
```

### 隔 1 秒後第二次讀取

```text
cpu  120 0 60 920 0 0 0 0
```

---

## 1. 計算 total

```text
total1 = 100 + 0 + 50 + 850 = 1000
total2 = 120 + 0 + 60 + 920 = 1100

total_delta = 1100 - 1000 = 100
```

---

## 2. 計算 idle

```text
idle1 = 850
idle2 = 920

idle_delta = 920 - 850 = 70
```

---

## 3. 計算 CPU usage

```text
cpu_usage = (100 - 70) / 100 * 100 = 30%
```

所以這段時間內 CPU 使用率是：

```text
30%
```
