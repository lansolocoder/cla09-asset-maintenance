# 设备资产与维保台账

用于本地设备资产与维保记录管理的命令行项目。

需要 Python 3.12，无第三方依赖（仅标准库 + SQLite）。数据持久化到仓库根目录的 `asset_ledger.sqlite3`（可用环境变量 `ASSET_LEDGER_DB` 覆盖路径）。

## 命令

无参数或 `--help` 显示帮助，`--version` 输出版本，未知参数以非零状态退出。所有业务命令成功时向 stdout 输出一行 JSON；失败时向 stderr 输出 `{"error":{"code": ..., "message": ...}}`，并以非零状态退出，且事务完整回滚。

### register — 登记资产

成功后状态为“在库”（`in_stock`），当前位置为初始位置，并写入不可变的登记事件。

```bash
python3 -m asset_ledger register \
  --asset-code A001 --name 笔记本电脑 --category IT设备 \
  --purchase-date 2026-01-15 --purchase-amount 8999.50 \
  --location-code WH-01 --location-name 一号库房
```

资产编号稳定唯一。重复编号（`duplicate-asset`）、非法日期/金额/必填字段（`validation-error`）均失败且不产生任何记录。金额须为正数、最多两位小数。

### activate — 启用资产

仅允许“在库”（`in_stock`）→“使用中”（`in_use`）；生效时间不得早于采购日期（`effective-before-purchase`），状态不符返回 `invalid-state`。

```bash
python3 -m asset_ledger activate --asset-code A001 \
  --request-id REQ-1 --effective-at 2026-02-01T09:00:00
```

`--request-id` 为幂等键：同一 request_id 同载荷重放返回原成功结果且不追加事件；异载荷返回 `request-conflict` 并保持原状态。

### move — 位置变更

“在库”或“使用中”均可执行；不存在的资产返回 `asset-not-found`。成功时同一事务内更新当前位置并追加不可变移动历史。

```bash
python3 -m asset_ledger move --asset-code A001 \
  --to-location-code OFFICE-3 --to-location-name 三号办公室 \
  --reason 领用 --request-id MV-1 --effective-at 2026-03-01T10:00:00
```

允许迟到记录：生效时间早于已有事件仍可提交。历史按生效时间升序、同一生效时间按提交顺序输出，旧记录不改写、不删除；当前位置始终取生效时间最新（同生效时间取最后提交）的移动记录。同一 request_id 同样支持重放/冲突检测。

### show / history — 查询

```bash
python3 -m asset_ledger show --asset-code A001
python3 -m asset_ledger history --asset-code A001
```

- 资产 JSON 至少含：`asset_code`、`name`、`category`、`purchase_date`、`purchase_amount`、`status`、`current_location`。
- 历史每项至少含：`effective_at`、`recorded_at`、`type`、`from_location`、`to_location`、`reason`、`request_id`。

### 稳定错误码

| code | 含义 |
| --- | --- |
| `usage-error` | 参数缺失或未知参数（exit 2） |
| `validation-error` | 日期/金额/必填字段非法 |
| `duplicate-asset` | 资产编号已存在 |
| `asset-not-found` | 资产不存在 |
| `invalid-state` | 当前状态不允许该操作 |
| `effective-before-purchase` | 启用生效时间早于采购日期 |
| `request-conflict` | request_id 已用于不同载荷 |
| `db-error` | 数据库错误（事务回滚） |

## 测试

```bash
python3 -m asset_ledger --help
python3 -m asset_ledger --version
python3 -m unittest discover -s tests -v
```
