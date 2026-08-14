$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonCandidates = @(
    $env:WECHAT_TTS_PYTHON,
    $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" }),
    $(Join-Path $env:USERPROFILE "miniforge3\envs\wechat-tts-voice\python.exe"),
    $((Get-Command python.exe -ErrorAction SilentlyContinue).Source)
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }

if (-not $pythonCandidates) {
    throw "Python was not found. Activate the wechat-tts-voice environment or set WECHAT_TTS_PYTHON."
}

$pythonPath = $pythonCandidates[0]
$environmentRoot = Split-Path -Parent $pythonPath
$appName = "WeChatTTS"
$distDir = Join-Path $projectRoot "dist\$appName"
$releaseDir = Join-Path $projectRoot "release"
$zipPath = Join-Path $releaseDir "$appName-win-x64.zip"

Push-Location $projectRoot
$originalPath = $env:PATH
try {
    $env:PATH = "$environmentRoot;$environmentRoot\Library\bin;$environmentRoot\DLLs;$environmentRoot\Scripts;$originalPath"
    & $pythonPath -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --windowed `
        --name $appName `
        --collect-all customtkinter `
        --collect-all resvg `
        --add-data "$projectRoot\assets;assets" `
        "$projectRoot\wechat_tts_widget.pyw"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code: $LASTEXITCODE"
    }

    if (-not (Test-Path -LiteralPath "$distDir\$appName.exe" -PathType Leaf)) {
        throw "Build completed but $appName.exe was not found"
    }

    foreach ($fileName in @("README-zh-CN.txt", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
        Copy-Item -LiteralPath "$projectRoot\$fileName" -Destination "$distDir\$fileName" -Force
    }

    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -LiteralPath $distDir -DestinationPath $zipPath -CompressionLevel Optimal

    Write-Host "EXE: $distDir\$appName.exe"
    Write-Host "ZIP: $zipPath"
}
finally {
    $env:PATH = $originalPath
    Pop-Location
}
