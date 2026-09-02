# Git 常用命令备忘

> 个人备忘，按需增删。

## 推送（需代理时）

```bash
cd secrets-repo && git -c http.proxy=http://127.0.0.1:7897 push origin master 2>&1
```

**逐段解释**：

| 片段 | 含义 |
|---|---|
| `cd secrets-repo` | 切换到 `secrets-repo` 目录 |
| `git` | 调用 git 客户端 |
| `-c http.proxy=http://127.0.0.1:7897` | 临时设置本次命令的 HTTP 代理，不写入 `~/.gitconfig` |
| `push origin master` | 把本地 `master` 分支推送到名为 `origin` 的远程仓库 |
| `2>&1` | 把标准错误合并到标准输出，错误和正常信息一起显示 |

> **为什么需要代理**：GitHub 在本机直连被拒（`127.0.0.1:443 connection refused`），所以走本机 7897 端口的代理（Clash/V2RayN 等）。
>
> **更省事的写法**：直接 `git push`（首次 `git push -u origin master` 已设置上游追踪，之后 push 默认就是 `origin master`）。

## 全局代理开关

```bash
# 开启全局代理
git config --global http.proxy http://127.0.0.1:7897
git config --global https.proxy http://127.0.0.1:7897

# 关闭全局代理
git config --global --unset http.proxy
git config --global --unset https.proxy

# 查看当前代理
git config --global --list | grep proxy
```

写入位置：`C:\Users\Administrator\.gitconfig`

## 撤销 push（仅未推送前）

```bash
git reset --soft HEAD^   # 撤销最近一次 commit，保留改动
git reset --hard HEAD^   # 撤销最近一次 commit，丢弃改动（慎用）
```
