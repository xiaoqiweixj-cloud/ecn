# ECN2to1

RoCEv2 ECN（WRED）速率均衡自动化测试 —— 遍历交换机 WRED 参数组合，驱动 IxNetwork 收发 RoCEv2 流量，监控双流速率差异，按段输出结果。

## 使用

```bash
# 完整运行（Ixia + 交换机）
python main.py

# 仅 Ixia（跳过交换机配置）
python main.py --skip-switch

# 仅配交换机（不启流量）
python main.py --skip-ixia --skip-save

# 跳过结果保存（只跑流量不出文件）
python main.py --skip-save

# 用已有数据重算
python main.py --skip-switch --skip-ixia --input-dir result/run_20260713_113731
```

### 运行参数

| 参数 | 作用 |
|------|------|
| `--skip-switch` | 跳过交换机 WRED 配置 |
| `--skip-ixia` | 跳过 Ixia 连接、流量启停和监控采集 |
| `--skip-save` | 跳过数据处理和结果保存（`summary.txt` + CSV） |
| `--input-dir <path>` | 指定已有 run 目录，读取 CSV 重算结果（隐含跳过 Ixia） |

### 调试脚本

```bash
python scripts/check_ixia.py       # 测试 Ixia API 连通性
python scripts/check_sessions.py   # 查看当前 Ixia 会话
python scripts/debug_stats.py      # 查看原始统计数据视图
```

## 环境要求

- Python ≥ 3.10
- [ixnetwork-restpy](https://pypi.org/project/ixnetwork-restpy/)
- [telnetlib3](https://pypi.org/project/telnetlib3/)
- [json5](https://pypi.org/project/json5/)

```bash
pip install ixnetwork-restpy telnetlib3 json5
```

## 配置

### `test_config.json5`

```json5
{
  "ixia": { "config_file": "ecn.ixncfg" },
  "switch": { "host": "10.140.0.142", "port": 10020 },
  "test": {
    "duration_minutes": 100,           // 每组 ECN 参数的总测试时长
    "segment_duration_minutes": 10,    // 每段时长（到点停流再重启）
    "check_interval_seconds": 10,      // 采样间隔
    "rate_diff_threshold_pct": 10.0,   // 速率差异阈值（超阈值标记 FAIL）
    "port_capacity_gbps": 400          // 端口线速，用于百分比换算
  },
  "ecn_params": [
    [100, 200, 80],    // [min_threshold, max_threshold, mark_probability]
    [100, 300, 80]
  ]
}
```

### `ixia_config.json`

```json
{
  "api_server_ip": "10.140.0.204",
  "rest_port": 443,
  "username": "admin",
  "password": "",
  "session_name": "ixia_session",
  "clear_config": true,
  "delete_on_exit": false
}
```

## 运行流程

每组 ECN 参数依次执行：

1. **停流** — 确保干净初始状态
2. **配交换机** — Telnet 登录，进入 400G 接口，下发 WRED 命令
3. **分段循环** — 总时长 ÷ 段时长 = N 段：
   - 启动流量
   - 首段：等待统计视图就绪 + 解析 Rate Tx 列索引
   - 采样监控 `segment_duration_minutes`
   - 前 30 秒为预热期，不触发阈值告警，不计入段统计
   - 超阈值 → 记录 warning，继续运行，不退出
   - 停流，保存本段 CSV
4. **汇总** — 计算平均速率、每段最差差异
5. **输出** — 每组参数一个子目录，内含 `summary.txt` + `segment_N.csv`

## 输出结构

```
result/
  run_20260713_091508/
    run_summary.csv                # 本轮所有参数汇总
    min=100_max=200_mark=80/
      summary.txt
      segment_1.csv
      segment_2.csv
      ...
    min=100_max=300_mark=80/
      summary.txt
      segment_1.csv
      ...
```

### run_summary.csv

每轮运行结束后自动生成，记录所有参数组合的结果：

```csv
min_th,max_th,mark,status,max_diff_pct
2000,8000,20,PASS:9 FAIL:1,3.93
2000,8000,80,PASS:8 FAIL:2,6.12
500,1000,80,PASS:9 FAIL:1,0.06
```

### summary.txt 示例

```
======================================================================
  Test Result - PASS:9 FAIL:1
======================================================================
  Time:           2026-07-13 09:15:08
  ECN Params:     min=100, max=200, mark=80
  Duration:       1000s
  Port Capacity:  400 Gbps
  Seg  1:   0.42% ( 198.16 Gbps/ 49.54%,  199.84 Gbps/ 49.96%) -PASS
  Seg  2:  16.57% ( 165.85 Gbps/ 41.46%,  232.11 Gbps/ 58.03%) -FAIL
  ...
```

### segment_N.csv 列说明

| 列 | 含义 |
|----|------|
| `time_s` | 段内时间（秒） |
| `flow0_gbps` | 流 0 速率（Gbps） |
| `flow0_pct` | 流 0 占比（%） |
| `flow1_gbps` | 流 1 速率（Gbps） |
| `flow1_pct` | 流 1 占比（%） |
| `diff_pct` | 双流差异绝对值（%） |

## 设计要点

- **Ixia 只连一次**：启动时连接，所有参数、所有段复用同一个会话
- **统计视图只解析一次**：首段解析 Flow Statistics 视图 ID 和 Rate Tx 列索引，后续段直接使用缓存
- **30 秒预热**：每段前 30 秒的采样不参与阈值判断和段 diff 统计，避免流量爬坡期的假差异
- **不提前退出**：超阈值只记 warning，测试跑满总时长
- **交换机配置**：通过 Telnet 下发 WRED；`--skip-switch` 可跳过交换机仅测 Ixia
- **分段独立启停**：每段到点停流再重启，每段 CSV 独立保存
- **可重算**：`--input-dir` 读取历史 CSV 重新生成 summary 和 run_summary.csv
