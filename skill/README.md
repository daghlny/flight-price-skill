# flight-price skill

把 `flight-price` 这个 CLI 封装成 AI Agent 可调用的能力。核心是同目录下的 [`SKILL.md`](./SKILL.md)，里面写了 Agent 应该在什么场景调用、参数怎么传、JSON 输出怎么读、容易踩什么坑。

下面说明如何安装到各家主流 Agent。

## 前置条件

`flight-price` CLI 本身必须已经装好。安装见仓库根目录 README：

```bash
curl -fsSL https://raw.githubusercontent.com/daghlny/flight-price-skill/main/install.sh | bash
```

验证：

```bash
flight-price --version
# flight-price 0.4.0
```

## Claude Code（Anthropic）

Claude Code 原生支持 Skill。把这个文件夹拷到全局或项目 skill 目录即可：

**全局安装（所有项目都能用）：**

```bash
mkdir -p ~/.claude/skills
cp -r skill ~/.claude/skills/flight-price
```

**项目级安装（只在当前 repo 里用）：**

```bash
mkdir -p .claude/skills
cp -r skill .claude/skills/flight-price
```

装好后重启 Claude Code 会话，描述中包含触发短语（"找便宜机票"、"比较机票价格"、"端午怎么去"等）时 Claude 会自动加载该 skill。

## Codex CLI（OpenAI）

Codex CLI 没有独立的 skill 概念，但读取项目根 `AGENTS.md`。建议做法是**把 SKILL.md 的内容追加到** `AGENTS.md`：

```bash
# 在你想让 Codex 使用 flight-price 的项目根目录下
cat path/to/flight-price-skill/skill/SKILL.md >> AGENTS.md
```

或者直接 symlink：

```bash
ln -s path/to/flight-price-skill/skill/SKILL.md ./AGENTS.md
```

（注意 symlink 方式会替换已有的 AGENTS.md，慎用）

## Cursor / Continue / 其他读 markdown 的 Agent

绝大多数 Agent 工具支持"项目级指令文件"（`.cursorrules`、`continue.config.json` 里的 `systemMessage` 等）。把 SKILL.md 的内容粘贴进去即可，frontmatter 部分可以保留也可以删除（不影响理解）。

## 通用做法（任何支持 system prompt 的 Agent）

把 SKILL.md 的**正文部分**（frontmatter 下方的所有内容）粘贴到 Agent 的 system prompt 末尾。这是最低公分母方案，适用于任何支持自定义指令的 Agent 平台。

## 更新

CLI 升级后（`flight-price --version` 变化），重新拷贝一次 SKILL.md 即可——它记录了 CLI 的当前版本和 schema。
