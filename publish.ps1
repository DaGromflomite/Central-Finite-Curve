<#
  publish.ps1  -  Get Central Finite Curve + its Windows installer published.

  What it does, in order:
    1. Pre-flight checks (right folder, clean secret list, gh logged in, read version).
    2. Make sure a Windows build exists in dist\CentralFiniteCurve (optionally rebuild it).
    3. Make sure Inno Setup is installed (installs it via winget if missing), then
       build dist\CentralFiniteCurve-Setup-<version>.exe  <-- the installer.
    4. Zip the portable build as  CentralFiniteCurve-windows.zip.
    5. Create the GitHub repo (if not already linked) and push main.
    6. Create/update the pre-release v<version> and upload the installer + the zip.

  YOU run this (not Claude): publishing is an outward-facing action under your account.
  Run from the repo root:
      powershell -ExecutionPolicy Bypass -File .\publish.ps1
  Make it a PUBLIC repo straight away:
      powershell -ExecutionPolicy Bypass -File .\publish.ps1 -Public
  Rebuild the .exe from current source first (needs pyinstaller):
      powershell -ExecutionPolicy Bypass -File .\publish.ps1 -RebuildExe

  Nothing here is destructive to your data; it only reads tracked files, builds, and
  uploads. It will PAUSE and ask before creating the GitHub repo.
#>

[CmdletBinding()]
param(
    [string]$RepoName    = "Central-Finite-Curve",
    [string]$Description  = "Desktop Meshtastic monitor with live drone Remote ID flight tracking (Windows/macOS). GPL-3.0.",
    [switch]$Public,          # default is a PRIVATE repo; pass -Public to publish openly
    [switch]$RebuildExe,      # re-run PyInstaller before packaging
    [switch]$SkipGitHub       # build the installer locally only, don't touch GitHub
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    [ok] $m" -ForegroundColor Green }
function Die($m)  { Write-Host "`n[FAIL] $m" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 1. pre-flight
Step "Pre-flight checks"

if (-not (Test-Path ".git"))             { Die "Not a git repo. Run this from the MeshWidget folder." }
if (-not (Test-Path "mesh_widget.pyw"))  { Die "mesh_widget.pyw not found - wrong folder?" }

# Read the version straight from the source so it can never drift.
$verLine = Select-String -Path "mesh_widget.pyw" -Pattern 'VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $verLine) { Die "Could not read VERSION from mesh_widget.pyw" }
$Version = $verLine.Matches[0].Groups[1].Value
Ok "Version = $Version"

# Hard stop if any personal/secret file is tracked by git.
$leak = git ls-files | Select-String -Pattern 'config\.json|messages.*\.jsonl|nodes-history|dronealert|^private/|^flights/|\.log$'
if ($leak) {
    Write-Host "    These sensitive files are tracked and would be published:" -ForegroundColor Red
    $leak | ForEach-Object { Write-Host "      $_" -ForegroundColor Red }
    Die "Refusing to continue. Remove them with: git rm --cached <file>  (and add to .gitignore)"
}
Ok "No secrets tracked in git"

if (-not $SkipGitHub) {
    gh auth status 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { Die "gh is not logged in. Run:  gh auth login" }
    Ok "gh CLI authenticated"
}

# ---------------------------------------------------------------- 2. build exe
Step "Windows build (dist\CentralFiniteCurve)"

if ($RebuildExe) {
    if (-not (Test-Path "mesh_monitor.spec")) { Die "mesh_monitor.spec missing - cannot rebuild." }
    python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    Installing pyinstaller..." -ForegroundColor Yellow
        python -m pip install --quiet pyinstaller
    }
    Write-Host "    Running PyInstaller..." -ForegroundColor Yellow
    pyinstaller --noconfirm mesh_monitor.spec
    if ($LASTEXITCODE -ne 0) { Die "PyInstaller build failed." }
}

$distApp = Join-Path $root "dist\CentralFiniteCurve"
$exe     = Join-Path $distApp "CentralFiniteCurve.exe"
if (-not (Test-Path $exe)) {
    Die "No build found at $exe. Re-run with -RebuildExe (needs Python + deps installed)."
}
Ok "Build present: $exe"

# ---------------------------------------------------------------- 3. installer
Step "Build the Windows installer (Inno Setup)"

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) {
    Write-Host "    Inno Setup not found - installing via winget..." -ForegroundColor Yellow
    winget install --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $iscc)) { $iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
}
if (-not (Test-Path $iscc)) {
    Die "Inno Setup (ISCC.exe) still not found. Install it from https://jrsoftware.org/isdl.php and re-run."
}
Ok "ISCC: $iscc"

& $iscc "/DMyAppVersion=$Version" "/DSourceDir=$distApp" "/DOutputDir=$(Join-Path $root 'dist')" "installer\CentralFiniteCurve.iss"
if ($LASTEXITCODE -ne 0) { Die "Inno Setup build failed." }

$setup = Join-Path $root "dist\CentralFiniteCurve-Setup-$Version.exe"
if (-not (Test-Path $setup)) { Die "Expected installer not produced: $setup" }
Ok "Installer built: $setup"

# ---------------------------------------------------------------- 4. portable zip
Step "Package portable build (zip)"
$zip = Join-Path $root "dist\CentralFiniteCurve-windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $distApp "*") -DestinationPath $zip
Ok "Zip built: $zip"

if ($SkipGitHub) {
    Write-Host "`nDone (local only). Installer + zip are in .\dist\" -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------- 5. repo + push
Step "GitHub repository"
$hasOrigin = $false
git remote get-url origin 1>$null 2>$null
if ($LASTEXITCODE -eq 0) { $hasOrigin = $true }

if ($hasOrigin) {
    Ok "origin already set: $(git remote get-url origin)"
    git push -u origin main
    if ($LASTEXITCODE -ne 0) { Die "git push failed." }
} else {
    $vis = if ($Public) { "--public" } else { "--private" }
    Write-Host "    About to CREATE a new GitHub repo '$RepoName' ($vis) and push 'main' to it." -ForegroundColor Yellow
    $ans = Read-Host "    Type YES to proceed"
    if ($ans -ne "YES") { Die "Aborted by user before creating the repo." }
    gh repo create $RepoName $vis --source=. --remote=origin --push --description $Description
    if ($LASTEXITCODE -ne 0) { Die "gh repo create failed." }
    Ok "Repo created and main pushed"
}

# ---------------------------------------------------------------- 6. release
Step "Create / update pre-release v$Version"
$tag = "v$Version"

$notes = if (Test-Path "CHANGELOG.md") { "--notes-file CHANGELOG.md" } else { '--notes "Alpha release."' }

gh release view $tag 1>$null 2>$null
if ($LASTEXITCODE -eq 0) {
    Ok "Release $tag exists - uploading assets (clobber)"
    gh release upload $tag $setup $zip --clobber
    if ($LASTEXITCODE -ne 0) { Die "Asset upload failed." }
} else {
    # Creates the tag at current HEAD and the pre-release in one go.
    $cmd = "gh release create $tag `"$setup`" `"$zip`" --prerelease --title `"Central Finite Curve $Version`" $notes"
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) { Die "gh release create failed." }
    Ok "Pre-release $tag created with installer + zip attached"
}

$repoUrl = gh repo view --json url -q .url
Write-Host "`nDONE." -ForegroundColor Green
Write-Host "  Repo:    $repoUrl"
Write-Host "  Release: $repoUrl/releases/tag/$tag"
Write-Host "  Assets:  CentralFiniteCurve-Setup-$Version.exe  +  CentralFiniteCurve-windows.zip"
Write-Host "`nLater, to enable the CI build workflow (needs an interactive scope refresh):" -ForegroundColor DarkGray
Write-Host "  gh auth refresh -h github.com -s workflow" -ForegroundColor DarkGray
Write-Host "  (then un-ignore .github/workflows/ and mesh_monitor.spec, commit, push)" -ForegroundColor DarkGray
