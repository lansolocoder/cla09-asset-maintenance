# 设备资产与维保台账

用于本地设备资产与维保记录管理的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m asset_ledger --help
python3 -m asset_ledger --version
python3 -m unittest discover -s tests -v
```

## 资产登记

```bash
python3 -m asset_ledger register \
  --asset-id A001 \
  --name "ThinkPad X1" \
  --category 笔记本电脑 \
  --location 3楼研发部 \
  --purchase-date 2026-03-15 \
  --purchase-amount 9999.00
```

- 资产编号是业务唯一键；重复登记将被拒绝（退出码非 0，错误写入 stderr），库中已有记录不变。
- 必填参数：`--asset-id`、`--name`、`--category`、`--location`、`--purchase-date`、`--purchase-amount`。
- 校验规则：编号/名称/分类/存放位置不能为空；日期必须为 `YYYY-MM-DD`；金额必须为两位小数以内的非负数。
- 登记成功时退出码 0，向 stdout 写一行结果，包含资产编号、名称与状态字面值 `in_use`。

## 台账查询

```bash
python3 -m asset_ledger list
python3 -m asset_ledger list --category 笔记本电脑
python3 -m asset_ledger list --location 3楼研发部 --category 笔记本电脑
```

- 查询结果以 JSON 输出到 stdout：顶层为对象，含 `records` 数组。
- 每条记录字段：`asset_id`、`name`、`category`、`location`、`purchase_date`（`YYYY-MM-DD`）、`purchase_amount`（数字，两位小数）、`status`（当前固定为 `in_use`）。
- `--category` 与 `--location` 可选，同时给出时取交集；无匹配时 `records` 为空数组，退出码仍为 0。
- 结果按 `asset_id` 升序排序。

数据持久化在仓库根目录的 SQLite 文件 `asset_ledger.db` 中；文件不存在时自动创建，可重复打开。未知参数或缺失必填参数将写 stderr 并以非零退出码退出，不产生业务记录。
