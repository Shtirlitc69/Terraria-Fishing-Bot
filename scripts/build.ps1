$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$BackupDir = Join-Path $Root ".build_backup"
$ReleaseDir = Join-Path $Root "release"
$PrefsBackup = Join-Path $BackupDir "preferences.json"
$StatsBackup = Join-Path $BackupDir "statistics.json"

if (Test-Path (Join-Path $ReleaseDir "preferences.json")) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    Copy-Item (Join-Path $ReleaseDir "preferences.json") $PrefsBackup -Force
}
if (Test-Path (Join-Path $ReleaseDir "statistics.json")) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    Copy-Item (Join-Path $ReleaseDir "statistics.json") $StatsBackup -Force
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
    $outputDir = Join-Path $ReleaseDir "Fishing bot"
    $srcExe = Join-Path $outputDir "Fishing bot.exe"
    $srcInternal = Join-Path $outputDir "_internal"
    $destExe = Join-Path $ReleaseDir "Fishing bot.exe"
    $destInternal = Join-Path $ReleaseDir "_internal"

    if (-not (Test-Path $srcExe) -or -not (Test-Path $srcInternal)) {
        throw "Build failed: PyInstaller output missing in $outputDir"
    }
    Test-QtBundle $srcInternal

    Assert-ReleaseUnlocked

    $stagingInternal = Join-Path $ReleaseDir "_internal.new"
    $stagingExe = Join-Path $ReleaseDir "Fishing bot.exe.new"
    $oldInternal = Join-Path $ReleaseDir "_internal.old"

    Get-ChildItem -LiteralPath $ReleaseDir -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "_internal.new" -or
            $_.Name -eq "_internal.old" -or
            $_.Name -like "_internal_old*" -or
            $_.Name -like "*.exe.old" -or
            $_.Name -eq "Fishing bot.exe.new"
        } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }

    Copy-Item $srcInternal $stagingInternal -Recurse -Force
    Copy-Item $srcExe $stagingExe -Force

    Test-QtBundle $stagingInternal

    if (Test-Path $destInternal) {
        try {
            Rename-Item -LiteralPath $destInternal -NewName "_internal.old"
        } catch {
            throw "Закрой Fishing bot.exe и повтори сборку."
        }
    }
    try {
        Rename-Item -LiteralPath $stagingInternal -NewName "_internal"
    } catch {
        if (Test-Path $oldInternal) {
            Rename-Item -LiteralPath $oldInternal -NewName "_internal" -ErrorAction SilentlyContinue
        }
        throw "Закрой Fishing bot.exe и повтори сборку."
    }

    if (Test-Path $destExe) {
        try {
            Remove-Item -LiteralPath $destExe -Force
        } catch {
            throw "Закрой Fishing bot.exe и повтори сборку."
        }
    }
    Rename-Item -LiteralPath $stagingExe -NewName "Fishing bot.exe"

    if (Test-Path $oldInternal) {
        Remove-Item $oldInternal -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $oldInternal) {
            Write-Warning "Не удалось удалить $oldInternal. Закрой Fishing bot.exe и удали папку вручную."
        }
    }

    Remove-Item $outputDir -Recurse -Force -ErrorAction SilentlyContinue
}

pip install -r requirements.txt
pyinstaller --noconfirm --clean --distpath release --workpath .pyinstaller_build "Fishing bot.spec"

Install-ReleaseBundle

if (Test-Path $PrefsBackup) {
    Copy-Item $PrefsBackup (Join-Path $ReleaseDir "preferences.json") -Force
}
if (Test-Path $StatsBackup) {
    Copy-Item $StatsBackup (Join-Path $ReleaseDir "statistics.json") -Force
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

Write-Host "Build complete:"
Write-Host "  $ExePath"
Write-Host "Launch: double-click Fishing bot.exe (keep _internal next to it)"
