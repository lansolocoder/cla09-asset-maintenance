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

## 维保计划

```bash
python3 -m asset_ledger create-plan \
    --asset-id A001 --plan-id P01 --task 清灰检查 \
    --interval-days 30 --next-due 2026-10-01
```

为资产创建周期维保计划。资产编号与计划编号是联合唯一键：同一资产下计划编号重复会被拒绝（退出码非 0，错误写 stderr，原计划不变）；不同资产可使用相同计划编号。资产编号不存在、维护内容为空、周期天数不是大于 0 的整数、日期不是合法 `YYYY-MM-DD` 同样拒绝。成功时退出码 0，stdout 输出一行含 `asset_id`、`plan_id`、`task`、`interval_days`、`next_due` 的 JSON。

```bash
python3 -m asset_ledger record-maintenance \
    --asset-id A001 --plan-id P01 --execution-date 2026-10-01
```

记录一次维保执行。执行日期早于该计划的下次执行日期时拒绝（计划不变）；否则把下次执行日期顺延为执行日期加周期天数，输出一行含 `asset_id`、`plan_id`、`next_due` 的 JSON，退出码 0。

```bash
python3 -m asset_ledger due 2026-10-05
```

查询截至指定日期已到期（含当天，`overdue_days` 为 0）的计划。输出 `{"records": [...]}`，每条记录含 `asset_id`、`plan_id`、`task`、`interval_days`、`next_due`、`overdue_days`（距到期日超出的天数），按 `asset_id`、`plan_id` 升序；未到期的计划不出现，无到期记录时为空数组，退出码 0。

数据持久化在仓库根目录的 `asset_ledger.db`（SQLite），文件不存在时自动创建。尚未实现部件更换记录以及折旧计算。
