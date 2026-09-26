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

多个过滤条件同时给出时取交集，`--location` 按资产的**当前**存放位置匹配（历史位置不命中）。结果按资产编号升序输出为 JSON：`{"records": [...]}`，每条记录含 `asset_id`、`name`、`category`、`location`、`purchase_date`、`purchase_amount`（保留两位小数的数字）、`status`、`move_count`（该资产累计存放位置变更次数，从未变更为 `0`）。无匹配时 `records` 为空数组，退出码仍为 0。

## 存放位置变更

```bash
python3 -m asset_ledger move \
    --asset-id A001 --to-location 仓库C --effective-date 2027-01-15
```

把指定资产的存放位置变更为新位置，并记录一次变更事实。后续 `query`（含 `--location` 过滤）与 `depreciate` 都基于变更后的状态。

- `--asset-id`、`--to-location`、`--effective-date` 均必填
- 新位置去除首尾空白后不得为空（落库与输出均使用去空白后的值）
- 生效日期必须是合法 `YYYY-MM-DD`，且不得早于该资产的购置日期（等于购置日允许）
- 目标位置与当前位置相同视为无效变更
- 资产必须已登记

任一校验失败时退出码为 1，错误写 stderr、stdout 为空，资产位置与变更记录均保持不变。位置更新与变更记录在同一事务中提交，要么同时落库、要么都不落库。

成功时退出码为 0，stdout 输出一行 JSON：

```json
{"asset_id": "A001", "from_location": "办公室", "to_location": "仓库C", "effective_date": "2027-01-15", "sequence": 1}
```

`sequence` 是该资产变更记录的顺序号，从 1 开始。同一资产允许在相同 `effective-date` 上多次移动（产生连续 `sequence`，最终位置以最后落库一条为准）；移动回曾经的旧位置也正常记录，不合并、不跳过。

### 查看变更事实

```bash
python3 -m asset_ledger moves --asset-id A001
```

按 `sequence` 升序输出该资产的全部变更事实：`{"records": [...]}`，每条含 `from_location`、`to_location`、`effective_date`、`sequence`。从未变更过的资产输出 `{"records": []}`；资产未登记时退出码为 1，错误写 stderr、stdout 为空。

## 折旧核算

```bash
python3 -m asset_ledger depreciate \
    --asset-id A001 --as-of 2027-09-25 \
    --useful-life-years 5 --salvage-rate 0.10
```

按直线法计算指定资产自购置日次日起至 `as-of` 日的应计折旧与账面价值：

- 残值 = 购置金额 × `salvage-rate`
- 应计折旧 =（购置金额 − 残值）× 已用天数 ÷（`useful-life-years` × 365），已用天数按自然日计
- 账面价值 = 购置金额 − 应计折旧

金额均用 `Decimal` 的 `ROUND_HALF_UP` 四舍五入到两位小数。`as-of` 早于或等于购置日时应计折旧为 `0.00`、账面价值等于购置金额；计算天数超过使用年限时应计折旧以「购置金额 − 残值」封顶。`useful-life-years` 必须是正整数；`salvage-rate` 必须是 0 到 1 之间（含端点）、恰好两位小数的十进制数；`as-of` 必须是合法 `YYYY-MM-DD`；资产编号必须已登记。任一校验失败时退出码为 1，错误写 stderr、stdout 为空，台账记录保持不变。

成功时退出码为 0，stdout 输出一行 JSON：

```json
{"asset_id": "A001", "as_of": "2027-09-25", "purchase_amount": 8999.00, "salvage_value": 899.90, "accumulated_depreciation": 1619.82, "book_value": 7379.18}
```

`depreciate` 是只读查询，同一资产可用不同 `as-of` 反复查询，不会修改台账中的任何记录。

数据持久化在仓库根目录的 `asset_ledger.db`（SQLite），文件不存在时自动创建。尚未实现维保计划与部件更换记录。
