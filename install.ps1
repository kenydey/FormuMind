<#
.SYNOPSIS
  FormuMind 一键安装脚本 (Windows)

.DESCRIPTION
  一条命令完成：环境检查 → 后端/前端依赖安装 → .env 配置 → 数据库迁移。
  安装完成后按屏幕提示手动启动各服务（命令已列好，复制粘贴即可）。

  用法:
    # 方式一（推荐）：双击 install.bat
    # 方式二：powershell -ExecutionPolicy Bypass -File install.ps1

  依赖安装口径与 scripts/install.sh（Linux/macOS）一致。
#>

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ROOT "backend"
$VenvDir = Join-Path $BackendDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Write-Step($msg) { Write-Host "" ; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg ✓" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "    ⚠ $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "    ❌ $msg" -ForegroundColor Red }

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

function Find-Python {
  # 返回 @{ Exe = <python.exe>; Version = [version] }，找不到返回 $null
  # 先挑项目自己测试 / 打包用的版本（Dockerfile 与 CI 是 3.11），最后才是"最新的 3.x"：新发布的 Python
  # 往往还没有 rdkit / torch 等科学依赖的预编译包，`py -3` 会选到它（CI 的 Windows 机器上就是 3.14）。
  $cands = @()
  $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    foreach ($v in @("3.11", "3.12", "3.10")) { $cands += @{ Cmd = "py"; Args = @("-$v") } }
    $cands += @{ Cmd = "py"; Args = @("-3") }
  }
  foreach ($name in @("python", "python3")) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { $cands += @{ Cmd = $c.Source; Args = @() } }
  }
  foreach ($cand in $cands) {
    try {
      $out = & $cand.Cmd @($cand.Args + "--version") 2>&1 | Out-String
      if ($out -match "Python (\d+\.\d+\.\d+)") {
        $ver = [version]$Matches[1]
        if ($ver -ge [version]"3.10") {
          $exe = $cand.Cmd
          if ($cand.Cmd -eq "py") {
            # 解析 py -3.x 背后的真实 python.exe，供 venv 使用
            $exe = (& py @($cand.Args + @("-c", "import sys; print(sys.executable)")) 2>$null | Out-String).Trim()
            if (-not $exe) { $exe = "py" }
          }
          return @{ Exe = $exe; Version = $ver }
        }
      }
    } catch { }
  }
  return $null
}

Write-Step "[1/5] 环境检查"

$py = Find-Python
if (-not $py) {
  Write-Err "未找到 Python 3.10+"
  Write-Host "  请先安装 Python 3.10+（勾选 Add to PATH）："
  Write-Host "    winget install Python.Python.3.12"
  Write-Host "    或 https://www.python.org/downloads/"
  exit 1
}
Write-Ok "Python $($py.Version) ($($py.Exe))"
if ($py.Version -ge [version]"3.13") {
  Write-Warn "Python $($py.Version) 比本项目测试过的版本（3.11，Dockerfile 与 CI 所用）新；科学依赖可能没有预编译包。安装失败时请改用 3.11 或 3.12（winget install Python.Python.3.11）。"
}

$nodeCmd = Get-Command "node" -ErrorAction SilentlyContinue
$npmCmd  = Get-Command "npm" -ErrorAction SilentlyContinue
if ($nodeCmd) {
  $nodeVer = ((& node --version) -replace "^v", "").Trim()
  if ([version]$nodeVer -ge [version]"20.0") { Write-Ok "Node.js v$nodeVer" }
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
if ($npmCmd) {
  Push-Location (Join-Path $ROOT "frontend")
  try { Invoke-Step "npm install" { & npm install } } finally { Pop-Location }
} else {
  Write-Warn "跳过：未找到 npm，稍后手动执行 cd frontend && npm install"
}

# ---- [4/5] .env 与数据库迁移 ----
Write-Step "[4/5] 配置 .env"
$envFile = Join-Path $ROOT ".env"
if (Test-Path $envFile) {
  Write-Host "    .env 已存在，跳过"
} else {
  Copy-Item (Join-Path $ROOT ".env.example") $envFile
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
Write-Host "  手动启动（各开一个 PowerShell）："
Write-Host "    # 后端 API (:8000)"
Write-Host "    cd $BackendDir; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --port 8000"
Write-Host ""
Write-Host "    # Celery worker（Windows 必须加 --pool=solo）"
Write-Host "    cd $BackendDir; .\.venv\Scripts\Activate.ps1"
Write-Host "    celery -A app.worker.celery_app worker --pool=solo --loglevel=info"
Write-Host ""
Write-Host "    # 前端 (:5173)"
Write-Host "    cd $(Join-Path $ROOT 'frontend'); npm run dev"
Write-Host ""
Write-Host "  健康检查： curl http://localhost:8000/health"
Write-Host ""
Write-Host "  产品级运行需要 Redis + Datalab ELN（:5001），推荐 Docker Desktop："
Write-Host "    docker compose up -d redis"
Write-Host "    docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d"
Write-Host "  详见 docs/QUICKSTART.md（中文：docs/快速入门.md）"
Write-Host ""
Write-Host "  可选引擎（BayBE/科学计算等，原生 DOE/优化器不依赖它们）："
Write-Host "    cd $BackendDir; .\.venv\Scripts\Activate.ps1"
Write-Host '    pip install -e ".[intel,science,embedding,optimize,bo,baybe,pydoe,color,file_ingest,export,notebooklm,colbert,crag]"'
