<#
.SYNOPSIS
    在你自己的网络里实测一个 Clash 订阅：导入 → 逐节点延迟 → 用最快节点跑真实流量。

.DESCRIPTION
    下载官方 mihomo 内核，把订阅挂成 proxy-provider，然后用内核 API 做真机验证：
      1) 订阅能否被内核解析、导入多少个节点
      2) 每个节点的真实延迟（0 = 超时/不可用）
      3) 选最快的节点，真实发一个请求出去，对比"直连出口 IP"和"走代理出口 IP"
    只监听 127.0.0.1，且只结束自己启动的内核进程，不会影响你正在运行的 Clash Verge Rev。

.EXAMPLE
    .\Test-Subscription.ps1
    .\Test-Subscription.ps1 -SubUrl "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/static/sub_ja"
#>
param(
    [string]$SubUrl      = "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub",
    [string]$ProbeUrl    = "https://www.gstatic.com/generate_204",
    [int]   $MixedPort   = 7899,   # 避开 Clash Verge Rev 默认的 7897
    [int]   $ApiPort     = 9099,
    [string]$MihomoVer   = "v1.19.30"
)

$ErrorActionPreference = "Stop"
$work = Join-Path $env:TEMP "mihomo-sub-test"
New-Item -ItemType Directory -Force -Path $work | Out-Null
Push-Location $work

function Say($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# ---------- 1. 下载内核 ----------
Say "1/6 下载 mihomo $MihomoVer"
$asset = "mihomo-windows-amd64-$MihomoVer.zip"
$exe   = Join-Path $work "mihomo.exe"
if (-not (Test-Path $exe)) {
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri "https://github.com/MetaCubeX/mihomo/releases/download/$MihomoVer/$asset" -OutFile $asset
    Expand-Archive -Force -Path $asset -DestinationPath $work
    $found = Get-ChildItem -Path $work -Filter *.exe -Recurse | Select-Object -First 1
    if (-not $found) { throw "解压后没找到 mihomo.exe" }
    $exe = $found.FullName
}
& $exe -v

# ---------- 2. 写配置 ----------
Say "2/6 生成测试配置"
New-Item -ItemType Directory -Force -Path (Join-Path $work "home") | Out-Null
@"
mixed-port: $MixedPort
bind-address: 127.0.0.1
external-controller: 127.0.0.1:$ApiPort
allow-lan: false
ipv6: false
mode: rule
log-level: warning
proxy-providers:
  sub:
    type: http
    url: "$SubUrl"
    interval: 3600
    path: ./providers/sub.yaml
    proxy: DIRECT
proxy-groups:
  - name: "TEST"
    type: select
    use: [sub]
rules:
  - MATCH,TEST
"@ | Set-Content -Encoding UTF8 (Join-Path $work "config.yaml")

$log = Join-Path $work "mihomo.log"
$proc = Start-Process -FilePath $exe -ArgumentList @("-d", (Join-Path $work "home"), "-f", (Join-Path $work "config.yaml")) `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden -PassThru

try {
    # ---------- 3. 等内核起来 ----------
    Say "3/6 等待内核就绪"
    $api = "http://127.0.0.1:$ApiPort"
    $ok = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try { Invoke-RestMethod "$api/version" -TimeoutSec 2 | Out-Null; $ok = $true; break } catch {}
    }
    if (-not $ok) { throw "内核没起来，看日志：$log" }
    Write-Host "  内核已就绪 (pid $($proc.Id))"

    # ---------- 4. 订阅导入情况 ----------
    Say "4/6 订阅导入情况"
    Start-Sleep -Seconds 3
    $providers = Invoke-RestMethod "$api/providers/proxies"
    foreach ($p in $providers.providers.PSObject.Properties) {
        $v = $p.Value
        Write-Host ("  provider {0}: {1} 个节点 (vehicleType={2}, updatedAt={3})" -f $v.name, $v.proxies.Count, $v.vehicleType, $v.updatedAt)
    }
    $names = (Invoke-RestMethod "$api/providers/proxies/sub").proxies | ForEach-Object { $_.name }
    if (-not $names) { Write-Host "  !! 一个节点都没导入 —— 订阅内容或网络有问题（详见脚本顶部注释）" -ForegroundColor Red }
    else { Write-Host "  节点清单:"; $names | ForEach-Object { Write-Host "    - $_" } }

    # ---------- 5. 延迟测试 ----------
    Say "5/6 延迟测试（$ProbeUrl，超时 8s）"
    $delayUrl = "$api/group/TEST/delay?url=" + [uri]::EscapeDataString($ProbeUrl) + "&timeout=8000"
    $delays = Invoke-RestMethod $delayUrl -TimeoutSec 120
    $rows = $delays.PSObject.Properties | Sort-Object { [int]$_.Value }
    foreach ($r in $rows) {
        $ms = [int]$r.Value
        $color = if ($ms -gt 0) { "Green" } else { "DarkGray" }
        Write-Host ("  {0,6} ms  {1}" -f $ms, $r.Name) -ForegroundColor $color
    }
    $alive = @($rows | Where-Object { [int]$_.Value -gt 0 })
    Write-Host ("  可用 {0} / 共 {1}" -f $alive.Count, @($rows).Count)

    # ---------- 6. 真实流量 ----------
    Say "6/6 真实流量验证（对比直连 / 走代理）"
    if ($alive.Count -eq 0) {
        Write-Host "  没有可用节点，跳过" -ForegroundColor Yellow
    } else {
        $best = $alive[0].Name
        Write-Host "  选中节点: $best"
        $body = @{ name = $best } | ConvertTo-Json
        Invoke-RestMethod -Method Put -Uri "$api/proxies/TEST" -ContentType "application/json" -Body $body | Out-Null
        Start-Sleep -Seconds 1

        try   { $direct = (Invoke-WebRequest -Uri "https://api.ipify.org?format=json" -TimeoutSec 15 -UseBasicParsing).Content }
        catch { $direct = "(直连失败)" }
        try   { $proxied = (Invoke-WebRequest -Uri "https://api.ipify.org?format=json" -Proxy "http://127.0.0.1:$MixedPort" -TimeoutSec 20 -UseBasicParsing).Content }
        catch { $proxied = "(走代理失败)" }

        Write-Host "  直连出口   : $direct"
        Write-Host "  走代理出口 : $proxied"
        if ($direct -ne $proxied -and $proxied -notmatch "失败") {
            Write-Host "  ✅ 出口 IP 不同 —— 流量确实走了代理节点" -ForegroundColor Green
        } else {
            Write-Host "  ⚠️ 出口 IP 相同 —— 流量可能没走代理（或该节点做了 NAT 出口复用）" -ForegroundColor Yellow
        }
    }
}
finally {
    Say "清理"
    if ($proc -and -not $proc.HasExited) {
        Write-Host "  只结束本次启动的内核 (pid $($proc.Id))，不影响你正在运行的 Clash"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  工作目录: $work （config.yaml / mihomo.log 保留，可自行查看）"
    Pop-Location
}
