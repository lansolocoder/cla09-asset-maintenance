# 设备资产与维保台账

用于本地设备资产与维保记录管理的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m asset_ledger --help
python3 -m asset_ledger --version
python3 -m unittest discover -s tests -v
```

## 数据文件

台账数据保存在本地 SQLite 文件中，默认使用当前工作目录下的
`asset_ledger.db`，可用 `--db PATH` 指定其他路径（根参数或子命令后均可）：

```bash
python3 -m asset_ledger --db ./data/ledger.db list
python3 -m asset_ledger list --db ./data/ledger.db
```

数据库文件不存在时，首次成功的资产登记会自动创建；失败的登记以及
`show`/`list` 等只读操作不会创建文件。

## 资产登记

```bash
python3 -m asset_ledger register \
  --tag A001 --name "MacBook Pro" --category laptop \
  --purchase-date 2026-01-15 --location "Office A-12"
```

- 资产标签在台账内唯一，按精确匹配判重。
- 购置日期统一使用 `YYYY-MM-DD`，非法日期（含 `2026-02-30` 等不存在的日期）拒绝登记。
- 登记成功后输出台账编号、资产标签和初始存放位置，退出码 0。
- 标签重复、日期非法或必填项缺失时以非零退出，错误写入 stderr；
  不会留下半条记录，也不会改动既有数据。

### 安全重试（请求标识）

登记时可通过 `--request-id` 携带请求标识：

```bash
python3 -m asset_ledger register ... --request-id <唯一标识>
```

- 同一请求标识重复提交且登记内容完全一致时，不新建资产，直接返回首次
  登记的同一台账编号和相同结果。
- 同一请求标识携带了不同的登记内容时拒绝，并说明原因。
- 校验失败的尝试不会占用或污染该请求标识，之后仍可用该标识正常登记。

## 位置变更

```bash
python3 -m asset_ledger move --tag A001 \
  --location "Warehouse B-3" --date 2026-02-01 --note "调拨至仓库"
```

成功后输出新的当前位置，并在变更历史中追加一条记录（含变更日期、
变更前后位置和说明）。历史按发生顺序保存，一经产生不可改写或覆盖。

以下情况操作失败（非零退出、错误写入 stderr，当前位置与历史保持原样）：

- 目标资产不存在；
- 目标位置与当前位置相同；
- 变更日期不合法；
- 变更日期早于该资产已有变更历史中最近一次变更的日期（同一天允许）。

## 查询

查看单个资产（含完整变更历史；从未变更时明确显示尚无变更记录）：

```bash
python3 -m asset_ledger show --tag A001
```

列出全部资产（按台账编号升序输出摘要行），可按类别筛选：

```bash
python3 -m asset_ledger list
python3 -m asset_ledger list --category laptop
```

## 约定

无参数显示帮助；`--help`/`--version` 行为不变；未知参数以非零状态退出
（错误写入 stderr）。
