# PowerShell wrapper around the real SCA addon-use route. It never evaluates argv.
param(
    [string]$ScaPython,
    [string]$ScaCommand,
    [string]$AddonHome,
    [string]$SkillsDir,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    . (Join-Path $PSScriptRoot 'native_process.ps1')
    if ($ScaPython -and $ScaCommand) { throw 'Choose -ScaPython or -ScaCommand, not both.' }
    $token = ConvertTo-ScaUsdEncodedArguments $Arguments
    if ($AddonHome) { Assert-ScaUsdInstallPath $AddonHome 'Addon home' }
    if ($env:SCA_ADDON_HOME) { Assert-ScaUsdInstallPath $env:SCA_ADDON_HOME 'SCA_ADDON_HOME' }
    if ($env:SCA_RUNTIME_DIR) { Assert-ScaUsdInstallPath $env:SCA_RUNTIME_DIR 'SCA_RUNTIME_DIR' }
    $nativeArguments = New-Object 'System.Collections.Generic.List[string]'
    if ($ScaPython) {
        if (-not (Test-Path -LiteralPath $ScaPython -PathType Leaf)) { throw "SCA host Python does not exist: $ScaPython" }
        $executable = (Resolve-Path -LiteralPath $ScaPython).ProviderPath
        foreach ($value in @('-I', '-B', '-m', 'simplecadapi.cli')) { $nativeArguments.Add($value) }
    }
    else {
        if (-not $ScaCommand) { $ScaCommand = 'sca' }
        $resolvedCommand = Get-Command $ScaCommand -CommandType Application -ErrorAction Stop | Select-Object -First 1
        $executable = $resolvedCommand.Source
        if ([System.IO.Path]::GetExtension($executable) -ieq '.cmd') {
            # SCA init writes a shim. Read its literal target; never execute a
            # shell script or reconstruct a command from its unrestricted text.
            $targets = @(Get-Content -LiteralPath $executable | ForEach-Object {
                if ($_ -match '^\s*@?"([^"\r\n]+\.exe)"\s+%\*\s*$') { $Matches[1] }
            })
            if ($targets.Count -ne 1 -or [System.IO.Path]::GetFileName($targets[0]) -ine 'sca.exe' -or -not (Test-Path -LiteralPath $targets[0] -PathType Leaf)) {
                throw 'This .cmd file is not a recognized SCA init shim. Pass the SCA host interpreter with -ScaPython, or an actual sca.exe with -ScaCommand.'
            }
            $executable = $targets[0]
        }
        if ([System.IO.Path]::GetExtension($executable) -ine '.exe') {
            throw 'Expected sca.exe or its SCA init shim. Use -ScaPython for an explicit interpreter.'
        }
    }
    foreach ($value in @('addon', 'use')) { $nativeArguments.Add($value) }
    if ($AddonHome) { $nativeArguments.Add('--home'); $nativeArguments.Add($AddonHome) }
    if ($SkillsDir) { $nativeArguments.Add('--skills-dir'); $nativeArguments.Add($SkillsDir) }
    foreach ($value in @('sca-usd-export', 'argv-json-base64', $token)) { $nativeArguments.Add($value) }
    $exitCode = Start-ScaUsdNativeProcess $executable $nativeArguments.ToArray()
    exit $exitCode
}
catch {
    [Console]::Error.WriteLine('sca-usd-export wrapper: ' + $_.Exception.Message)
    exit 2
}
