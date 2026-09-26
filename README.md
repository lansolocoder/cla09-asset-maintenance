# 设备资产与维保台账

用于本地设备资产与维保记录管理的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m asset_ledger --help
python3 -m asset_ledger --version
python3 -m unittest discover -s tests -v
```

支持资产登记与状态流转，数据持久化在仓库根目录的 `ledger.db`（SQLite，仅追加事件、不改写历史）：

```bash
python3 -m asset_ledger asset add --id A001 --name 投影仪 --cost 3200 --purchased-at 2026-09-01
python3 -m asset_ledger asset status --id A001 --to repairing
python3 -m asset_ledger asset status --id A001 --to retired --reason 报废
python3 -m asset_ledger asset show --id A001   # 输出一行 JSON，含按发生顺序的完整 history
```

合法状态转换为 `in_use→repairing`、`repairing→in_use`、`in_use/repairing→retired`；`retired` 为终态。无参数显示帮助，未知参数以非零状态退出。

支持按资产登记周期性维保计划，并查询某一截止日之前到期的任务清单：

```bash
python3 -m asset_ledger plan add --id P001 --asset-id A001 --cycle-days 90 --first-due 2026-09-01
# 成功输出 plan P001 created
python3 -m asset_ledger plan due --as-of 2026-12-31
# 输出一行 JSON：{"as_of": "2026-12-31", "due": [{"plan_id": ..., "asset_id": ..., "due_date": ..., "asset_status": ...}, ...]}
```

计划标识全局唯一，重复登记直接拒绝；`--cycle-days` 必须为正整数；`--first-due` 不得晚于今日。登记后到期日按周期滚动：下一到期日由首次到期日按周期向前推算（已过去的到期日自动顺延）。`plan due` 仅列出下一到期日不晚于 `--as-of` 且资产未报废（`retired`）的计划，按 `due_date` 升序、同日按 `plan_id` 升序排列。登记或查询参数非法时在 stderr 输出 `error: ...` 并以退出码 1 拒绝，不留下任何记录。
