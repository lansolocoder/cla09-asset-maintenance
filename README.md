# 设备资产与维保台账

用于本地设备资产与维保记录管理的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m asset_ledger --help
python3 -m asset_ledger --version
python3 -m unittest discover -s tests -v
```

## 数据文件

台账数据保存在本地 SQLite 文件中，默认使用当前工作目录下的 `asset_ledger.db`，
可用 `--db PATH` 指定（`--db` 放在子命令前后均可）。文件不存在时在首次成功登记时
自动创建；查看、列表等只读操作以及任何失败的登记都不会创建空文件。

## 资产登记

```bash
python3 -m asset_ledger register \
  --tag NB-001 --name "笔记本电脑" --category 电脑 \
  --purchase-date 2026-01-15 --location "北京-机房A"
```

- 必填：资产标签 `--tag`、名称 `--name`、类别 `--category`、购置日期
  `--purchase-date`（统一 `YYYY-MM-DD`）、初始存放位置 `--location`。
- 资产标签在台账内唯一，按精确匹配判重。
- 登记成功（退出码 0）输出系统分配的台账编号、资产标签和初始存放位置。
- 可携带 `--request-id` 支持安全重试：同一请求标识重复提交且内容一致时，
  直接返回首次登记的同一台账编号和相同结果，不会新建第二条资产；同一请求标识
  携带不同内容时拒绝并说明原因。失败的尝试不会写入或占用该请求标识。
- 标签重复、日期不合法、必填项缺失均以非零退出，错误写入 stderr，
  不会留下半条记录，也不会改动既有数据。

## 位置变更

```bash
python3 -m asset_ledger move --tag NB-001 --location "上海-B座" \
  --date 2026-03-01 --note "部门搬迁"
```

- 按资产标签指定资产，输入目标存放位置、变更日期，可附变更说明 `--note`。
- 成功后输出新的当前位置（退出码 0），并在变更历史中按发生顺序追加一条记录，
  记录包含变更日期、变更前后位置和说明，一经产生不被后续操作改写或覆盖。
- 以下情况操作失败（非零退出，错误写入 stderr，当前位置与历史保持原样）：
  目标资产不存在、目标位置与当前位置相同、变更日期不合法、变更日期早于该资产
  最近一次变更的日期（同一天的再次变更允许）。

## 查询

查看单个资产（标签、名称、类别、购置日期、当前存放位置、完整变更历史；
从未变更时显示“尚无变更记录”）：

```bash
python3 -m asset_ledger show --tag NB-001
```

列出全部资产，按台账编号升序输出摘要行，可用 `--category` 按类别筛选：

```bash
python3 -m asset_ledger list
python3 -m asset_ledger list --category 电脑
```

无参数显示帮助；`--help` / `--version` 行为不变；未知参数以非零状态退出。

后续规划：维保计划、部件更换记录以及折旧计算。
