# AMR 部署（.178 等盒子）

AMR 是零运行时依赖的纯 Python 3.10+ 包，部署 = 同步代码 + 起两个 systemd `--user` 常驻服务。

## 一键部署 / 更新

从开发机（已有目标机 SSH 访问）：

```sh
deploy/deploy.sh dbos-user@192.168.31.178
```

它会 `rsync` 代码到目标机 `~/amr/`，再远程跑 `deploy/install.sh`：
渲染 systemd 单元、生成 web token（首次）、`enable-linger`（开机自启 + 登出存活）、
`enable --now` 两个服务。幂等，可反复跑做升级。

## 服务

| 单元 | 作用 | 关键配置 |
|---|---|---|
| `amr-web.service` | 只读 Web 收件箱 `0.0.0.0:8088` | `EnvironmentFile=~/.config/jl/amr.env`（含 `JL_WEB_TOKEN`，600，不入仓）|
| `amr-poll.service` | 5 分钟增量拉新（`jl poll`）| 读 `~/.config/agent-wechat/token` 调 `localhost:6174` |

`Restart=always` 崩溃自拉起；`enable-linger` 保证 .178 重启后自启。

## 运维速查

```sh
systemctl --user status amr-web amr-poll      # 状态
systemctl --user restart amr-web              # 重启
journalctl --user -u amr-web -n 50 --no-pager # 日志
systemctl --user stop amr-web amr-poll        # 停
cat ~/.config/jl/web_token                    # 看 web token
```

**访问**：`http://<host>:8088/?token=<web_token>`（`/api/*` 需 token；首页不需要；浏览器 UI 会把 `?token=` 转发给 API）。

**轮换 web token**：`python3 -c 'import secrets;print(secrets.token_hex(16))' > ~/.config/jl/web_token && bash ~/amr/deploy/install.sh`。

## 前置

- 目标机 `python3 --version` ≥ 3.10（零 pip）。
- fullwechat 后端在目标机 `localhost:6174`，token 在 `~/.config/agent-wechat/token`（600）。
- 因暴露私聊全文于 LAN，**必须**有 `JL_WEB_TOKEN`（install.sh 自动生成）。
