# Shared native-process and data-only argv helpers. Windows PowerShell 5.1.

function Assert-ScaUsdInstallPath {
    param([Parameter(Mandatory = $true)][string]$Path, [string]$Label = 'Install path')
    # SCA 2.2.4 embeds these paths into a cmd /c prefix before this script runs.
    # This restriction applies to installation roots, never to CAD/output paths.
    if ($Path -match '[''"&|<>^%!()\r\n]') {
        throw "$Label contains an unsupported quote or cmd metacharacter. Choose an installation/runtime root containing none of these characters: apostrophe, double quote, ampersand, pipe, angle brackets, caret, percent, exclamation, parentheses, or newlines. CAD/input/output paths use encoded argv and have no such restriction."
    }
}

function ConvertTo-ScaUsdNativeArgument {
    param([AllowEmptyString()][string]$Value)
    # Microsoft CRT escaping for ProcessStartInfo.Arguments; no shell is used.
    # ArgumentList is unavailable on Windows PowerShell 5.1 / .NET Framework.
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * ($slashes * 2 + 1)))
            [void]$builder.Append('"')
        }
        else {
            [void]$builder.Append(('\' * $slashes))
            [void]$builder.Append($character)
        }
        $slashes = 0
    }
    [void]$builder.Append(('\' * ($slashes * 2)))
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Start-ScaUsdNativeProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [AllowEmptyCollection()][string[]]$ArgumentList = @(),
        [hashtable]$Environment = @{}
    )
    $location = Get-Location
    if ($location.Provider.Name -ne 'FileSystem') {
        throw 'Run this command from a filesystem working directory.'
    }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.WorkingDirectory = $location.ProviderPath
    $info.Arguments = (($ArgumentList | ForEach-Object { ConvertTo-ScaUsdNativeArgument $_ }) -join ' ')
    foreach ($key in $Environment.Keys) { $info.EnvironmentVariables[$key] = [string]$Environment[$key] }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw "Could not start $Executable" }
        # Copy bytes asynchronously: no runspace callbacks, no pipe deadlock,
        # no re-encoding of UTF-8 JSON, and live pip/export progress is retained.
        $stdoutTask = $process.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())
        $stderrTask = $process.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())
        $process.WaitForExit()
        [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask))
        return $process.ExitCode
    }
    finally { $process.Dispose() }
}

function ConvertFrom-ScaUsdEncodedArguments {
    param([Parameter(Mandatory = $true)][string]$Token)
    if ($Token.Length -gt 6000 -or $Token -notmatch '^[A-Za-z0-9+/]+={0,2}$') {
        throw 'Invalid encoded argv: expected one Base64 token of at most 6000 characters.'
    }
    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
        $json = $utf8.GetString([Convert]::FromBase64String($Token))
        if (-not $json.TrimStart().StartsWith('[') -or -not $json.TrimEnd().EndsWith(']')) {
            throw 'The JSON root must be an array.'
        }
        # Windows PowerShell 5.1 emits the JSON array as ONE pipeline object;
        # wrapping that command in @() would add a spurious nested array.
        $values = ConvertFrom-Json -InputObject $json -ErrorAction Stop
        if ($values -isnot [System.Array]) { throw 'The JSON root must be an array.' }
        if ($values.Count -eq 0 -or $values.Count -gt 256) {
            throw 'Expected between 1 and 256 command arguments.'
        }
        foreach ($value in $values) {
            if ($value -isnot [string] -or $value.IndexOf([char]0) -ge 0) {
                throw 'Every JSON argument must be a string without NUL characters.'
            }
        }
        if ($values[0] -cnotin @('export', 'validate', 'history', 'preview', 'check', 'provision', '--help', '-h')) {
            throw 'Expected an addon CLI command, --help, or provision as the first argument.'
        }
        return ,$values
    }
    catch { throw "Invalid encoded argv: $($_.Exception.Message)" }
}

function ConvertTo-ScaUsdEncodedArguments {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Values)
    $json = ConvertTo-Json -InputObject @($Values) -Compress
    $token = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))
    $null = ConvertFrom-ScaUsdEncodedArguments $token
    return $token
}
