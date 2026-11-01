# 检查项目录

标题里的检查项名与 `scripts/scan.py` 输出的 `check` 一一对应；阈值以脚本顶部的 `THRESHOLDS` 为准。最后一节是脚本做不了、需要人来判断的项。

## A 磁盘

### orphan_project

`~/.claude/projects/<目录>` 里记录的项目路径已经不存在（项目或 worktree 被删了），会话记录却还留着。

- **处置**：trash 整个目录。
- **风险**：低。只是历史记录，需要时能从废纸篓找回。
- **注意**：目录里有 `memory/` 时，先列出 memory 文件名。如果项目只是换了路径，先把 `memory/` 挪到新路径对应的目录，再 trash。

### large_project

某个项目的会话记录超过阈值，而且项目还在。

- **处置**：不整目录删，二选一：
  1. 设置建议：调短 `cleanupPeriodDays`，交给 Claude Code 按最后活动时间自动清理。
  2. trash 早于统计窗口的单个会话：`<id>.jsonl` 和同名的 `<id>/` 目录（子 agent 记录、工具结果）。24 小时内有写入的会话除外。
- **风险**：中。删掉的会话无法再 `claude --resume`。

### cleanup_period

会话记录总量超过阈值时，报告 `cleanupPeriodDays` 的当前值（默认 30）。

- **处置**：设置建议，在 `~/.claude/settings.json` 写入 `"cleanupPeriodDays": <天数>`，例如 14。
- **风险**：中。超过天数的会话会被自动删除。
- **注意**：只建议一个正整数，不建议设成 0。

### cache_dir

`~/.claude` 下的缓存类目录超过阈值。

| 目录 | 作用 | trash 的风险 |
|---|---|---|
| `file-history` | 回退文件改动用的检查点 | 中：旧会话无法再回退文件 |
| `shell-snapshots`、`telemetry`、`debug`、`paste-cache`、`todos`、`cache` | 运行时缓存、日志 | 低 |
| `plugins` | 已装插件和市场缓存 | 不直接删，用 `/plugin` 管理 |

### old_plans

`~/.claude/plans/` 里超过统计窗口没改过的计划文件。

- **处置**：先列出文件名，用户确认不再需要后 trash。
- **风险**：低。

### codex_sessions

`~/.codex/sessions` 超过阈值。这些记录属于 Codex，Claude Code 的自动清理管不到。

- **处置**：按日期目录 trash 早于统计窗口的部分。
- **风险**：中。删掉的会话在 Codex 里无法续接。

## B 常驻上下文

每个已安装 skill 的 description 都会进入每一次会话。收益统一按「能省下的 description 字数」或「常驻文件行数」计算。

### skill_budget

所有 skill 的 description 总字数超过阈值。这是汇总指标，具体处置来自本组其他项。体检单里写出：都做完之后总字数能降到多少。

### long_description

单个 skill 的 description 超过阈值。

- **自有 skill（my-skills）**：仓库编辑，精简 description。只保留做什么、什么时候用、什么时候不用、触发词，实现细节挪进正文。
- **第三方 skill**：只报告。确实用不上的，按 unused_skill 处理。

### redirect_stub

description 很短，只是把请求转交给另一个 skill，例如 `lark-minutes → lark-meeting`。

- **处置**：管理器命令停用，例如 `npx skills remove --global <name>`。
- **风险**：低。被转交的那个 skill 本身就覆盖了这些请求。
- **注意**：停用前确认转交目标仍然装着。

### broken_skill_link

`~/.claude/skills` 里的软链指向不存在的目录。

- **处置**：先用原来的管理器修复或卸载；管理器里已经没有这一项时，trash 掉这个链接。
- **风险**：低。

### unused_skill

统计窗口内没有调用记录的 skill。这只是参考信号：调用次数是下限，模型直接读 SKILL.md、Codex 里的使用都不计。

- **判断**：
  - 安装不到一周的不算。
  - 按套件看收益最大，例如从来不用钉钉，整套 `dingtalk-*` 一起停用。
  - 用得少但需要时必须在的（如偶尔才做的视频、逆向），问用户再定。
- **处置**：管理器命令停用；用户以后需要时，可以从同一来源重新安装。
- **风险**：中。停用后，相关请求不会再自动触发这个 skill。

### duplicate_name

同名 skill 同时装在全局和项目里，项目级的会覆盖全局的。

- **处置**：确认要保留哪一个，用管理器停用另一个。

### unused_mcp

配置了、但统计窗口内没有调用的 MCP server。MCP 工具通常是按需加载的，常驻成本主要是 server 自带的说明文字，有的会很长。

- **处置**：管理器命令 `claude mcp remove <name>`（用户级的加 `-s user`）。
- **风险**：中。

### always_loaded

CLAUDE.md、rules、AGENTS.md 等常驻文件超过阈值。带 `paths:` 的 rules 按路径条件加载，不算。

- **处置**：
  - 只在特定任务里用到的内容，挪进对应 skill 的 references，变成按需加载；
  - 删掉和某个 skill 重复的内容；
  - 删掉 agent 读代码就能推出来的内容。
- **风险**：低到中。
- **注意**：仓库外的文件改之前先备份，并逐条列出删掉了什么。

### memory_index

memory 目录的索引和文件对不上，或 MEMORY.md 过长。

- **处置**：
  - 没进索引的文件，补一行指针；
  - 指向不存在文件的行，删掉；
  - MEMORY.md 过长时，合并同主题的条目。
- **风险**：低。改之前给出 diff。

## C 仓库结构

都属于仓库编辑。git 可以回滚，风险低。动手前先确认工作区干净。

### skill_md_long

SKILL.md 超过阈值。把细节、模板、案例下放到 references，SKILL.md 只留主流程，并用相对链接指过去。

### frontmatter

frontmatter 缺失、有 name 和 description 以外的字段，或者 name 与目录名不一致。改到只剩 name 和 description，并让 name 等于目录名。

### broken_link

相对链接打不开。修正路径；目标确实不存在时删掉这个链接。

### orphan_reference

`references/` 里的文件没有被任何文件提到。

- 有用的：在 SKILL.md 里加一行链接，并说明什么时候读。
- 没用的：trash，并逐条列出里面的知识点。

### missing_openai_yaml

缺 `agents/openai.yaml`。照仓库里已有 skill 的格式补上。

### pending_rules

eat 写进 `rules/` 的规则不会自动加载，要等用户审阅后自己迁入 CLAUDE.md。待审阅的规则数超过阈值时，提醒用户审阅，并按文件列出条数。

审阅时逐条给出建议：

- **迁入 CLAUDE.md**：每次都必须生效的规则。由用户来迁。
- **并入某个 skill**：只在某类任务里用得上的规则。
- **删掉**：模型本来就会的、已经过时的、和别的规则重复的。

已经迁走或删掉的条目，从 `rules/` 里去掉，避免下次重复审阅。

### missing_provenance

`rules/` 里有规则没写出处，看不出它从哪来、该不该留。

- **处置**：用 `git log -S '<规则片段>' -- rules/` 找到引入它的提交，按提交说明补上出处；找不到的，标「出处不明」，交给用户决定去留。

### uncommitted

仓库有未提交的改动。先请用户提交或暂存，再做仓库编辑，避免把两类改动混在一起。

### tracked_junk

git 跟踪了 `.DS_Store`、`__pycache__` 之类的文件。在 `.gitignore` 里加上规则，再执行 `git rm --cached <路径>`。

## 人工判断项

脚本判断不了，需要读内容来定。

| 检查 | 怎么查 | 处置 |
|---|---|---|
| skill 功能重叠 | 对比 description：讲的是同一件事，或者一个是另一个的子集 | 保留覆盖面更好的那个，另一个停用；自有 skill 之间的重叠可以合并 |
| 常驻文件与 skill 重复 | CLAUDE.md、`~/.claude/rules` 里的条目，是否已经写在某个 skill 里 | 留在更窄的地方（skill），从常驻文件里删掉 |
| 仓库 `rules/` 与 CLAUDE.md 重复 | `rules/` 里的条目已经出现在 CLAUDE.md 里，多半是用户迁过去了 | 问用户后，从 `rules/` 删掉 |
| 能从代码推出来的规则 | 目录结构、技术栈、lint 已经覆盖的风格 | 删掉 |
| references 缺出处 | `references/` 里吸收来的条目没有「出处：」（`rules/` 由脚本检查） | 同 missing_provenance |
