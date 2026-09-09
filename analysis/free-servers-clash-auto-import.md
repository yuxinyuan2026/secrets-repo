# Pawdroid/Free-servers 能否在节点更新后自动导入 Clash？

> 分析时间：2026-09-09（UTC） · 分析对象：<https://github.com/Pawdroid/Free-servers> · 结论基于**对该仓库的实际克隆/解码** + **Clash/Mihomo 客户端源码核实**

---

## 一、结论（先看这个）

**能，但"自动"分两段，且"直接把 URL 当配置导入"这条路在现代客户端上是走不通的。**

| 问题 | 结论 |
|---|---|
| 订阅内容是不是标准格式？ | ✅ 是。**标准 base64 分享链接订阅**（trojan/vless/ss/vmess/hysteria2），不是 Clash YAML |
| Mihomo（Clash Meta）内核能否直接解析？ | ✅ 能，原生支持，无需转换 |
| 节点更新后能否自动生效？ | ✅ 能，靠 **proxy-provider 的 `interval`** 或客户端的"订阅自动更新"，内核按间隔重新拉取 |
| 能否把 URL 直接当成"配置/订阅"导入客户端？ | ⚠️ **看客户端**。Clash Verge Rev 会直接报错拒绝（源码核实：要求响应体是含 `proxies`/`proxy-providers` 的 YAML） |
| 仓库自身有没有推送到 Clash 的自动化？ | ❌ 没有。仓库 0 个 Actions workflow，是外部 cron 定时 force-push 的纯数据仓 |
| 真机上跑通了吗？ | ✅ **已用真实 Mihomo Meta v1.10.4 内核实测跑通：16/16 个订阅文件全部 20/20 导入成功，节点更新后不重启内核即自动生效**（详见第四节） |

一句话：**把它当作 proxy-provider（代理提供者）挂进去，节点更新后 Clash 会自动拉取；别指望"导入链接"按钮在所有客户端上都成功。**
实测还发现一个必须知道的坑：**订阅里只要有 1 条节点解析失败，整包更新会被拒绝并保留旧列表**（见 4.2）。

---

## 二、这个仓库到底在更新什么（实测）

```
$ gh api repos/Pawdroid/Free-servers
stars: 19001 · default_branch: main · pushed_at: 2026-09-09T02:00:51Z
commits: 1        # 只有 1 个 commit
actions/workflows: total_count = 0     # 没有任何 GitHub Actions
```

- 仓库只有 **1 个 commit**（`BT Cron / Update latest server snapshot`），说明每次更新都是**压平历史后 force push**，历史不可追溯，也**无法从 commit 历史验证"6 小时更新一次"**这个说法（README 自称 6h）。
- 每次更新 HEAD 的 commit SHA 都会变 → **不能用 commit-SHA 形式的 raw 链接当订阅源**（那是 immutable 的，永远拿到旧内容）。
- 真正的数据文件是 16 个 base64 订阅文件，**每个语言版本内容都不同**：

| 文件 | 链接数 |
|---|---|
| `/sub`（中文，README 主推） | 20 |
| `/static/sub_{en,ja,ru,de,fr,es,pt-BR,ko-KR,vi,id,hi,bn,ur-PK,pl,ar}` | 各 20 |

合并去重后共 **284 个互不相同的节点**，协议分布：

```
vless 168 · trojan 41 · ss 32 · hysteria2 32 · vmess 11
```

节点质量提示（实测 `sub` 解码结果）：

- **重名严重**：20 条链接只有 8 个不同名字（11 条都叫「美国 CloudFlare节点」）。Mihomo 会自动加后缀去重（`名字`、`名字-01`、`名字-02`…），所以不会丢节点，但列表可读性差。
- **4 条 vless 参数写法可疑**：`path=/pyip=ProxyIP.KR.CMLiussss.net`、`path=/proxyip=...`。`proxyip`/`pyip` 本该是独立查询参数，写进 `path` 会让 WebSocket 路径变成一整串乱码，这几条大概率连不通。
- 个别节点把 `themeforest.net`、`www.speedtest.net` 这类**第三方域名**当入口地址（CDN 前置），稳定性取决于别人。
- GitHub raw 不会返回 `subscription-userinfo` 响应头 → Clash 里**看不到流量/到期时间**。

---

## 三、Clash 侧：源码级兼容性核实

### 3.1 Mihomo（Clash Meta）内核 —— 原生支持，这是关键证据

`adapter/provider/provider.go` 解析订阅响应体的逻辑：**先当 YAML 解析，失败则回退到"分享链接"解析**：

```go
if err := yaml.Unmarshal(buf, schema); err != nil {
    proxies, err1 := convert.ConvertsV2Ray(buf)   // ← base64 分享链接走这里
    ...
    schema.Proxies = proxies
}
```

- `common/convert/base64.go`：`DecodeBase64` 依次尝试 `RawStdEncoding` → `StdEncoding` → 原文，所以带不带 padding 都能解。
- `common/convert/converter.go`：支持 `hysteria` / `hysteria2` / `vless` / `trojan` / `ss` / `ssr` / `vmess`，并用 `uniqueName()` 做重名去重。
- 官方 `docs/config.yaml` 明确写着：

```yaml
# Mihomo 格式的节点或支持 *ray 的分享格式
proxy-providers:
  provider1:
    type: http
    url: "url"
    interval: 3600     # 单位：秒 → 到点自动重新拉取
```

- `component/resource/fetcher.go`：`NewFetcher(name, interval, ...)` + 定时拉取循环，**这就是"节点更新后自动导入"的实现点**。

⚠️ 但注意两个边界：

1. **主配置不支持 base64**：`config.Parse()` 只认 Clash YAML，全文件里没有任何 `convert.` 调用；也就是说分享链接格式**只能通过 proxy-providers 进入**。
2. `PUT /configs` 只接受**本地绝对路径**（`hub/route/configs.go` 校验 `filepath.IsAbs`），不接受 URL —— 所以"下载订阅"这一步必然由客户端自己做。

> 附：上游 `Dreamacro/clash` 与 `Fndroid/clash_for_windows_pkg`（Clash for Windows）现在都已是 **404 下线**状态，不建议再围绕它们设计流程。

### 3.2 Clash Verge Rev —— 直接导入会失败（已确认）

`src-tauri/src/config/prfitem.rs` 中从 URL 创建配置的逻辑：

```rust
let yaml = serde_yaml_ng::from_str::<Mapping>(data)
    .context("the remote profile data is invalid yaml")?;
if !yaml.contains_key("proxies") && !yaml.contains_key("proxy-providers") {
    bail!("profile does not contain `proxies` or `proxy-providers`");
}
```

base64 文本是一整个字符串，`serde_yaml_ng::from_str::<Mapping>` 直接失败 → 导入报错。**必须用下面的方案 A 或 B。**

### 3.3 其它客户端

- **Clash Meta for Android**：本次未做源码核实（仓库树拉取失败）。判据同上 —— 看导入时是否报含 `proxies` 的错误，报了就说明它同样只吃 YAML，走方案 B/C。
- **ClashX / ClashX Pro**：基于老 Clash 内核，同样只吃 YAML 配置 → 方案 B/C。
- **Nyanpasu / 其它 Tauri 类**：行为与 Verge Rev 同类，大概率同样要求 YAML。

---

## 四、实测：拿真内核跑一遍（Mihomo Meta v1.10.4）

不猜了，直接跑。环境：真实 Mihomo 内核 `Mihomo Meta v1.10.4 linux amd64 with go1.23.12`，用 RESTful API 读取结果。

> ⚠️ 一个必须交代的前提：本次沙箱的出口**封了 `raw.githubusercontent.com`**（连接被重置），所以订阅内容由本地 HTTP 服务提供 —— 但喂给内核的字节与仓库里的 `sub` **md5 完全一致**（`8ac22c4319d91618d430d41d183d1575`）。也就是说：测的是**真内核 + 真订阅内容**，只把"源站在哪"换成了本地。真机连通性问题已在第六节单独讨论。

### 4.1 实验结果总表

| # | 实验 | 结果 |
|---|---|---|
| 1 | 配成 proxy-provider，内核导入订阅 | ✅ **20/20** 节点进入内核（Trojan 10 + Vless 10），重名自动变 `名字-01 … -10` |
| 2 | 16 个订阅文件逐个导入 | ✅ **16/16 全部成功**，每个文件都是 20/20 |
| 3 | 节点更新后自动生效 | ✅ 订阅内容改成 13 条（含 3 条新节点），**未重启内核**，10 秒内自动变成 13 条并出现新节点 |
| 4 | 把 base64 订阅当主配置文件加载 | ❌ `yaml: unmarshal errors: line 1: cannot unmarshal !!str 'dHJvamF...' into config.RawConfig` |
| 5 | 订阅里混进 1 条坏节点 | ❌ 整包更新被拒绝，保留旧列表（fail-closed），见 4.2 |
| 6 | 转换器产出的 YAML 当配置 | ✅ `mihomo -t` 校验通过；实跑后 `/proxies` 返回 20 个节点，分组正常 |

内核 API 真实输出（实验 1）：

```
provider=default      vehicleType=Compatible  count=  3
provider=free-local   vehicleType=HTTP        count= 20   updatedAt=2026-09-09T02:34:32Z
provider=free-remote  vehicleType=HTTP        count=  0   ← 沙箱内连不上远端，非格式问题

美国+CloudFlare节点              Trojan
美国+CloudFlare节点-01           Vless
巴西圣保罗+CloudFlare节点         Trojan
美国+V2CROSS.COM                 Vless
…
美国+CloudFlare节点-10           Trojan
```

实验 3（自动更新）的关键日志与结果 —— 只改订阅文件内容，不碰内核：

```
level=info  msg="[Provider] free-local's content update"     ← 内核自己发现内容变了
内核内节点数: 20 @02:34 → 13 @02:36，并出现【更新测试】新增节点A/B/C
```

### 4.2 实测发现的坑：一条坏节点 = 整包更新失败

在订阅末尾追加 1 条 REALITY 公钥非法的节点后，内核每隔 10 秒重试、每次都失败：

```
level=error msg="[Provider] free-local pull error: proxy 20 error: invalid REALITY public key"
updatedAt 停在旧时间戳，节点列表保持上一次成功的 20 条
```

也就是说 mihomo 对 provider 更新是 **fail-closed（整包原子替换）**：坏一条 → 整包丢弃 → 沿用旧列表。好消息是不会把节点清空；坏消息是**上游一旦夹带格式错误的节点，你的订阅就静止在那一版，直到上游修好**。这个仓库是自动抓取共享节点的，出这种问题的概率不低 —— 这也是我更推荐方案 C（自己转换时校验并剔除坏节点）的原因。

> 顺带实测了另一个判断依据：**把 base64 订阅直接当配置加载会失败**（实验 4）。这正是"某些客户端导入按钮报错"的根因 —— 客户端下载完直接按 YAML 解析，而它不是 YAML。

---

## 五、三种可行接法（推荐 A，最省事）

### 方案 A：挂成 proxy-provider（推荐，原生自动更新）

把它写进你的配置（Verge Rev 里放在「配置 → 扩展配置(Merge)」，纯 Mihomo 直接写进 `config.yaml`）：

```yaml
proxy-providers:
  free-servers:
    type: http
    url: "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub"
    interval: 21600          # 6h，和仓库更新节奏对齐（秒）
    path: ./free-servers.yaml
    proxy: DIRECT            # 拉取订阅用什么出口
    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 600

  free-servers-en:           # 可叠加多个语言版本，节点集互不重复
    type: http
    url: "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/static/sub_en"
    interval: 21600
    path: ./free-servers-en.yaml
    health-check: { enable: true, url: https://www.gstatic.com/generate_204, interval: 600 }

proxy-groups:
  - name: "免费节点"
    type: url-test
    use:                     # 这里填的是 provider 名，不是节点名
      - free-servers
      - free-servers-en
    url: https://www.gstatic.com/generate_204
    interval: 300
    tolerance: 200

rules:
  - MATCH,免费节点
```

- 优点：内核自己定时拉取，**节点更新后自动生效**，无需任何转换服务；`use:` 引用 provider，新增/删除节点不用改分组。
- 缺点：raw.githubusercontent.com 的连通性取决于你的网络（见第六节）。

### 方案 B：用订阅转换器转成 Clash YAML（适合"导入链接"按钮）

把分享链接订阅转成真正的 Clash 配置 URL，任何客户端都能导入：

- 仓库 README 自己推荐的在线转换：<https://acl4ssr-sub.github.io>
- 或自建 subconverter：`https://<你的转换端>/sub?target=clash&url=<urlencode 后的订阅地址>`

- 优点：客户端兼容性最好，还能顺手做重命名/过滤/测速。
- 缺点：多一个中间环节和故障点；公共转换服务会看到你的订阅 URL。

### 方案 C：自己建一个定时转换仓（最稳，推荐长期用）

在你自己的仓库里跑 Actions（例如 `cron: "17 */6 * * *"`）：

1. 抓取本仓的 16 个订阅文件 → base64 解码；
2. 合并去重（284 个节点）、按地区/协议重命名、修掉 `path=/pyip=` 这类坏参数；
3. 输出一份完整 Clash YAML（含 proxy-groups / rules）推到 `gh-pages` 或固定分支；
4. Clash 只订阅你自己的这一个 URL。

- 优点：URL 稳定、不怕上游换域名/锁仓库、能用 GitHub Pages/自定义域名解决连通性、可加测速剔除死节点。
- 缺点：需要维护（我可以帮你搭）。

---

## 六、时效与缓存：更新后多久能在 Clash 里看到

| 源 | 生效延迟 | 说明 |
|---|---|---|
| `raw.githubusercontent.com/<repo>/main/sub`（分支引用） | 最长约 5 分钟 CDN 缓存 | 该域名对分支引用返回 `Cache-Control: max-age=300` [1](https://stackoverflow.com/questions/46551413/github-not-update-raw-after-commit) |
| `raw.githubusercontent.com/<repo>/<commit-sha>/sub` | **永不更新**（immutable） | 本仓每次更新都换新 SHA，SHA 链接会永久冻结在旧节点，别用 |
| `cdn.jsdelivr.net/gh/.../@main/sub` | **最长 12 小时** | jsDelivr 对分支的缓存是 12h [2](https://github.com/jsdelivr/jsdelivr)，会吃掉 6h 更新节奏，**不建议当订阅源** |
| 自建 Pages / 转换端（方案 C） | 由你的缓存头决定 | 可设成 0~60s，最快 |

所以：`interval: 21600`（6h）足够，即便碰上 5 分钟 CDN 缓存也只是延迟几分钟。**但别用 jsDelivr**。

> 实测补充：本次分析所用的沙箱出口**无法直连** `raw.githubusercontent.com` / `cdn.jsdelivr.net`（连接被重置），只能通过 `api.github.com` 取内容。这提醒了一件事：**订阅源的连通性本身就是风险**，国内环境建议配镜像或直接用方案 C。

---

## 七、安全与合规提醒（别跳过）

- 这些是**公开共享节点**，流量经由第三方（大量 `*.workers.dev` / `*.pages.dev` 前置）中转，对方理论上可观测明文流量；trojan 密码在仓库里是明文的统一值，vless UUID 也是公开的。
- 不要用它登录网银、邮箱、公司后台、支付账号等敏感服务；不要传输任何隐私/凭据。
- 节点来源不可控，存在被记录、限速、劫持甚至钓鱼的可能；请自行评估并遵守所在地区的法律法规与网络管理规定。
- 建议：仅作临时/低敏感度用途，并开启客户端的"不泄露本地 DNS / 不代理内网"等保护。

---

## 八、本次分析附带的产物

- `tools/sub2clash.py` —— 一个零依赖的 base64 订阅 → Clash YAML 转换器，逻辑对齐 Mihomo 的 `ConvertsV2Ray`（含 `DecodeBase64` 降级、`uniqueName` 去重）。实测结果：

```
$ python3 tools/sub2clash.py sub -o out.yaml
[i] payload 5512B -> 20 proxies, 0 skipped     # sub / sub_en / sub_ja / sub_ru / sub_bn 均 20/20，0 丢弃
```

- 用途：可直接作为方案 C 的转换内核，或在没有 proxy-provider 支持的客户端上手工生成配置。
- 第四节的全部实验用的是同一套思路的最小配置（proxy-provider + `interval: 10` + 本地 HTTP 源），任何一台装了 mihomo 的机器上都能复现：
  `mihomo -d <home> -f config.yaml` → `curl http://127.0.0.1:9090/providers/proxies/<provider名>` 看节点数，
  `curl -X PUT http://127.0.0.1:9090/providers/proxies/<provider名>` 可手动触发一次更新。

---

## 九、要不要我接着做？

1. **方案 C 全套**：在你这个仓库里加一个每 6 小时跑的 Actions（抓取 → 去重 → 重命名 → **剔除会触发整包失败的问题节点（见 4.2）** → 输出 Clash YAML → 发到 Pages），给你一个稳定可订阅的 URL；
2. **方案 A 的现成配置**：直接生成一份可用的 `config.yaml`（provider + 分组 + 规则）；
3. **加测速剔除**：转换时对每个节点做 TCP/HTTP 延迟探测，只保留可用节点。
