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
    --asset-id A001 --name 电脑 --category 办公设备 \
    --location 办公室 --purchase-date 2026-09-25 --purchase-amount 8999.00
```

资产编号是业务唯一键，重复登记会被拒绝（退出码非 0，错误写 stderr，库中记录不变）。资产编号、名称、分类、存放位置不得为空；购置日期必须是合法的 `YYYY-MM-DD`；购置金额必须是两位以内小数的非负数。登记成功退出码为 0，stdout 输出一行含资产编号、名称与状态 `in_use` 的 JSON。

## 资产台账查询

```bash
python3 -m asset_ledger query                      # 全部记录
python3 -m asset_ledger query --category 办公设备   # 按分类过滤
python3 -m asset_ledger query --location 办公室     # 按存放位置过滤
```

多个过滤条件同时给出时取交集。结果按资产编号升序输出为 JSON：`{"records": [...]}`，每条记录含 `asset_id`、`name`、`category`、`location`、`purchase_date`、`purchase_amount`（保留两位小数的数字）、`status`。无匹配时 `records` 为空数组，退出码仍为 0。

## 资产折旧计算

按年限平均法（直线法）计算指定资产截至评估日期的累计折旧与净值：

```bash
python3 -m asset_ledger depreciate --asset-id A001 --as-of 2027-09-25 --annual-rate 5
```

`--annual-rate` 为年折旧率百分数字符串（如 `"5"` 或 `"2.5"`），表示每年折旧购置金额的该百分比。折旧自购置日期起算，累计折旧按已使用天数占 365 天的比例计算（不足一年按天计），保留两位小数、四舍五入；净值 = 购置金额 - 累计折旧，同样保留两位小数。累计折旧最多折到购置金额为止，净值不低于 `0.00`。已登记但状态不是 `in_use` 的资产同样可计算。

成功时退出码为 0，stdout 输出一行 JSON，例如：

```json
{"asset_id": "A001", "as_of": "2027-09-25", "purchase_amount": 8999.00, "annual_rate": 5.0, "accumulated_depreciation": 449.95, "net_value": 8549.05}
```

评估日期必须是合法的 `YYYY-MM-DD` 且不得早于购置日期；年折旧率必须是 0–100 之间的数字；资产编号必须已登记。否则按业务错误处理：退出码非 0，错误写 stderr，stdout 为空，库中记录不变。

数据持久化在仓库根目录的 `asset_ledger.db`（SQLite），文件不存在时自动创建。尚未实现维保计划与部件更换记录。
