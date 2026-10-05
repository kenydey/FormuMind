<#
.SYNOPSIS
  FormuMind 一键安装脚本 (Windows)

.DESCRIPTION
  一条命令完成：环境检查 → 后端/前端依赖安装 → .env 配置 → 数据库迁移。
  装完用 scripts\windows\start-dev.ps1 启动（Redis/Neo4j + API + worker + 前端），
  或按屏幕末尾的手动命令启动。

  用法:
    # 方式一（推荐）：双击 install.bat
    # 方式二：powershell -ExecutionPolicy Bypass -File install.ps1
    # 只装核心（最快）:      install.ps1 -Minimal
    # 追加 torch 引擎:       install.ps1 -Full
    # 只装后端:             install.ps1 -SkipFrontend

  依赖安装口径与 scripts/install.sh（Linux/macOS）一致。

  Windows 专有处理 —— 以下四处是 POSIX 脚本照搬过来必然踩的：

  * 全程用 .venv\Scripts\python.exe 绝对路径调用，不碰 Activate.ps1：既避免
    「source 不是 cmdlet」这类报错，也不受 Set-ExecutionPolicy 限制。
  * venv 按解释器校验并可能重建。Linux 上建的 .venv 只有 bin/ 没有 Scripts\，
    Windows 上根本激活不了；换个 Python 小版本复用旧 venv 也会因 wheel 标签
    （cp311/cp312）不匹配而装不上。检测到这两种情况就把旧 venv 改名为
    .venv.bak-<时间戳> 再重建，不直接删。
  * 依赖分档安装。pip 把 -e ".[a,b,c]" 的全部 extras 放在同一个解析事务里，
    一个不可能满足的依赖会让整条命令失败、并且什么都不装。那个依赖是 ColBERT：
    ragatouille → voyager 的 Windows wheel 只到 CPython 3.12，3.13/3.14 上会显式
    跳过并提示（RAG 自动退回 BM25 / TF-IDF / sentence-transformers）。
  * Celery 在 Windows 必须 --pool=solo（默认 prefork 池无法运行），启动脚本里已固定。
#>
param(
  [switch]$Minimal,       # 只装 requirements.txt + 后端核心，不装可选引擎
  [switch]$Full,          # 追加 torch 引擎（bo / heavy / colbert，走 CPU wheel 源）
  [switch]$SkipFrontend   # 跳过前端 npm install
)

$ErrorActionPreference = "Stop"
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$BackendDir = Join-Path $Root "backend"
$VenvDir = Join-Path $BackendDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Write-Step($msg) { Write-Host "" ; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg ✓" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    ⚠ $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    ❌ $msg" -ForegroundColor Red }

# 原生命令（pip / npm / alembic）失败不会触发 $ErrorActionPreference = "Stop"：
# 不检查 $LASTEXITCODE 的话，依赖没装上也会一路打印「安装完成」。
function Invoke-Step($What, [scriptblock]$Command) {
  & $Command
  if ($LASTEXITCODE) {
    Write-Err "$What 失败（退出码 $LASTEXITCODE），已中止安装"
    exit $LASTEXITCODE
  }
}

# 可选步骤：成功返回 $true，失败只警告。临时放宽 Stop，避免 Windows PowerShell 5.1 把原生命令的
# stderr 当作终止错误；输出送去 Out-Host，否则它会混进函数的返回值（数组恒为真）。
function Invoke-Optional([scriptblock]$Command) {
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Command | Out-Host
    return (-not $LASTEXITCODE)
  } finally {
    $ErrorActionPreference = $previous
  }
}

# 探测型调用（--version / -c "..."）：要拿输出，又不能在失败时中止安装，所以返回而不是抛出。
# 放宽 Stop 的理由：py -3.12 没装时会把错误写进 stderr，那在 Stop 下是终止错误。
# catch 的理由不同 —— `& 不存在的命令` 抛的是终止错误，Continue 降不下来（实测 py 的
# 版本选择器在 py.exe 存在但该版本缺失时会走到这里），探测必须能返回而不是中断安装。
function Invoke-Probe([scriptblock]$Command) {
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $output = & $Command 2>&1 | Out-String
    return @{ Ok = (-not $LASTEXITCODE); Output = $output.Trim() }
  } catch {
    return @{ Ok = $false; Output = "$_" }
  } finally {
    $ErrorActionPreference = $previous
  }
}

function Find-Python {
  # 返回 @{ Exe = <python.exe>; Version = "3.12" }，找不到返回 $null。
  # 顺序有意为之：3.12 → 3.11 优先，因为 ColBERT 的 Windows wheel 到 3.12 为止；
  # 3.13/3.14 仍然接受，但会在下一步提示 ColBERT 不可用。3.10 及更低不在其列：requirements.txt 固定的
  # numpy 2.x 要求 >= 3.11，在 3.10 上 pip 会在依赖解析处失败，远离原因。
  $candidates = @()
  if (Get-Command "py" -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.12", "3.11", "3.13", "3.14")) {
      $candidates += @{ Exe = "py"; Args = @("-$v") }
    }
  }
  foreach ($name in @("python", "python3")) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { $candidates += @{ Exe = $c.Source; Args = @() } }
  }

  $code = "import sys;print('{0}.{1}'.format(*sys.version_info[:2]));print(sys.executable)"
  foreach ($cand in $candidates) {
    $probe = Invoke-Probe { & $cand.Exe @($cand.Args + @("-c", $code)) }
    if (-not $probe.Ok) { continue }
    $lines = @($probe.Output -split "\r?\n" | Where-Object { $_.Trim() })
    if ($lines.Count -lt 2) { continue }
    $version = $lines[0].Trim()
    $exe = $lines[1].Trim()
    if ($version -notmatch '^\d+\.\d+$') { continue }
    if ([version]$version -lt [version]"3.11") { continue }
    return @{ Exe = $exe; Version = $version }
  }
  return $null
}

# 可选引擎的档位。分档的原因见文件头：一个不可满足的 extra 会让整条 pip 命令失败。
# torch 相关的（bo/heavy/colbert）单独一档，并走 CPU-only wheel 源 —— 默认 wheel 带
# CUDA，约 2.5GB。
$extras = @(
  "science", "optimize", "intel", "file_ingest", "report_export", "parse_pro",
  "export", "embedding", "pydoe", "baybe", "color", "crag", "notebooklm",
  "dev", "postgres"
)
$torchExtras = @("bo", "heavy")

Write-Step "[1/5] 环境检查"

$py = Find-Python
if (-not $py) {
  Write-Err "未找到 Python 3.11+（3.10 及更低装不上 requirements.txt：numpy 2.x 要求 >= 3.11）"
  Write-Host "  请先安装 Python 3.12（推荐，ColBERT 需要 ≤3.12）："
  Write-Host "    winget install --id Python.Python.3.12"
  Write-Host "    或 https://www.python.org/downloads/windows/"
  exit 1
}
$pyMinor = [int]($py.Version.Split(".")[1])
$colbertOk = ($pyMinor -le 12)
Write-Ok "Python $($py.Version) ($($py.Exe))"
if (-not $colbertOk) {
  Write-Warn "CPython $($py.Version) 没有 ColBERT 的 Windows wheel（ragatouille → voyager 到 3.12 为止）"
  Write-Host "        这一档会被跳过，其余全部安装；RAG 自动退回 BM25 / TF-IDF / 向量检索"
  Write-Host "        需要 ColBERT 请改用 3.12 重建： py -3.12 -m venv backend\.venv"
}
if ([version]$py.Version -ge [version]"3.14") {
  Write-Warn "Python $($py.Version) 比本项目测试过的版本（3.11–3.13；3.11 是 Dockerfile 与 CI 阻塞 job 所用）新；科学依赖可能没有预编译包。安装失败时请改用 3.12 或 3.11（winget install --id Python.Python.3.12）。"
}

$nodeCmd = Get-Command "node" -ErrorAction SilentlyContinue
$npmCmd  = Get-Command "npm" -ErrorAction SilentlyContinue
if ($nodeCmd) {
  $nodeProbe = Invoke-Probe { & $nodeCmd.Source --version }
  $nodeVer = ($nodeProbe.Output -replace "^v", "").Trim()
  if ($nodeVer -and ([version]$nodeVer -ge [version]"20.0")) { Write-Ok "Node.js v$nodeVer" }
  else { Write-Warn "Node.js v$nodeVer 过低（需要 >= 20），前端安装可能失败" }
  if (-not $npmCmd) { Write-Err "找到 node 但未找到 npm"; exit 1 }
} else {
  Write-Warn "未找到 node — 将跳过前端安装（只跑 API 不受影响）"
  Write-Host "  winget install OpenJS.NodeJS.LTS  或 https://nodejs.org/"
}

if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
  Write-Warn "未找到 git（不影响安装，推送代码时需要）"
}

# ---- [2/5] 后端依赖 ----
Write-Step "[2/5] 安装后端依赖（virtualenv + pinned requirements + extras）"

$rebuildReason = ""
if (Test-Path $VenvDir) {
  if (-not (Test-Path $VenvPython)) {
    $rebuildReason = "已有 .venv 里没有 Scripts\python.exe（Linux/macOS 上建的 venv，Windows 无法使用）"
  } else {
    $existing = Invoke-Probe { & $VenvPython -c "import sys;print('{0}.{1}'.format(*sys.version_info[:2]))" }
    $existingVersion = @($existing.Output -split "\r?\n" | Where-Object { $_.Trim() } | Select-Object -First 1)
    if ($existingVersion -and ($existingVersion.Trim() -ne $py.Version)) {
      $rebuildReason = "已有 .venv 是 CPython $($existingVersion.Trim())，与选中的 $($py.Version) 不一致（wheel 标签不匹配）"
    }
  }
}

if ($rebuildReason) {
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $backup = "$VenvDir.bak-$stamp"
  Write-Warn "重建 .venv：$rebuildReason"
  Write-Host "    旧 venv 改名保留为 $backup"
  Rename-Item -Path $VenvDir -NewName (Split-Path -Leaf $backup)
}

if (-not (Test-Path $VenvPython)) {
  Write-Host "    创建虚拟环境 $VenvDir ..."
  Invoke-Step "创建虚拟环境" { & $py.Exe -m venv $VenvDir }
} else {
  Write-Host "    复用已有虚拟环境"
}
Invoke-Step "升级 pip" { & $VenvPython -m pip install -U pip setuptools wheel }

Push-Location $BackendDir
try {
  # 与 scripts/install.sh 一致：先装 Docker/CI 锁定版本，再叠加 extras
  Invoke-Step "安装 requirements.txt" { & $VenvPython -m pip install -r requirements.txt }
  Invoke-Step "安装后端（pip install -e .[dev,llm]）" { & $VenvPython -m pip install -e ".[dev,llm]" }

  if ($Minimal) {
    Write-Warn "跳过可选引擎（-Minimal）：DOE / 优化的原生引擎不需要它们"
  } else {
    # 第二档：不含 torch 的引擎。单独一条命令，装了它就与第三档无关。
    $spec2 = ".[" + ($extras -join ",") + "]"
    Write-Host "    安装可选用引擎（无 torch）：$spec2"
    Invoke-Step "安装引擎 extras" { & $VenvPython -m pip install -e $spec2 }

    if ($Full) {
      # 第三档：torch 系。CPU wheel 源；colbert 仅 3.12 及以下。
      $torch = @($torchExtras)
      if ($colbertOk) { $torch += "colbert" } else { Write-Warn "colbert 已跳过（CPython > 3.12）" }
      $spec3 = ".[" + ($torch -join ",") + "]"
      $cpuIndex = "https://download.pytorch.org/whl/cpu"
      Write-Host "    安装 torch 引擎：$spec3  --extra-index-url https://download.pytorch.org/whl/cpu"
      $torchOk = Invoke-Optional { & $VenvPython -m pip install -e $spec3 --extra-index-url $cpuIndex }
      if (-not $torchOk) {
        Write-Warn "torch 档安装失败（原生 DOE/优化器不依赖它，后端照常可用）"
        Write-Host "    稍后重试： `"$VenvPython`" -m pip install -e $spec3 --extra-index-url $cpuIndex"
      }
    } else {
      Write-Host "    未装 torch 引擎（bo/heavy/colbert）：需要时加 -Full 重跑"
    }
  }

  # 轻量在线检索（失败不中断，与 install.sh 一致）
  if (-not (Invoke-Optional { & $VenvPython -m pip install arxiv semanticscholar ddgs })) {
    Write-Warn "arxiv/semanticscholar/ddgs 安装失败，已跳过（在线检索可选）"
  }
} finally { Pop-Location }

# 第三方库补丁（幂等；与 scripts/install.sh 一致）
$patchScript = Join-Path $BackendDir "scripts\apply_patches.py"
if (Test-Path $patchScript) {
  if (-not (Invoke-Optional { & $VenvPython $patchScript })) {
    Write-Warn "补丁应用失败，详见 backend/scripts/reference/rapidocr-attribute-fix.md"
  }
}

# ---- [3/5] 前端依赖 ----
Write-Step "[3/5] 安装前端依赖"
if ($SkipFrontend) {
  Write-Warn "已跳过（-SkipFrontend）"
} elseif ($npmCmd) {
  Push-Location (Join-Path $Root "frontend")
  # --legacy-peer-deps 与 frontend/Dockerfile 的构建口径一致
  try { Invoke-Step "npm install" { & npm install --legacy-peer-deps --no-audit --no-fund } } finally { Pop-Location }
} else {
  Write-Warn "跳过：未找到 npm，稍后手动执行 cd frontend && npm install"
}

# ---- [4/5] .env 与数据库迁移 ----
Write-Step "[4/5] 配置 .env"
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
  Write-Host "    .env 已存在，跳过"
} else {
  Copy-Item (Join-Path $Root ".env.example") $envFile
  Write-Host "    已从 .env.example 生成 .env"
  Write-Host "    内网/免登录：FORMUMIND_API_AUTH_ENABLED=false"
  Write-Host "    LLM Key 可选（不填则用规则引擎离线运行）"
}

Write-Step "[5/5] 数据库迁移"
Push-Location $BackendDir
try { Invoke-Step "数据库迁移" { & $VenvPython -m alembic upgrade head } } finally { Pop-Location }
Write-Host "    数据库已就绪"

# ---- 完成 ----
Write-Host ""
Write-Host "✅ 安装完成" -ForegroundColor Green
Write-Host ""
Write-Host "  一键启动（Redis/Neo4j 走 Docker，然后 API + worker + 前端）："
Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1 start"
Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1 status"
Write-Host "    （或双击 scripts\windows\start.cmd / status.cmd / stop.cmd）"
Write-Host ""
Write-Host "  手动启动（各开一个 PowerShell；不需要 activate，直接用 venv 里的解释器）："
Write-Host "    # 后端 API (:8000)"
Write-Host "    cd $BackendDir; .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload --reload-dir app"
Write-Host ""
Write-Host "    # Celery worker（Windows 必须 --pool=solo）"
Write-Host "    cd $BackendDir; .\.venv\Scripts\python.exe -m celery -A app.worker.celery_app worker --pool=solo --loglevel=info"
Write-Host ""
Write-Host "    # 前端 (:5173)"
Write-Host "    cd $(Join-Path $Root "frontend"); npm run dev"
Write-Host ""
Write-Host "  健康检查： curl http://localhost:8000/health"
Write-Host ""
Write-Host "  产品级运行需要 Redis + Datalab ELN（:5001），推荐 Docker Desktop（Redis/Neo4j 由启动脚本拉起）："
Write-Host "    docker compose up -d redis kg"
Write-Host "    docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d"
Write-Host "  详见 docs/QUICKSTART.md（中文：docs/快速入门.md）与 scripts/windows/README.md"
Write-Host ""
Write-Host "  另有两个 extra 没有装（按需手动）："
Write-Host "    .\backend\.venv\Scripts\python.exe -m pip install -e `".[observability]`"   # langfuse 追踪"
Write-Host "    .\backend\.venv\Scripts\python.exe -m pip install -e `".[intel-colbert]`"   # 上面两档的合集"
Write-Host ""
