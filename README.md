# Monterey

把 Schwab、OKX、富途的持仓和现金拉到一起，按统一币种（默认 HKD）汇总，每次同步存一份快照。全部只读，不会下单或转账。

## 结构

```
portfolio/
  models.py            统一数据结构：Holding（持仓/现金）、ReportedTotal（券商报的账户总值）
  connectors/
    schwab.py          Trader API，OAuth，自动刷新 token
    okx.py             V5 API，只读 key：交易账户、资金账户、简单赚币、合约持仓
    futu.py            OpenAPI，通过本机 OpenD
    hsbc_hk.py         预留：之后用浏览器模拟实现
  fx.py                汇率（open.er-api.com，免费无 key）
  aggregate.py         拉取、换汇、对账、汇总输出
  store.py             SQLite 快照（data/portfolio.db）
  cli.py               命令行
tests/                 用模拟数据测试解析、签名、token 刷新和对账
```

加新来源只需实现 `Connector.fetch() -> FetchResult`，在 `connectors/__init__.py` 注册即可，HSBC 就按这个方式接入。

## 安装

建议用 Python 3.11 或 3.12 的虚拟环境（系统自带 pip 装 futu-api 可能报 `install_layout` 错误，用 venv 就没问题）。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[futu,dev]"
cp .env.example .env    # 填入各家凭证
```

## 各账户准备

**Schwab**
1. 在 developer.schwab.com 注册开发者账号，申请 Trader API – Individual，创建 App，勾选 Accounts and Trading Production，回调地址填 `https://127.0.0.1`。
2. 等 App 状态变成 Ready For Use，把 App Key / Secret 填到 `.env`。
3. 运行 `python -m portfolio schwab-login`，在浏览器登录授权，把跳转后的地址栏 URL 粘回终端。
4. 授权 7 天有效，到期前一天多同步时会提醒，重新跑第 3 步即可。

**OKX**
在 API 管理里新建 V5 API Key，权限只选「读取」，建议绑定 IP 白名单，把 key、secret、passphrase 填到 `.env`。

**富途**
1. 下载并启动 OpenD，用牛牛号登录（首次登录后需要完成 API 问卷和协议）。
2. 保持 OpenD 运行，默认 `127.0.0.1:11111`。工具只查资金和持仓，不需要交易密码。

## 使用

```bash
python -m portfolio sync                 # 拉全部来源，打印汇总并保存快照
python -m portfolio sync --only okx      # 只拉某几个来源
python -m portfolio sync --json          # JSON 输出，方便接其他工具
python -m portfolio history              # 每次快照的总值
pytest                                   # 跑测试
```

某个来源出错不会影响其他来源，会在 Sources 一栏显示 FAIL 和原因。

## 对账提醒

每个账户的持仓加现金，会和券商自己报的总值比较（Schwab liquidationValue、富途 total_assets、OKX 交易账户 totalEq），差超过 1% 会在 Warnings 里提示，用来发现漏算或重复算的资产（例如富途的基金、债券，或 OKX 质押类产品）。

## 已知限制

- OKX：资金账户和赚币余额用现货 `XXX-USDT` 价格估值，USDT 按 1 美元算；链上赚币（staking/DeFi）暂未接入；合约持仓只展示敞口，不计入总额（盈亏已含在交易账户权益里）。
- 富途：只接证券账户，期货账户未接。
- OKX 里没有 USDT 报价的币会留空市值并提示。
- Schwab：期权不算成本价。
- 开发环境无法访问 Schwab、OKX、富途和汇率接口，解析逻辑按官方文档的返回格式用模拟数据测试过，第一次用真实账户跑时请留意 Warnings。
