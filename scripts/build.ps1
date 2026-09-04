$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$BackupDir = Join-Path $Root ".build_backup"
$ReleaseDir = Join-Path $Root "release"
$DistDir = Join-Path $Root ".build_dist"
$PrefsBackup = Join-Path $BackupDir "preferences.json"
$StatsBackup = Join-Path $BackupDir "statistics.json"

# GitHub Actions starts from a clean checkout, where release\ does not exist.
# Create the destination before moving the PyInstaller bundle into it.
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

if (Test-Path (Join-Path $ReleaseDir "preferences.json")) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    Move-Item (Join-Path $ReleaseDir "preferences.json") $PrefsBackup -Force
}
if (Test-Path (Join-Path $ReleaseDir "statistics.json")) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    Move-Item (Join-Path $ReleaseDir "statistics.json") $StatsBackup -Force
}

function Assert-ReleaseUnlocked {
    if (Get-Process -Name "Fishing bot" -ErrorAction SilentlyContinue) {
        throw "Закрой Fishing bot.exe и повтори сборку."
    }

    $paths = @(
        (Join-Path $ReleaseDir "Fishing bot.exe")
    )
    $internal = Join-Path $ReleaseDir "_internal"
    if (Test-Path $internal) {
        $paths += Get-ChildItem $internal -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in ".pyd", ".dll", ".exe" } |
            Select-Object -First 8 -ExpandProperty FullName
    }

    foreach ($path in $paths) {
        if (-not $path -or -not (Test-Path $path)) { continue }
        try {
            $stream = [System.IO.File]::Open(
                (Resolve-Path $path),
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            $stream.Dispose()
        } catch {
            throw "Закрой Fishing bot.exe и повтори сборку."
        }
    }
}

function Test-QtBundle([string]$internalDir) {
    $dll = Get-ChildItem -LiteralPath $internalDir -Recurse -Filter "Qt6Widgets.dll" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $dll) {
        throw "Build failed: Qt6Widgets.dll missing in $internalDir"
    }
    $qss = Join-Path $internalDir "gui\styles\dark.qss"
    if (-not (Test-Path $qss)) {
        throw "Build failed: QSS missing: $qss"
    }
}

function Install-ReleaseBundle {
    $outputDir = Join-Path $DistDir "Fishing bot"
    $srcExe = Join-Path $outputDir "Fishing bot.exe"
    $srcInternal = Join-Path $outputDir "_internal"

    if (-not (Test-Path $srcExe) -or -not (Test-Path $srcInternal)) {
        throw "Build failed: PyInstaller output missing in $outputDir"
    }
    Test-QtBundle $srcInternal

    Assert-ReleaseUnlocked

    $stagingDir = Join-Path $DistDir "staging"
    $stagingInternal = Join-Path $stagingDir "_internal.new"
    $stagingExe = Join-Path $stagingDir "Fishing bot.exe.new"
    $destExe = Join-Path $ReleaseDir "Fishing bot.exe"
    $destInternal = Join-Path $ReleaseDir "_internal"
    $oldInternal = Join-Path $stagingDir "_internal.old"

    New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null
    Get-ChildItem -LiteralPath $stagingDir -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }

    Copy-Item $srcInternal $stagingInternal -Recurse -Force
    Copy-Item $srcExe $stagingExe -Force

    Test-QtBundle $stagingInternal

    if (Test-Path $destInternal) {
        try {
            Rename-Item -LiteralPath $destInternal -NewName "_internal.old"
            Move-Item (Join-Path $ReleaseDir "_internal.old") $oldInternal -Force
        } catch {
            throw "Закрой Fishing bot.exe и повтори сборку."
        }
    }
    try {
        Move-Item -LiteralPath $stagingInternal -Destination $destInternal
    } catch {
        if (Test-Path $oldInternal) {
            Move-Item $oldInternal $destInternal -Force -ErrorAction SilentlyContinue
        }
        throw "Закрой Fishing bot.exe и повтори сборку."
    }

    try {
        Move-Item -LiteralPath $stagingExe -Destination $destExe -Force
    } catch {
        throw "Закрой Fishing bot.exe и повтори сборку."
    }

    if (Test-Path $oldInternal) {
        Remove-Item $oldInternal -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $oldInternal) {
            Write-Warning "Не удалось удалить $oldInternal. Закрой Fishing bot.exe и удали папку вручную."
        }
    }

    Remove-Item $outputDir -Recurse -Force -ErrorAction SilentlyContinue
}

pip install -r requirements.txt
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
pyinstaller --noconfirm --clean --distpath $DistDir --workpath .pyinstaller_build "Fishing bot.spec"

Install-ReleaseBundle

# The bundle includes defaults from src/, but the copies next to the exe are
# user data. Put back the exact files moved aside before PyInstaller ran.
if (Test-Path $PrefsBackup) {
    Move-Item -LiteralPath $PrefsBackup -Destination (Join-Path $ReleaseDir "preferences.json") -Force
}
if (Test-Path $StatsBackup) {
    Move-Item -LiteralPath $StatsBackup -Destination (Join-Path $ReleaseDir "statistics.json") -Force
}

$ExePath = Join-Path $ReleaseDir "Fishing bot.exe"
if (-not (Test-Path $ExePath)) {
    throw "Build failed: $ExePath not found"
}
Test-QtBundle (Join-Path $ReleaseDir "_internal")

$WorkPath = Join-Path $Root ".pyinstaller_build"
if (Test-Path $WorkPath) {
    Remove-Item $WorkPath -Recurse -Force -ErrorAction SilentlyContinue
}
if (Test-Path $DistDir) {
    Remove-Item $DistDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Build complete:"
Write-Host "  $ExePath"
Write-Host "Launch: double-click Fishing bot.exe (keep _internal next to it)"
