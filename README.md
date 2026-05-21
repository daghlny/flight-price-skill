# flight-price

一个查询 **Trip.com** 真实可订机票价格的命令行工具，专为 **AI Agent** 设计。

不像普通比价站给你看的是"营销 from 价"，本工具背后调用的是 Trip.com 的 `FlightListSearch` 接口——每一个返回的价格都对应一个具体、当下可订的航班。

> 项目地址：<https://github.com/daghlny/flight-price-skill>

## 能力一览

- ✈️ **单程 / 往返** 任意日期区间扫描
- 📅 **`--pairs` 一次跑多组日期组合**——AI Agent 自己算好"端午请1天假怎么飞最便宜"的所有候选，一次调用搞定
- 🎯 **过滤器**：航司白名单 / 黑名单、最大中转数、出发时间窗、仅直飞
- 📊 **排序**：按价格 / 总时长 / 出发时间
- 🔄 **回程信息**：往返模式可解码出归途的航班号 + 起飞时间（去程是完整段落详情）
- 🤖 **JSON 输出**：含跨所有查询的扁平 `flights[]` 排好序数组，Agent 直接取 `[0]` 就是全场最佳
- 🚥 **每条查询独立 `status` 信号**：`ok` / `no_results` / `timeout`，Agent 知道要不要重试
- 📖 内置 `help` / `man` / `--version`

## 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/daghlny/flight-price-skill/main/install.sh | bash
```

脚本会做：

1. 检查 `python3 >= 3.10` 和 `git`
2. 克隆仓库到 `~/.flight-price-skill`
3. 自动建 venv 装依赖（playwright）
4. 下载 Chromium（一次性，~150MB）
5. 把 `flight-price` 命令放到 `~/.local/bin/`

装完验证：

```bash
flight-price --version
# flight-price 0.4.0
```

如果 `~/.local/bin` 不在你的 `PATH`，脚本会提示你加上。

> 升级：再跑一次同一行命令即可，幂等。

## 快速上手

```bash
# 国内单程：北京 -> 上海，6/1~6/7 区间最便宜
flight-price BJS SHA --from 2026-06-01 --to 2026-06-07

# 往返：北京 -> 东京，6/6 出发待 2 晚
flight-price BJS TYO --from 2026-06-06 --to 2026-06-06 --rt --stay 2

# 只看每天最便宜的直飞，按出发时间排
flight-price BJS SHA --direct --sort depart --limit 3

# 过滤航司 + 时间窗口
flight-price BJS SHA --depart-after 18:00 --airline MU,CA --limit 5

# 多组日期组合一次扫（端午 5 种候选）
flight-price BJS HGH --pairs \
  2026-06-18:2026-06-20,2026-06-18:2026-06-21,\
  2026-06-19:2026-06-21,2026-06-19:2026-06-22,\
  2026-06-20:2026-06-22

# JSON 输出（给 AI Agent / 脚本消费）
flight-price BJS TYO --rt --stay 2 --from 2026-06-06 --to 2026-06-06 --json
```

完整手册：`flight-price man`

## 作为 AI Agent Skill 使用

仓库自带 `skill/` 目录，里面是给 Agent 用的指令包，已经适配主流软件。

**Claude Code：**

```bash
mkdir -p ~/.claude/skills
cp -r ~/.flight-price-skill/skill ~/.claude/skills/flight-price
```

重启 Claude Code 后，描述里包含"找便宜机票"、"比较机票价格"、"端午怎么去"等触发短语时，Claude 会自动加载这个 skill 并按其指导调用 CLI。

**Codex CLI（OpenAI）/ Cursor / Continue 等**：详见 [`skill/README.md`](./skill/README.md)。

## 输出示例

表格模式（人看）：

```
BJS → HGH  2026-06-18~2026-06-22  PAIRS (5 RT)  (5 days, 5 options, min=1420 CNY)
date          return           CNY  type    dep    dur     airline  outbound        return-leg
2026-06-18    2026-06-20      1480  direct  11:55  2h20m   HU       HU7477          JD5907@21:45
2026-06-18    2026-06-21      1530  direct  11:55  2h20m   HU       HU7477          CA1701@07:00
2026-06-19    2026-06-21      1450  direct  21:35  2h35m   CA       CA1732          CA1701@07:00
2026-06-19    2026-06-22      1460  direct  21:35  2h35m   CA       CA1732          CA8367@07:55
2026-06-20    2026-06-22      1420  direct  19:35  2h15m   GJ       GJ8988          CA8367@07:55  *
```

JSON 模式（Agent 用）：每条查询单独 `status`，顶层有跨所有查询排好序的 `flights[]`，详见 `flight-price man` 里 `JSON OUTPUT FORMAT` 章节。

## 工作原理

- 用 Playwright 跑 headless Chromium 访问 `tw.trip.com/chinaflights/showfarefirst`，监听其内部 `FlightListSearch` / `FlightListSearchSSE` 接口拿到真实运价
- 多日期 / 多组合并发跑（默认 3 路并行，可调），每条查询 ~10 秒
- 价格用 `curr=CNY` 参数让 Trip.com 直接返回 CNY，零汇率换算误差
- 往返模式下，归途的航班号和起飞时间从响应里的 `shortPolicyId` 解码——零额外请求

## 已知限制

- 仅支持 **1 成人 + 经济舱**（参数化在 roadmap 里）
- 不支持联程 / 多城
- 归途只有航班号 + 起飞时间，**过境机场和到达时间需要二次 `FlightDetail` 请求**才能拿到（目前未实现）
- 货币当前固定 CNY，不支持 `--currency` 切换
- 无本地缓存，重跑相同查询会重新打 Trip.com

## License

MIT。Trip.com 的数据归 Trip.com 所有；本工具仅做合理的浏览器模拟访问，请自行确认你所在司法辖区的使用合规性。

## 反馈 / Issues

<https://github.com/daghlny/flight-price-skill/issues>
