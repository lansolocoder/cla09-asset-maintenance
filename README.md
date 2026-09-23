# 设备资产与维保台账

用于本地设备资产与维保记录管理的命令行项目。

需要 Python 3.12，无第三方依赖。在仓库根目录运行：

```bash
python3 -m asset_ledger --help
python3 -m asset_ledger --version
python3 -m unittest discover -s tests -v
```

当前仅提供帮助与版本查询入口；无参数显示帮助，未知参数以非零状态退出。尚未实现资产登记、维保计划、部件更换记录以及折旧计算，不会创建业务数据文件。
