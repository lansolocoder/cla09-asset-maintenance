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
