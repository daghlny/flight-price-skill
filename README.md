# flight-price-skill

给 **Claude Code / Codex / Cursor** 等 AI Agent 装上"查真实机票价"的能力。

装好之后，你直接用自然语言跟 Agent 说：

- "帮我查 6/19 端午请 1 天假去杭州，怎么飞最便宜？"
- "比较一下 6 月每个周末从北京飞东京的往返价"
- "下周三晚上 6 点之后有什么国航的直飞北京-上海航班？"

Agent 会自动调用本 skill，**返回真实可订航班**（不是普通比价站那种"营销 from 价"），并按你的意图筛选/排序候选给出建议。

> 项目地址：<https://github.com/daghlny/flight-price-skill>

## 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/daghlny/flight-price-skill/main/install.sh | bash
```

脚本会：

1. 装好底层 CLI（Python + Playwright + Chromium）
2. **自动检测**是否装了 Claude Code（`~/.claude/`），有的话顺便把 skill 装到 `~/.claude/skills/flight-price/`
3. 把 `flight-price` 命令放到 `~/.local/bin/`（CLI 也可以独立用）

装完重启 Claude Code 即可生效，验证：

```bash
flight-price --version
# flight-price 0.4.0
```

> Codex / Cursor / 其他 Agent 的装法见 [`skill/README.md`](./skill/README.md)。

## Skill 的能力

| 维度 | 支持 |
|---|---|
| **数据源** | Trip.com 的 `FlightListSearch` 接口——每个价格对应具体可订航班，非营销价 |
| **航程类型** | 单程 / 往返 |
| **日期模式** | 区间扫描、固定回程扫出发、固定 stay 扫日期、**多组合一次扫**（`--pairs`） |
| **舱位 / 乘客 / 货币** | `--cabin economy\|premium\|business\|first`、`--adults N`、`--currency USD/JPY/HKD/CNY/...` |
| **过滤** | 航司白/黑名单、最大中转数、仅直飞、出发时间窗 |
| **排序** | 价格 / 总时长 / 出发时间 |
| **回程数据** | 含归途航班号 + 起飞时间（去程含完整段落详情：机场、航站楼、duration、layover） |
| **输出** | 结构化 JSON（含跨所有查询排好序的扁平 `flights[]`、独立 `status` 信号便于 Agent 重试决策） |
| **缓存** | `--cache` 可选开启（默认关），Agent 迭代调参时秒回，详见 `flight-price man` |
| **自检** | `flight-price doctor` 一条命令排查环境/网络/接口问题 |

## Agent 怎么用（实际对话示例）

**场景 1：节假日规划**

> 你：帮我查 2026 端午请 1 天假去杭州，怎么飞最便宜？最少待 2 晚。

Agent 会自己枚举出 5 种合法的（出发, 回程）组合，一次调用 skill 拿到全部价格，再综合"性价比/玩的天数/请假代价"给推荐——而不是给你一堆原始数据。

**场景 2：定制化筛选**

> 你：下周从北京去上海，要 18 点之后出发的直飞，最好是国航或东航。

Agent 自动转成 `--depart-after 18:00 --max-stops 0 --airline CA,MU`，返回筛好的候选。

**场景 3：多目的地比价**

> 你：周末从北京出发，HGH、NGB、XMN 哪个最便宜？

Agent 并行扫多个目的地，给出对比表。

## 也可以当作 CLI 直接使用

如果你只想自己敲命令：

```bash
# 国内单程：北京 -> 上海，6/1~6/7 区间最便宜
flight-price BJS SHA --from 2026-06-01 --to 2026-06-07

# 往返：北京 -> 东京，6/6 出发待 2 晚，每天列 5 个候选
flight-price BJS TYO --from 2026-06-06 --to 2026-06-06 --rt --stay 2 --limit 5

# 过滤航司 + 时间窗
flight-price BJS SHA --depart-after 18:00 --airline MU,CA --limit 5

# 多组合一次扫（端午 5 种候选）
flight-price BJS HGH --pairs \
  2026-06-18:2026-06-20,2026-06-18:2026-06-21,\
  2026-06-19:2026-06-21,2026-06-19:2026-06-22,\
  2026-06-20:2026-06-22

# JSON 输出（默认 limit=5，给脚本消费）
flight-price BJS TYO --rt --stay 2 --from 2026-06-06 --to 2026-06-06 --json
```

完整手册：`flight-price man`

## 工作原理

- 用 Playwright 跑 headless Chromium 访问 `tw.trip.com/chinaflights/showfarefirst`，监听其内部 `FlightListSearch` / `FlightListSearchSSE` 接口拿到真实运价
- 多日期 / 多组合并发跑（默认 3 路并行，可调），每条查询 ~10 秒
- 价格用 `curr=CNY` 参数让 Trip.com 直接返回 CNY，零汇率换算误差
- 往返模式下，归途航班号和起飞时间从响应的 `shortPolicyId` 解码——零额外请求
- Skill 本身就是一个 markdown 文件（[`skill/SKILL.md`](./skill/SKILL.md)），里面告诉 Agent 什么时候调用、参数怎么传、JSON 怎么读、容易踩什么坑

## 已知限制

- 仅支持 **1 成人 + 经济舱**（参数化在 roadmap 里）
- 不支持联程 / 多城
- 归途只有航班号 + 起飞时间，**过境机场和到达时间需要二次 `FlightDetail` 请求**才能拿到（目前未实现）
- 货币当前固定 CNY，不支持 `--currency` 切换
- 无本地缓存，重跑相同查询会重新打 Trip.com

## License

MIT。Trip.com 数据归 Trip.com 所有；本工具仅做合理的浏览器模拟访问，请自行确认你所在司法辖区的使用合规性。

## 反馈 / Issues

<https://github.com/daghlny/flight-price-skill/issues>
