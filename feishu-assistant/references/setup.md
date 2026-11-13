# 环境、开关、授权和 id

出处：`~/Desktop/my-lark-助手能力交接.md`（2026-11-13 获取）；本机实测 my-lark 0.1.0、lark-cli 1.0.98（2026-11-13）。

## my-lark

CLI 通过 `~/.my-lark/agent.sock` 请已登录的飞书桌面端代为执行，所以动作以本人身份发生，对方看到的就是本人发的消息。这些组件由飞书 → Lark Mods → My Lark 模组安装。

| 组件 | 位置 |
|---|---|
| CLI | `~/.local/bin/my-lark` → `~/.my-lark/bin/my-lark`，包装脚本里写死了 node 路径 |
| skill | `~/.my-lark/skill/`，`~/.claude/skills/my-lark` 是它的软链 |
| socket | `~/.my-lark/agent.sock`，飞书开着时存在 |
| 审计日志 | `~/.my-lark/audit.log`，JSONL，每次调用一行：`at`、`tool`、`access`（read / write）、`ms`、`ok`；不记参数和会话 |
| 上传副本 | `~/.my-lark/uploads/` |
| 附件下载 | `$TMPDIR/my-lark/downloads/`，保留一天，单个上限 25 MiB，最多等 60 秒 |
| 监听用的 MCP | `~/.claude.json` 里的 `my-lark`：`command` 是 `~/.my-lark/bin/my-lark`，`args` 是 `["channel"]` |

自检：

```bash
which my-lark && my-lark --version   # 0.1.0
my-lark whoami                        # 姓名、id；next 行是和自己单聊的 id
ls -la ~/.my-lark/agent.sock
my-lark inbox summary                 # 只读链路通
```

### 开关

在飞书 → Lark Mods → My Lark 里设置。开关状态不落盘，只能从命令报错判断；报错会写明缺哪个开关。

| 开关 | 管哪些命令（`--help` 里的标记） |
|---|---|
| 写 | `[write]`：send、msg react/edit/forward/pin/to-task、card click、vote cast/create、scheduled update、group create/add、`chat dm --create` |
| 整理会话列表 | `[organize]`：feed done/mute/pin/mark-unread、msg save/flag、person alias/focus |
| 管理 | `[admin]`：msg recall、scheduled cancel、group remove/update/leave |
| 注册到 Claude Code | 把上面的 MCP 写进 `~/.claude.json`，监听 session 要用 |
| 签名 | 发出的消息末尾自动带的引用块；agent 不再自己加 |

没有标记的读命令，以及 `[draft]` 标记的 draft、chat open、msg open，始终可用。

### 常见故障

- 没有 `~/.my-lark` 目录：没装或没启用 My Lark，请用户到飞书 Lark Mods 里装上。
- `command not found`：把 `~/.local/bin` 加进 PATH，或直接调 `~/.my-lark/bin/my-lark`。
- 报飞书没在运行：请用户打开飞书，不重试。
- node 升级或换位置后跑不起来：包装脚本里的 node 路径失效，让 My Lark 重新生成 CLI（未验证）。
- `--help` 自己承认不可靠的命令：`inbox flagged` 没观察到过有数据的返回；`person focused` 在开发机上失败过；`group admins` 开发时被拒；`msg open` 不能滚动到那条消息。`im`、`contact` 出现在 `--help --all` 里，但调用会报 unknown command。
- `msg to-task` 在 `--help` 里有，但本机实测返回 `TransportError: command handler not found`（2026-11-13）。从消息建待办改走 lark-cli task。

## lark-cli

- 通用规则先读 `~/.agents/skills/lark-shared/SKILL.md`：`--as user` 选身份；`ok == true` 判成功；文件路径相对当前目录；`high-risk-write` 不加 `--yes` 会以退出码 10 拒绝执行。
- 默认身份是 `auto`，可能解析成 bot。bot 只看得到应用自己的资源，查用户数据会返回空的成功结果，所以一律写 `--as user`。
- 每个命令的 `--help` 末尾写着 `Risk: read`、`write` 或 `high-risk-write`，按 SKILL.md 第 2 节对上确认级别。
- `lark-cli auth status`：`identities.user.openId` 是本人的 `ou_` id；`scope` 是已授权列表；`tokenStatus: needs_refresh` 不用管，下次调用自动刷新；过了 `refreshExpiresAt` 要重新登录。

### 领域和 scope

本机 2026-11-13 的授权情况，以 `auth status` 实际输出为准：

| 领域 | scope 举例 | 本机 |
|---|---|---|
| task | `task:task:read`、`task:task:write` | 已授权 |
| docs、drive、sheets、base | `docx:document:readonly`、`base:record:read` 等 | 已授权 |
| wiki | `wiki:wiki:readonly` | 只读 |
| im 消息 | `im:message`、`im:message.group_msg:get_as_user` | 已授权 |
| minutes | `minutes:minutes.basic:read`、`minutes:minutes.artifacts:read` | 只读 |
| search | `search:docs:read`、`search:message` | 已授权 |
| calendar | `calendar:calendar.event:read` | 未授权 |
| approval | `approval:task:read` | 未授权 |
| contact | `contact:user:search` | 未授权 |
| okr、mail、vc、attendance、im:chat | — | 未授权 |

缺 scope 时的报错（实测原文，节选）：

```json
{"ok": false, "error": {"type": "authorization", "subtype": "missing_scope",
  "message": "missing required scope(s): calendar:calendar.event:read",
  "hint": "run `lark-cli auth login --scope \"calendar:calendar.event:read\" --no-wait --json` to get device_code and verification_url; ...",
  "missing_scopes": ["calendar:calendar.event:read"]}}
```

approval 的报错写法不同：`unauthorized: user authorization does not cover the required scope(s): approval:task:read`，同样带 `missing_scopes`。

### 补授权

按 lark-shared 的分步设备授权走，细节见 `lark-shared/references/lark-shared-identity-and-permissions.md`：

1. 跑报错 `hint` 里给的命令，或按领域一次补齐：`lark-cli auth login --domain calendar,approval,contact --no-wait --json`。拿到 `verification_url` 和 `device_code`。
2. 把 `verification_url` 原样给用户，结束本轮，等用户授权。
3. 用户说授权好了，再跑 `lark-cli auth login --device-code <device_code>`，然后重跑 `auth status`，确认 scope 已经在列表里。

不在同一轮里轮询等待。不替用户换别的 scope 绕过去。

## id 过桥

my-lark 用飞书客户端内部的纯数字 id，lark-cli 用 OpenAPI 的 `oc_`、`om_`、`ou_` id，两边不能混用。实测能用的转换（2026-11-13）：

| 对象 | my-lark → lark-cli | lark-cli → my-lark |
|---|---|---|
| 本人 | `lark-cli auth status` 的 `identities.user.openId` | `my-lark whoami` |
| 会话 | `my-lark chat show <chat> --json` 的 `chat.openId`（`oc_…`） | 用会话名或链接：`my-lark chat show <名字>` |
| 消息 | `lark-cli im +chat-messages-list --chat-id <oc_> --start <createdAt 前 1 分钟> --end <后 1 分钟> --as user`，按 `create_time`、`msg_type`、`sender` 对上 `message_id`（`om_…`） | 按会话、时间、内容在 `my-lark chat read <chat> --ids` 里对 |
| 人员 | 有 contact scope：`lark-cli contact +search-user --query <姓名或邮箱前缀> --as user`，邮箱可从 `my-lark person show <人> --json` 拿。没有时：在双方都在的会话里用上面的消息列表，取对方发言的 `sender.id`（`ou_…`） | `my-lark person find <姓名>` |
| 文档 | `my-lark search <词> --type docs --json` 直接返回 `url`，lark-cli 接受 URL | 同一个 URL |

注意：

- `my-lark msg show --json` 里没有 `om_` id，消息只能按上面的方法对。
- my-lark JSON 里的时间是 UTC（`…Z`），lark-cli 的 `create_time` 是本地时间，只精确到分钟。比对前统一时区；同一分钟有多条时再比内容。
- 人员同名时不猜，列出候选（部门、邮箱）让用户选。
