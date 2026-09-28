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

```bash
python3 -m asset_ledger depreciate --asset-id A001 --as-of 2027-09-25 --annual-rate 5
```

按年限平均法（直线法）计算截至评估日期的累计折旧与净值。年折旧率以百分数传入（如 `5` 或 `2.5`，表示每年折旧购置金额的该百分比）；折旧自购置日期起算，按已使用天数占 365 天的比例计算（不足一年按天计），保留两位小数、四舍五入。累计折旧最多折到购置金额为止，净值不低于 `0.00`。已登记但状态不是 `in_use` 的资产同样可计算。

成功时退出码为 0，stdout 输出一行 JSON：`{"asset_id": "A001", "as_of": "2027-09-25", "purchase_amount": 8999.00, "annual_rate": 5.0, "accumulated_depreciation": 449.95, "net_value": 8549.05}`，其中金额字段为保留两位小数的数字，`annual_rate` 为数字。评估日期早于购置日期、非法日期、非法折旧率（非数字、负数、超过 100）、未知资产编号均为业务错误（退出码非 0，错误写 stderr，stdout 为空，库中记录不变）。

数据持久化在仓库根目录的 `asset_ledger.db`（SQLite），文件不存在时自动创建。

## 维保计划登记

```bash
python3 -m asset_ledger plan \
    --asset-id A001 --plan-id P001 --item 年度校准 \
    --cycle-days 90 --next-due 2027-01-01
```

为已登记资产登记一项维保计划。计划编号是业务唯一键；同一资产可登记多项计划，但同一资产同一维保项目名称不得重复登记。`--cycle-days` 为正整数，表示每两次维保的间隔天数；首次到期日期必须是合法的 `YYYY-MM-DD`，且不得早于该资产的购置日期。资产编号、计划编号、维保项目名称均不得为空。登记成功退出码为 0，stdout 输出一行 JSON：`{"plan_id": "P001", "asset_id": "A001", "item": "年度校准", "cycle_days": 90, "next_due": "2027-01-01"}`。

## 维保到期提醒查询

```bash
python3 -m asset_ledger due --start 2026-12-01 --end 2027-03-31
```

输入开始日期与结束日期（均为合法 `YYYY-MM-DD`，开始不晚于结束），输出区间内到期的全部计划：每条计划按首次到期日期起逐次加周期天数推算，只输出落在区间内的最早一次到期日期。结果按 `due_date` 升序、同日再按 `plan_id` 升序输出为 JSON：`{"records": [...]}`，每条记录含 `plan_id`、`asset_id`、`item`、`cycle_days`、`due_date`。无匹配时 `records` 为空数组，退出码仍为 0。

计划编号重复、同一资产同一维保项目重复、未知资产编号、空字段、非法或早于购置日期的到期日期、非法周期天数（非正整数）均为业务错误；区间起止非法同理（退出码非 0，错误写 stderr，stdout 为空，库中记录不变）。

尚未实现部件更换记录。
