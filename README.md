# 设备资产与维保台账

用于本地设备资产与维保记录管理的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m asset_ledger --help
python3 -m asset_ledger --version
python3 -m asset_ledger asset add --id A001 --name 笔记本 --cost 8000 --purchased-at 2026-09-26
python3 -m asset_ledger asset status --id A001 --to repairing --reason 换屏
python3 -m asset_ledger asset show --id A001
python3 -m unittest discover -s tests -v
```

资产数据持久化在仓库根目录的 `ledger.db`（SQLite）。`asset add` 登记资产并置为 `in_use`；`asset status` 仅允许 in_use↔repairing 以及任一在用状态→retired（转 retired 须给 `--reason`）；`asset show` 输出含完整状态变更历史的一行 JSON。编号唯一且不复用，每次变更追加记录，不改动历史。无参数显示帮助，未知参数以非零状态退出。
