# 设备资产与维保台账

用于本地设备资产与维保记录管理的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m asset_ledger --help
python3 -m asset_ledger --version
python3 -m unittest discover -s tests -v
```

## 子命令

### register — 登记新资产

```bash
python3 -m asset_ledger register \
  --asset-id DEV-001 --name 示波器 --category 测量仪器 \
  --purchase-date 2024-01-15 --purchase-amount 12999.50 --location 一号仓库
```

所有字段均为必填。资产编号全局唯一，重复登记会以非零状态退出且不改动已有记录；日期必须是合法的 `YYYY-MM-DD`，金额必须是非负十进制数（按输入原样保存，不四舍五入）。登记成功后资产处于“在用”状态，初始存放位置成为位置历史的第一条记录。

### relocate — 变更存放位置

```bash
python3 -m asset_ledger relocate \
  --asset-id DEV-001 --location 二楼办公室 --change-date 2024-03-01
```

按资产编号追加一条位置变更记录，历史记录只增不改。变更日期不得早于购置日期，也不得早于上一条位置记录的日期；资产编号未登记或日期不合法时以非零状态退出，不写入任何数据。

### show — 查询资产

```bash
python3 -m asset_ledger show --asset-id DEV-001
```

输出资产基本信息、当前状态、当前存放位置以及按日期与录入顺序排列的完整位置变更历史。编号不存在时以非零状态退出并在 stderr 提示。

## 数据文件

数据用 SQLite 持久化，默认写入当前目录下的 `asset_ledger.db`；可用环境变量 `ASSET_LEDGER_DB` 指定其他路径。每次写入都在单个事务中完成，命令失败不会留下半条记录，修正输入后重新执行即可。
