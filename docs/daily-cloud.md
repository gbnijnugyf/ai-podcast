# 每日云端播客

本分支提供完整流程的脚本和[云端任务提示词](../cloud/daily-task.md)：
浏览器选题 → DeepSeek 评分 → 指定 ChatGPT 会话总结 → 单主题生成 →
TTS 重试 → 视频校验 → 私有 GitHub Release。

**仓库中的文件不会自行创建或激活 Codex 云端计划。** 当前开发会话未提供
创建云端定时任务或控制云端浏览器的工具，故尚未在用户账号启用定时运行，
也没有验证该账号能在同一云端任务使用“仓库终端 + 登录浏览器”。
这两项是上线条件，不能用本机测试替代。

## 云端准备

在连接 `gbnijnugyf/ai-podcast` 的云端环境使用本分支，安装命令：

```sh
bash cloud/setup.sh
```

需要 Python 3.11+、FFmpeg 和 Noto CJK 中文字体；没有 Blender/GPU 依赖。
安装脚本针对 Ubuntu/Debian，安装系统包需要已有权限。
Linux 参数在 `cloud/config.template.yaml`，凭据在运行时注入 ignored 的 output 目录。
不上传你本机的 config.yaml，不依赖 ai-daily 的 Windows 目录。

| 配置 | 用途 | 建议方式 |
| --- | --- | --- |
| `PODCAST_GITHUB_TOKEN` | 产物仓库 Contents 读写 | Network secret，域名 api.github.com、uploads.github.com |
| `DEEPSEEK_API_KEY` | 财经评分及现有脚本的口播文稿生成 | Network secret，域名 api.deepseek.com |
| `DEEPSEEK_MODEL` | 可选，默认沿用 deepseek-chat | 普通环境变量；如账号不支持此别名，填写账号可用模型名 |
| `PEXELS_API_KEY` | 可选，下载背景图片 | Network secret，域名 api.pexels.com |
| `PIXABAY_API_KEY` | 可选，下载背景图片 | Network secret，域名 pixabay.com |
| `PODCAST_FONT` | 可选，覆盖 Linux 中文字体路径 | 普通环境变量 |
| `PODCAST_FFMPEG` / `PODCAST_FFPROBE` | 可选，可执行文件路径 | 普通环境变量 |

未提供图片服务密钥时，已有背景搜索模块使用仓库自带的兜底图片。
DeepSeek 不用于替代指定 ChatGPT 会话的单句总结。

网络还需要允许 36kr/ChatGPT 浏览器访问、`cn.bing.com`（现有文稿搜索）、
GitHub Release 下载域名、Edge TTS 使用的 `speech.platform.bing.com`，
以及启用图片服务后实际返回的图片 CDN。Edge TTS 使用 WebSocket；
域名白名单本身不证明云端代理支持其连接，必须实测。
安装期间还需软件源访问。不要关闭环境代理来绕过限制。
GitHub 权限详情见 [github-output.md](github-output.md)。

## 启用每天 18:00

1. 发布/更新云端环境，在新云端任务检出 `codex/github-output-storage`。
2. 按 [daily-task.md](../cloud/daily-task.md) 手动执行一次。
   在云端浏览器安全登录界面登录 ChatGPT，并验证指定会话可打开和正常回复。
   本机浏览器登录不会自动迁移到云端；不要把 Cookie 或密码贴进聊天。
3. 在 Scheduled 创建每日任务，把上述完整提示词作为任务说明，
   设为每天 18:00，时区 `Asia/Shanghai`，选择经过验收的云端执行环境。
   若界面仅有本地目录/worktree，或云端缺少浏览器/仓库执行能力，先停止上线并确认账号支持。
4. 手动触发一次计划，核对 Release 中的 video.mp4 可以播放，再观察首个正常定时运行。
   这是平台计划时间，不是严格准点 SLA；调度延迟不能靠脚本消除。

官方区分了本地项目定时任务与网页后台任务；网页任务也不保留本地工作目录。
这里把必要状态保存到 GitHub，而非依赖跨天保留某个云端目录。
参考：[定时任务](https://learn.chatgpt.com/docs/automations)、
[云端环境](https://learn.chatgpt.com/docs/environments/cloud-environments)、
[浏览器登录与限制](https://learn.chatgpt.com/docs/browser)。

## 运行和续跑

每次固定使用一个北京时间日期，命令的 `--date` 必须放在子命令之前：

```sh
python scripts/daily_podcast.py --date 2026-10-20 prepare
python scripts/daily_podcast.py --date 2026-10-20 select --candidates output/daily/2026-10-20/candidates.json
python scripts/daily_podcast.py --date 2026-10-20 accept-summary --file output/daily/2026-10-20/chat-summary.json
python scripts/daily_podcast.py --date 2026-10-20 run
```

浏览器采集与会话交互由执行任务的 Codex 完成，Python 工具不自行登录 ChatGPT。
`candidates.json` 必须来自原文，`chat-summary.json` 必须来自指定会话的新回复。
脚本验证链接、日期范围、格式和来源字段；字段验证不能证明浏览器确实执行过操作，
也不能机械验证四个事实维度，所以任务指令要求核对实际页面和回复。
DeepSeek 热度/争议分是估计值，不等于平台实际阅读量。
缺乏真实候选时扩大近 3 天搜索；全站不可用时报告失败，不制造兜底。

TTS 首次失败后最多再试 6 次，等待 1、2、4、8、15、15 分钟。
每次子进程上限 60 分钟，超时会终止所属进程树；只有 TTS 阶段才进入重试。
云端平台仍可能提前终止任务，所以这不保证每次都能用完预算。
非 TTS 失败、凭据错误、选题或总结失败会停止并报告。

失败前将文稿、背景图、已完成页的音频与缓存作为独立附件保存到草稿 Release；
检查点清单最后上传，含每个文件的 SHA-256。恢复时检查白名单路径及校验和，
重新生成本机路径和配置。没有上传 config、Cookie、原始日志或整个工作目录。
远端存储本身故障时可能无法保存最新进度，不能声称已保存。
强制终止发生在首次检查点之前时，尚无持久化资料。

跨云端任务续跑：同一分支、配置相同凭据，执行原日期的 `run` 即可恢复 TTS 检查点。
若保存的是素材/合成等非 TTS 阶段，需要检查日志和修复具体问题后再决定恢复方式。
本地日志在 `output/daily/日期/`，不会自动上传。

每天独立 Release tag：`podcast-YYYY-MM-DD-daily`。
GitHub 产物仓库的 `locks/日期.json` 是短期互斥锁，只含任务标识和时间；
它作为少量文本进入产物仓库 Git 历史，视频和检查点均在 Releases。
正常结束会删除锁文件；硬终止后可能留下锁，**先确认旧任务已停止**，
再在产物仓库删除该日期锁文件并重新执行。程序不会按时间强行抢锁。

视频需有音频流、视频流、有效时长且完整解码通过，随后才发布 Release。
上传最多尝试 3 次；同名同内容重复校验、不覆盖不同内容。
再次运行时若远端已有 video.mp4，先下载复核并补齐发布步骤，避免重做 TTS；
已发布且有完成清单的日期直接返回结果。
上传失败后保留本地成品；成品尚未上传且环境被销毁时，只能恢复先前检查点。

## 验证范围

```sh
python -m unittest discover -s tests -p 'test_daily_*.py' -v
python -m unittest discover -s tests -p 'test_batch_isolation.py' -v
python -m unittest discover -s tests -p 'test_github_output.py' -v
```

这些测试验证控制流、筛选、恢复和数据隔离，不调用付费模型或真实 TTS。
实际 ChatGPT 网页登录、Edge TTS 网络和云端计划必须按上述步骤单独验收。
