[CmdletBinding()]
param(
    [string]$Python = $env:SCA_USD_BOOTSTRAP_PYTHON,
    [string]$RuntimeDir = $env:SCA_RUNTIME_DIR
)

$ErrorActionPreference = 'Stop'
$addonRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))

try {
    if (-not $RuntimeDir) { throw 'SCA_RUNTIME_DIR is missing. Run provision through sca addon use.' }
    $runtimeRoot = [IO.Path]::GetFullPath($RuntimeDir)
    foreach ($rootPath in @($addonRoot, $runtimeRoot)) {
        if ($rootPath -match '[\x00-\x1f''"&|<>^%!()]') {
            throw 'SCA 2.2.4 Windows installation/runtime paths cannot contain quotes or cmd metacharacters. Spaces are supported.'
        }
    }
    if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) {
        throw 'The runtime directory must be created by sca addon add before provisioning.'
    }
    if (-not $Python) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            $Python = (& $launcher.Source -3.12 -I -c 'import sys; print(sys.executable)' | Select-Object -Last 1)
            if ($LASTEXITCODE -ne 0) { $Python = $null }
        }
        if (-not $Python) {
            $candidate = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($candidate) { $Python = $candidate.Source }
        }
    }
    if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw 'CPython 3.12 was not found. Set SCA_USD_BOOTSTRAP_PYTHON to its python.exe path and retry provision.'
    }
    & $Python -I -B (Join-Path $PSScriptRoot 'provision_runtime.py') --runtime-dir $runtimeRoot
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('sca-usd-export provision: ' + $_.Exception.Message)
    exit 2
}
