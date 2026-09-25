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

## 资产折旧查询

```bash
python3 -m asset_ledger depreciation --asset-id A001
python3 -m asset_ledger depreciation --asset-id A001 --as-of 2026-09-30
```

按直线法计算单条资产截至指定日期的折旧：残值固定为购置金额的 5%，折旧年限固定 5 年，年折旧额 =（购置金额 − 残值）÷ 5，月折旧额 = 年折旧额 ÷ 12，所有金额一律四舍五入（half-up）保留两位小数。`--as-of` 可省略，默认取系统当天；已折旧月数为购置日期到该日期经过的**整月数**（不足整月不计），累计折旧 = 月折旧额 × 已折旧月数，账面净值 = 购置金额 − 累计折旧，且不超过购置金额、不低于残值（折旧期满后净值保持残值）。`--as-of` 早于购置日期时已折旧月数为 0、累计折旧为 0.00、净值等于购置金额。

成功时退出码 0，stdout 输出一行 JSON：

```json
{"asset_id": "A001", "purchase_amount": 8999.00, "residual_amount": 449.95, "as_of": "2026-09-30", "elapsed_months": 0, "monthly_depreciation": 142.48, "accumulated_depreciation": 0.00, "net_book_value": 8999.00}
```

资产编号不存在或 `--as-of` 不是合法的 `YYYY-MM-DD` 时，退出码非 0、错误写 stderr、stdout 无输出。折旧查询为只读操作，不修改台账中的任何字段。

## 整月折旧汇总

```bash
python3 -m asset_ledger depreciation-summary --month 2026-09
```

按与单条折旧查询完全一致的口径（直线法、残值 5%、月折旧额 half-up 两位小数）对全部资产输出指定月份的汇总，stdout 输出一行 JSON：

```json
{"month": "2026-09", "records": [...], "totals": {"total_monthly": ..., "total_accumulated": ..., "total_net_book_value": ...}}
```

`--month` 必填，格式为 `YYYY-MM`，且必须是真实月份（如 `2026-13` 非法）。records 按 `asset_id` 升序，每条含 `asset_id`、`purchase_amount`、`monthly_depreciation`、`accumulated_depreciation`（截至该月月末）、`net_book_value`（截至该月月末），金额均为两位小数数字。该月月末即该月最后一天，是累计折旧与净值的截止日。

当月折旧额仅当该月已过整月（月末不早于购置日对应的整月时点；购置当月不计提）时等于月折旧额，否则为 `0.00`。折旧期（60 个月）已满的资产，当月折旧额取 `min(月折旧额, 可折旧总额 − 该月月初累计折旧)`，不重复计提；月折旧额四舍五入造成的尾差在最后一个计提月一次性补齐，期满后各月均为 `0.00`。累计折旧不超过可折旧总额、净值不低于残值。

totals 固定含 `total_monthly`、`total_accumulated`、`total_net_book_value` 三个两位小数数字。台账为空时 records 为空数组、totals 各值为 `0.00`，退出码仍为 0。`--month` 非法时退出码非 0、错误写 stderr、stdout 无输出。汇总为只读操作，不修改台账中的任何字段，也不影响其他命令的输出。

## 维修工单

登记工单（初始状态为 `submitted`）：

```bash
python3 -m asset_ledger register-ticket \
    --ticket-id WX001 --asset-id A001 \
    --fault-description 无法开机 --submitted-date 2026-09-20
```

必填项为工单号、资产编号、故障描述、送修日期。成功时退出码 0，stdout 输出一行含 `asset_id`、`ticket_id`、`status` 的 JSON。工单号是业务唯一键，重复登记同一工单号会被整体拒绝（退出码非 0、错误写 stderr、stdout 无输出），已有工单保持不变；故障描述不得为空；资产不存在时拒绝且不产生记录。送修日期必须是合法 `YYYY-MM-DD`，且不得早于该资产购置日期（等于购置日期允许）；晚于当天的送修日期按原值保存，不视为错误。

完成工单：

```bash
python3 -m asset_ledger complete-ticket --ticket-id WX001 --completed-date 2026-09-25
```

完成日期必须是合法 `YYYY-MM-DD` 且不得早于送修日期。成功后状态变为 `completed` 并保存完成日期，stdout 输出一行含 `ticket_id`、`status` 的 JSON。工单不存在、已完成工单再次完成（完成日期保持不变）、日期非法或早于送修日期时，均退出码非 0、错误写 stderr、stdout 无输出，库中已有记录不变。

按资产列出工单：

```bash
python3 -m asset_ledger list-tickets --asset-id A001
```

按工单号升序输出一行 JSON：`{"asset_id": "...", "tickets": [...]}`，每条工单含 `ticket_id`、`fault_description`、`submitted_date`、`status`、`completed_date`；已完成工单的 `completed_date` 为保存的日期字符串，未完成为 `null`。资产不存在时退出码非 0、错误写 stderr、stdout 无输出；资产没有工单时 `tickets` 为空数组、退出码 0。

工单的登记、完成与查询均不修改资产的 `name`、`location`、`purchase_date`、`purchase_amount`、`status` 字段，也不影响折旧与汇总命令的输出。

数据持久化在仓库根目录的 `asset_ledger.db`（SQLite），文件不存在时自动创建。尚未实现维保计划与部件更换记录。
