# 播客产物仓库

目标：`gbnijnugyf/ai-podcast-output`，必须为私有仓库。
代码保留在 `gbnijnugyf/ai-podcast`；视频作为目标仓库的 Release 附件，
不进入任何仓库的 Git 历史。本配置不启用定时任务或自动生成。

## 云端凭据与环境

### 1. 创建专用 GitHub 令牌

在 GitHub 的 Settings → Developer settings → Personal access tokens →
Fine-grained tokens 中创建令牌：

| 字段 | 值 |
| --- | --- |
| Token name | `codex-ai-podcast-output` |
| Resource owner | `gbnijnugyf` |
| Repository access | Only select repositories → `ai-podcast-output` |
| Repository permissions | Contents: Read and write；Metadata 保持自动附带的读取权限 |
| Expiration | 按需设置，例如 90 天，到期前更换 |

保存令牌时直接填入下一步的凭据界面，不写入代码、命令行、配置文件或聊天。
这里只需要已有产物仓库的写入权限，不需要创建仓库或 Actions 权限。
源代码仓库的读取授权由 Codex 的 GitHub 连接独立提供。
参考：[GitHub 令牌管理](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)、
[Release 附件上传权限](https://docs.github.com/en/rest/releases/assets#upload-a-release-asset)。

### 2. 配置 Codex 云端环境

打开 Settings → Codex Cloud → Environments，新建或编辑环境，连接
`gbnijnugyf/ai-podcast`，建议保持 Only me。在设置中添加 Network secret：

| 字段 | 值 |
| --- | --- |
| Key | `PODCAST_GITHUB_TOKEN` |
| Value | 上一步生成的 GitHub 令牌 |
| Allowed domains | `api.github.com`、`uploads.github.com` |

同时启用联网并核对允许访问上述域名；如需下载附件复核，另允许
`release-assets.githubusercontent.com`，实际重定向到其他 GitHub 附件域名时再补充。
下载域名无需加入令牌的 Allowed domains：脚本跨域重定向时会去掉 Authorization。
保留云端环境原有的 HTTP/HTTPS 代理设置。

Network secret 通过 HTTPS 代理替换占位值，本工具支持这种凭据传递方式。
不要再创建同名的普通环境变量。如果改用 Personal vault，需让环境请求同名 Key，
并将其应用到本环境，另外配置网络域名。保存后 Publish/Republish，再启动新任务。
界面及机制依据：[Codex 云端环境官方说明](https://learn.chatgpt.com/docs/environments/cloud-environments)。

上传工具只依赖 Python 3.11+ 标准库，不需要 Conda、FFmpeg 或 Blender。
这只代表存储接入的依赖，后续生成视频仍需安装项目自身依赖。
本次代码位于 `codex/github-output-storage` 分支，云端任务必须检出该分支。
在云端项目目录执行 `git branch --show-current`，并确认存在
`github-output.json` 和 `scripts/github_output.py`，再进行验收。
若云端只拉取了默认分支，可在新的干净任务中执行：

```sh
git fetch origin codex/github-output-storage
git switch --track origin/codex/github-output-storage
```

若本地同名分支已经存在，使用 `git switch codex/github-output-storage`。
不要覆盖已有任务中的未提交改动。

### 3. 在新云端任务中验收

在源代码仓库根目录逐条运行，前一条成功后再执行下一条：

```sh
python --version
python -m unittest discover -s tests -p test_github_output.py -v
python scripts/github_output.py --cloud status
python scripts/github_output.py --cloud smoke-test --tag podcast-2026-10-20-cloud-check
```

`--cloud` 只接受配置指定的 `PODCAST_GITHUB_TOKEN`，缺失就报错，
不会尝试其他 `GITHUB_TOKEN` 或本机 Git 登录。不要打印环境变量来排查凭据。

`status` 应返回 `"private": true`；它只证明读取权限。
`smoke-test` 只上传工具生成的无敏感内容文本，保持 Release 为草稿，
连续执行两次上传并校验 SHA-256，成功返回 `"repeat_verified": true`。
同一 tag 可以再次验证，不会重复上传同名附件；请为检查保留专用 tag。
这一步才验证写权限，不生成视频，也不消耗 TTS 调用。

常见故障：缺少 Key 表示任务未获得凭据；HTTP 401 通常是令牌无效或过期；
HTTP 403 需检查 Contents 写权限、网络限制及 GitHub 限流；
HTTP 404 需检查仓库名和令牌选中的仓库。
修正环境配置后启动新任务验证，不把本机验证结果视为云端已通过。

## 本机初始化与检查

在项目根目录、当前 Python 环境中执行，脚本仅依赖 Python 3.11+ 标准库：

```text
python scripts/github_output.py init
python scripts/github_output.py status
```

初始化会验证账号、创建私有仓库并写入 README 和 .gitignore。
若目标已经存在，检查其隐私状态并保留不同的已有文件，不强行覆盖。
目标产物仓库已经创建，云端无需再执行 `init`。
本机未设置令牌时，可以复用 Git Credential Manager 的 GitHub 登录。

## 上传成品

以下为示例，实际文件和作业编号由生成流程提供：

```text
python scripts/github_output.py --cloud upload --tag podcast-2026-10-20-001 --file output/video/example.mp4 --file output/summary.txt
```

默认创建草稿，上传后校验大小和 SHA-256。重复执行时，同名且相同内容的文件会
直接复核；同名不同内容会报错，不删除旧文件。
先通过 FFprobe/解码和音画验收，再在相同命令后加 `--publish` 发布。
私有仓库的 Release 链接需要 GitHub 登录权限，不能作为匿名公开下载地址。

请只明确传入经过审查的产物文件，脚本不自动扫描 output 目录。
文件名过滤只是防误操作，不能替代对 summary/manifest/log 内容的脱敏。
每个文件必须非空且小于 2 GiB。
上传中断后，重新执行相同命令会验证已经成功的附件并继续上传缺失文件；
若 GitHub 留有不完整的同名附件，脚本会报错，需人工检查，不自动删除。
上传工具不会自动重试 TTS，也不会验证视频是否能够正常播放。

## 续跑资料

通用上传脚本不允许上传压缩包：
现有 `_config.yaml` 可能含 API Key，直接压缩工作目录会泄露凭据。
每日任务已通过 `daily_storage.py` 按白名单上传独立的文稿、音频、背景素材与状态附件，
恢复时重建路径并重新注入密钥，详见 [daily-cloud.md](daily-cloud.md)。
浏览器登录资料始终不放入普通产物附件。

没有启用自动删除；保留期限和清理将在完整流水线验收时配置。
云端真实凭据和环境设置通过 Codex 界面配置，不保存在本仓库。
每日任务工具和云端提示词见 [daily-cloud.md](daily-cloud.md)；
实际云端计划创建、浏览器登录与全流程验收仍需在云端完成。
