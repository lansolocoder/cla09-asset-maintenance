# 设备资产与维保台账

用于本地设备资产与维保记录管理的命令行项目。

需要 Python 3.12，无第三方依赖。数据持久化到仓库根目录的 `asset_ledger.sqlite3`（SQLite），可用环境变量 `ASSET_LEDGER_DB` 覆盖数据库路径。

```bash
python3 -m asset_ledger --help
python3 -m asset_ledger --version
python3 -m unittest discover -s tests -v
```

无参数或 `--help` 显示帮助，`--version` 输出版本，未知参数以非零状态退出。所有业务错误写入 stderr，格式为 `error: <稳定错误码>: <说明>` 并以非零状态退出；成功时向 stdout 输出一行 JSON。

## 业务命令

### register —— 登记资产

```bash
python3 -m asset_ledger register \
  --asset-code A001 --name Laptop --category IT \
  --purchase-date 2026-01-15 --purchase-amount 9999.99 \
  --location-code BJ-01 --location-name Beijing
```

资产编号稳定唯一；采购日期必须为合法 `YYYY-MM-DD`；采购金额为正数且最多两位小数；位置名称可选。登记成功后状态为“在库”，当前位置为初始位置。重复编号（`duplicate-asset`）、非法日期（`invalid-date`）、非法金额（`invalid-amount`）、必填缺失（`missing-field`）均失败且不产生记录。

### activate —— 启用

```bash
python3 -m asset_ledger activate \
  --asset-code A001 --request-id R1 --effective-at 2026-02-01T10:00:00
```

仅允许“在库”→“使用中”（`invalid-status-transition`），生效时间不得早于采购日期（`effective-before-purchase`）。同一 `request_id` 以相同载荷重放返回原成功结果且不追加事件；载荷不同返回 `request-conflict` 且状态不变。

### move —— 位置变更

```bash
python3 -m asset_ledger move \
  --asset-code A001 --request-id M1 --effective-at 2026-03-01T09:00:00 \
  --to-location-code SH-02 --to-location-name Shanghai --reason relocate
```

“在库”“使用中”均可执行，资产不存在返回 `asset-not-found`。当前位置更新与移动历史追加在同一事务内完成，任何失败完整回滚。

移动历史不可变，允许迟到记录：生效时间早于已有事件仍可提交，历史始终按生效时间升序、同一生效时间按提交顺序（`recorded_at`/`seq`）输出，旧记录不会被改写或删除；当前位置取生效时间最新（平局取提交更晚）的移动记录。

### get / history —— 查询

```bash
python3 -m asset_ledger get --asset-code A001
python3 -m asset_ledger history --asset-code A001
```

`get` 输出资产 JSON，至少含 `asset_code`、`name`、`category`、`purchase_date`、`purchase_amount`、`status`、`current_location`；`history` 输出事件 JSON 数组，每项至少含 `effective_at`、`recorded_at`、`type`、`from_location`、`to_location`、`reason`、`request_id`。

重启后已提交记录仍可查询，不丢失、不重复。
