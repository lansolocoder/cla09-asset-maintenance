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

## 折旧与残值查询

```bash
python3 -m asset_ledger depreciate --asset-id A001 \
    --as-of 2027-09-25 --useful-life-years 5 --salvage-rate 0.10
```

按直线法对指定资产核算从购置日（不含当日，自次日起计提）至 `as-of` 日的折旧情况：

- 残值 = 购置金额 × `salvage-rate`
- 应计折旧 =（购置金额 − 残值）× 已用天数 ÷（`useful-life-years` × 365），已用天数按自然日计
- 账面价值 = 购置金额 − 应计折旧
- 金额一律四舍五入到两位小数（`Decimal` 的 `ROUND_HALF_UP`）；应计折旧以"购置金额 − 残值"为上限，计算天数超过使用年限时封顶
- `as-of` 早于购置日时应计折旧为 `0.00`，账面价值等于购置金额

成功时退出码为 0，stdout 输出一行 JSON：

```json
{"asset_id": "A001", "as_of": "2027-09-25", "purchase_amount": 8999.00, "salvage_value": 899.90, "accumulated_depreciation": 1619.82, "book_value": 7379.18}
```

`useful-life-years` 必须是正整数；`salvage-rate` 必须是 0 到 1 之间（含端点）两位小数的十进制数（如 `0.00`、`0.10`、`1.00`）；`as-of` 必须是合法 `YYYY-MM-DD`。任一校验失败或资产编号不存在时退出码为 1，错误写 stderr，stdout 为空。该命令为只读查询，可对同一资产用不同 `as-of` 反复查询，台账记录不会被修改。

数据持久化在仓库根目录的 `asset_ledger.db`（SQLite），文件不存在时自动创建。尚未实现维保计划与部件更换记录。
