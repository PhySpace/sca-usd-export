# The SCA descriptor calls this script with a trusted runtime root and safe words.
# Paths supplied by users travel only inside a UTF-8 JSON/Base64 data envelope.
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$RuntimeDir = $env:SCA_RUNTIME_DIR,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$DispatchArguments
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    . (Join-Path $PSScriptRoot 'native_process.ps1')
    $payload = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    Assert-ScaUsdInstallPath $payload 'Addon payload root'
    if ([string]::IsNullOrWhiteSpace($RuntimeDir)) {
        throw 'Runtime directory is missing. Invoke this addon through sca addon use, or pass -RuntimeDir explicitly.'
    }
    $runtime = [System.IO.Path]::GetFullPath($RuntimeDir)
    Assert-ScaUsdInstallPath $runtime 'Addon runtime root'
    if ($DispatchArguments.Count -eq 1 -and $DispatchArguments[0] -cin @('check', 'provision')) {
        $commandArguments = @($DispatchArguments[0])
        $token = ConvertTo-ScaUsdEncodedArguments $commandArguments
    }
    elseif ($DispatchArguments.Count -eq 2 -and $DispatchArguments[0] -ceq 'argv-json-base64') {
        $token = $DispatchArguments[1]
        $commandArguments = ConvertFrom-ScaUsdEncodedArguments $token
    }
    else {
        throw "Use scripts/use.ps1 -Arguments @('export','INPUT.scadpkg','OUTPUT.usda'), or the safe check/provision commands. Raw paths after sca addon use are unsupported on SCA 2.2.5 Windows."
    }
    $childEnvironment = @{
        SCA_ADDON_DIR = $payload
        SCA_RUNTIME_DIR = $runtime
        PYTHONUTF8 = '1'
        PYTHONIOENCODING = 'utf-8'
    }
    if ($commandArguments[0] -ceq 'provision') {
        if ($commandArguments.Count -ne 1) {
            throw 'Provision accepts no trailing arguments. Set SCA_USD_BOOTSTRAP_PYTHON to select the base interpreter.'
        }
        $provision = Join-Path $PSScriptRoot 'provision.ps1'
        if (-not (Test-Path -LiteralPath $provision -PathType Leaf)) { throw "Provision script is missing: $provision" }
        $powershell = Join-Path $PSHOME 'powershell.exe'
        $exitCode = Start-ScaUsdNativeProcess $powershell @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $provision) $childEnvironment
        # Normal successful shutdown avoids a measured CLR Exit delay. Plain
        # `exit` in the descriptor's dot-sourced -Command normalizes nonzero
        # codes to 1, so preserve those exact native codes with Environment.Exit.
        if ($exitCode -eq 0) { exit 0 }
        [Environment]::Exit($exitCode)
    }
    $python = Join-Path $runtime '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Addon runtime interpreter is missing: $python. Prepare it with: sca addon use sca-usd-export provision. No host Python dependencies will be borrowed for export."
    }
    $entry = Join-Path $PSScriptRoot 'runtime_cli.py'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw "Runtime entry script is missing: $entry" }
    $exitCode = Start-ScaUsdNativeProcess $python @('-I', '-B', $entry, 'argv-json-base64', $token) $childEnvironment
    if ($exitCode -eq 0) { exit 0 }
    [Environment]::Exit($exitCode)
}
catch {
    [Console]::Error.WriteLine('sca-usd-export: ' + $_.Exception.Message)
    [Environment]::Exit(2)
}
