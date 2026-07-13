# ECN2to1

RoCEv2 ECN（WRED）速率均衡自动化测试 —— 遍历交换机 WRED 参数组合，驱动 IxNetwork 收发 RoCEv2 流量，监控双流速率差异，按段输出结果。

## 项目结构

```
main.py                      测试主入口，遍历 ECN 参数，协调各模块
switch/switch_config.py      通过 Telnet 在交换机 400G 端口配置 WRED
ixia/connect.py              IxNetwork REST API 会话管理
ixia/run.py                  流量启停 + RoCEv2 Flow Statistics 采集
analysis/data_processor.py   从采样数据计算汇总统计
analysis/result_saver.py     输出 summary.txt + 分段 CSV
test_config.json5            测试参数配置（ECN 组合、时长、阈值等）
ixia_config.json             Ixia 服务器连接凭证
ecn.ixncfg                   IxNetwork 流量配置文件
logger.py                    统一日志模块
scripts/                     调试/工具脚本
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

## 使用

```bash
# 完整运行（Ixia + 交换机）
python main.py

# 仅 Ixia（跳过交换机配置）
python main.py --skip-switch

# 调试脚本
python scripts/check_ixia.py       # 测试 Ixia API 连通性
python scripts/check_sessions.py   # 查看当前 Ixia 会话
python scripts/debug_stats.py      # 查看原始统计数据视图
```

## 运行流程

每组 ECN 参数依次执行：

1. **停流** — 确保干净初始状态
2. **配交换机** — Telnet 登录，进入 400G 接口，下发 WRED 命令
3. **分段循环** — 总时长 ÷ 段时长 = N 段：
   - 启动流量
   - 首段：等待统计视图就绪 + 解析 Rate Tx 列索引
   - 采样监控 `segment_duration_minutes`
   - 前 5 秒为预热期，不触发阈值告警，不计入段统计
   - 超阈值 → 记录 warning，继续运行，不退出
   - 停流，保存本段 CSV
4. **汇总** — 计算平均速率、每段最差差异
5. **输出** — 每组参数一个子目录，内含 `summary.txt` + `segment_N.csv`

## 输出结构

```
result/
  run_20260713_091508/
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

### summary.txt 示例

```
======================================================================
  Test Result - PASS:1 FAIL:9
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
- **5 秒预热**：每段前 5 秒的采样不参与阈值判断和段 diff 统计，避免流量爬坡期的假差异
- **不提前退出**：超阈值只记 warning，测试跑满总时长
- **交换机配置**：通过 Telnet 下发 WRED；`--skip-switch` 可跳过交换机仅测 Ixia
