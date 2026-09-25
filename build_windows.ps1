[CmdletBinding()]
param(
  [switch]$WithHarness,
  [switch]$SkipRuntimeBuild,
  [switch]$NoZip
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$RequiredSourcePaths = @(
  (Join-Path $Root 'workbench.html'),
  (Join-Path $Root 'backend\requirements.lock'),
  (Join-Path $Root 'assets\app-icon.ico'),
  (Join-Path $Root 'plugins\bundled')
)
foreach ($RequiredSourcePath in $RequiredSourcePaths) {
  if (-not (Test-Path $RequiredSourcePath)) {
    throw "The Windows build package is incomplete or was not fully extracted: $RequiredSourcePath"
  }
}

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $false)][string[]]$Arguments = @()
  )
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
  }
}

if (-not [Environment]::Is64BitOperatingSystem) {
  throw 'English WorkBench requires 64-bit Windows 10 or Windows 11.'
}

Write-Host 'Preparing isolated Windows Python environment...'
$VenvDir = Join-Path $Root '.venv-windows'
$Python = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $Python)) {
  $BootstrapPython = $null
  $BootstrapArgs = @()
  $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($null -ne $PyLauncher) {
    $BootstrapPython = $PyLauncher.Source
    $BootstrapArgs = @('-3.11')
  } else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $PythonCommand -and $PythonCommand.Source -notlike '*WindowsApps*') {
      $BootstrapPython = $PythonCommand.Source
    }
  }
  if ($null -eq $BootstrapPython) {
    throw 'Python 3.11 x64 was not found. Install it from python.org and enable Add Python to PATH.'
  }
  Invoke-Checked $BootstrapPython ($BootstrapArgs + @('-m', 'venv', $VenvDir))
}

Invoke-Checked $Python @(
  '-c',
  'import struct,sys; assert sys.version_info[:2] == (3,11), "Python 3.11 x64 is required"; assert struct.calcsize("P") * 8 == 64, "64-bit Python required"'
)
Invoke-Checked $Python @('-m', 'pip', 'install', '--upgrade', 'pip', 'wheel')
if ($WithHarness) {
  foreach ($HarnessPythonProject in @(
    (Join-Path $Root 'vendor\deepseek-harness-upstream\python\sdk-runtime'),
    (Join-Path $Root 'vendor\deepseek-harness-upstream\python\sdk')
  )) {
    if (-not (Test-Path (Join-Path $HarnessPythonProject 'pyproject.toml'))) {
      throw "The optional Harness source is incomplete: $HarnessPythonProject"
    }
    Invoke-Checked $Python @('-m', 'pip', 'install', '-e', $HarnessPythonProject)
  }
}
Invoke-Checked $Python @('-m', 'pip', 'install', '-r', (Join-Path $Root 'backend\requirements.lock'))
Invoke-Checked $Python @(
  '-m', 'pip', 'install',
  'pyinstaller>=6.21,<7',
  'pywebview>=5.3,<7',
  'pystray>=0.19,<1'
)

$RuntimeRoot = Join-Path $Root 'teachmate-runtime'
$RuntimeNode = Join-Path $RuntimeRoot 'node\node.exe'
$RuntimeCli = Join-Path $RuntimeRoot 'harness\apps\cli\lib\bin.js'
$RuntimePatch = Join-Path $RuntimeRoot 'harness\examples\teachmate\cordis.sdk.yml'
$RuntimeReady = (Test-Path $RuntimeNode) -and (Test-Path $RuntimeCli) -and (Test-Path $RuntimePatch)

if ($WithHarness -and -not $RuntimeReady) {
  if ($SkipRuntimeBuild) {
    throw 'A Windows TeachMate runtime was not found. Remove -SkipRuntimeBuild or provide teachmate-runtime\node\node.exe.'
  }

  Write-Host 'Building Windows Node/Harness runtime...'
  $NodeCommand = Get-Command node -ErrorAction SilentlyContinue
  $NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if ($null -eq $NpmCommand) {
    $NpmCommand = Get-Command npm -ErrorAction SilentlyContinue
  }
  if ($null -eq $NodeCommand -or $null -eq $NpmCommand) {
    throw 'Node.js 22.19+ x64 was not found. Install a current LTS version from nodejs.org and reopen this script.'
  }
  Invoke-Checked $NodeCommand.Source @(
    '-e',
    'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 22 || (major === 22 && minor < 19)) { console.error("Node.js 22.19+ is required"); process.exit(1); }'
  )

  $PnpmHome = Join-Path $Root '.windows-tools'
  $Pnpm = Join-Path $PnpmHome 'node_modules\.bin\pnpm.cmd'
  if (-not (Test-Path $Pnpm)) {
    Invoke-Checked $NpmCommand.Source @('install', '--prefix', $PnpmHome, 'pnpm@11.7.0')
  }
  $env:PATH = "$(Split-Path -Parent $Pnpm);$env:PATH"

  $HarnessSource = Join-Path $Root 'vendor\deepseek-harness-upstream'
  Push-Location $HarnessSource
  try {
    Invoke-Checked $Pnpm @('install', '--frozen-lockfile')
  } finally {
    Pop-Location
  }
  Invoke-Checked $Python @(
    (Join-Path $Root 'tools\build_teachmate_runtime.py'),
    '--node-binary',
    $NodeCommand.Source
  )
}

if ($WithHarness) {
  foreach ($RequiredRuntimeFile in @($RuntimeNode, $RuntimeCli, $RuntimePatch)) {
    if (-not (Test-Path $RequiredRuntimeFile)) {
      throw "Windows TeachMate runtime is incomplete: $RequiredRuntimeFile"
    }
  }
} else {
  Write-Host 'Building the stable Python Agent edition (no Node.js or Harness required).'
}

$Name = 'EnglishWorkBench'
$PyInstallerArgs = @(
  '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
  '--name', $Name,
  '--paths', $Root,
  '--icon', (Join-Path $Root 'assets\app-icon.ico'),
  '--add-data', "$Root\workbench.html;.",
  '--add-data', "$Root\workbench-assets;workbench-assets",
  '--add-data', "$Root\assets\app-icon.png;assets",
  '--add-data', "$Root\backend\alembic.ini;backend",
  '--add-data', "$Root\backend\migrations;backend\migrations",
  '--add-data', "$Root\backend\app\agent\prompts;backend\app\agent\prompts",
  '--add-data', "$Root\backend\app\agent\education_bridge;backend\app\agent\education_bridge",
  '--add-data', "$Root\plugins;plugins",
  '--collect-submodules', 'backend.app',
  '--collect-submodules', 'backend.migrations.versions',
  '--collect-submodules', 'webview',
  '--collect-submodules', 'pystray',
  '--hidden-import', 'backend.app.main',
  '--hidden-import', 'backend.app.factory',
  '--hidden-import', 'desktop_shell',
  '--hidden-import', 'blank_launcher',
  '--hidden-import', 'windows_launcher'
)
if ($WithHarness) {
  $PyInstallerArgs += @(
    '--paths', (Join-Path $Root 'vendor\deepseek-harness-upstream\python\sdk\src'),
    '--collect-submodules', 'deepseek_harness',
    '--add-data', "$RuntimeRoot;teachmate-runtime"
  )
}
$PyInstallerArgs += (Join-Path $Root 'blank_launcher.py')

Write-Host 'Building EnglishWorkBench.exe...'
Invoke-Checked $Python $PyInstallerArgs

$Output = Join-Path $Root "dist\$Name"
$ExePath = Join-Path $Output "$Name.exe"
if (-not (Test-Path $ExePath)) {
  throw "PyInstaller completed without producing $ExePath"
}

@('@echo off', 'start "" "EnglishWorkBench.exe"') |
  Set-Content (Join-Path $Output 'start_workbench.bat') -Encoding ASCII
Copy-Item (Join-Path $Root 'WINDOWS_GUIDE.md') $Output -Force
@(
  'English WorkBench Windows x64',
  "Built: $([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))",
  'Entry: EnglishWorkBench.exe',
  "Agent runtime: $(if ($WithHarness) { 'Harness' } else { 'Python (stable)' })",
  'Data: %APPDATA%\English Workbench Blank',
  'This package contains no user database or imported files.'
) | Set-Content (Join-Path $Output 'BUILD_INFO.txt') -Encoding UTF8

$ForbiddenData = Get-ChildItem $Output -Recurse -File | Where-Object {
  $_.Extension -in @('.db', '.sqlite', '.sqlite3') -or
  $_.Name -in @('workspace_state_backup.json', '.desktop-instance.lock', '.desktop-activate.sock')
}
if ($ForbiddenData) {
  $Names = ($ForbiddenData.FullName -join [Environment]::NewLine)
  throw "Build contains forbidden user-data files:$([Environment]::NewLine)$Names"
}

$ReleaseDir = Join-Path $Root 'release'
New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
if (-not $NoZip) {
  $ZipPath = Join-Path $ReleaseDir 'EnglishWorkBench_Windows_x64.zip'
  if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
  }
  Compress-Archive -Path $Output -DestinationPath $ZipPath -CompressionLevel Optimal
  $Hash = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
  "$Hash  $(Split-Path -Leaf $ZipPath)" |
    Set-Content "$ZipPath.sha256" -Encoding ASCII
  Write-Host "Created distribution ZIP: $ZipPath"
}

Write-Host ''
Write-Host 'Windows build completed successfully.' -ForegroundColor Green
Write-Host "EXE: $ExePath"
Write-Host 'Close hides the window; double-click the EXE or tray icon to reopen it.'
Write-Host 'Use the tray menu item Fully Exit to stop the app and local backend.'
