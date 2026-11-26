[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateLength(1, 149)]
    [ValidatePattern('^host-agent-publish-v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.+-]+)?$')]
    [string]$Tag,
    [Parameter(Mandatory = $true)][string]$Api,
    [Parameter(Mandatory = $true)][string]$Registry,
    [Parameter(Mandatory = $true)][string]$DiscoveryUrl,
    [Parameter(Mandatory = $true)][string]$ClientId,
    [string]$AssetsDir,
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'Vitral\guided-setup')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Api -notlike 'https://*' -or $Registry -notlike 'https://*' -or $DiscoveryUrl -notlike 'https://*') {
    throw 'Use the HTTPS addresses supplied by your Vitral administrator.'
}
$architectures = @(Get-CimInstance -ClassName Win32_Processor |
    Select-Object -ExpandProperty Architecture -Unique)
if ($architectures.Count -ne 1) { throw 'Could not determine the Windows CPU architecture.' }
switch ($architectures[0]) {
    9 { $arch = 'amd64' }
    12 { $arch = 'arm64' }
    default { throw 'This Windows architecture has no Vitral setup package.' }
}
if ($DataDir -notmatch '^[A-Za-z]:\\') { throw 'Use an absolute local data directory.' }
$root = [IO.Path]::GetFullPath($DataDir)
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$adminSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$allowed = @($currentSid.Value, $systemSid.Value, $adminSid.Value)
if (-not (Test-Path -LiteralPath $root)) {
    $null = New-Item -ItemType Directory -Path $root -Force
    $private = [Security.AccessControl.DirectorySecurity]::new()
    $private.SetOwner($currentSid)
    $private.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($currentSid, $systemSid)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $private.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $root -AclObject $private
}
$location = Get-Item -LiteralPath $root
if (-not $location.PSIsContainer -or ($location.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Choose a normal local directory for setup data.'
}
$acl = Get-Acl -LiteralPath $root
if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $allowed) {
    throw 'The setup data directory belongs to another account.'
}
foreach ($ace in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
    if ($ace.AccessControlType -eq 'Allow' -and $ace.IdentityReference.Value -notin $allowed) {
        throw 'Choose a setup data directory private to your Windows account.'
    }
}
$work = Join-Path $root ("stage-" + [Guid]::NewGuid().ToString('N'))
$pins = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'setup-dependencies.tsv') |
    Where-Object { $_ -notmatch '^#' -and $_.Trim() } |
    ConvertFrom-Csv -Delimiter "`t" -Header Platform, Dependency, SHA256, Bytes, URL

function Get-VerifiedDependency([string]$Name, [string]$Platform) {
    $matches = @($pins | Where-Object { $_.Dependency -ceq $Name -and $_.Platform -ceq $Platform })
    if ($matches.Count -ne 1) { throw "Missing verified dependency $Name on $Platform." }
    $pin = $matches[0]
    if ($pin.SHA256 -cnotmatch '^[0-9a-f]{64}$' -or $pin.Bytes -notmatch '^[1-9][0-9]+$' -or
        $pin.URL -cnotlike 'https://*') {
        throw "Invalid verification pin for $Name."
    }
    $destination = Join-Path $root "$Name-$($pin.SHA256)"
    if (Test-Path -LiteralPath $destination) {
        $existing = Get-Item -LiteralPath $destination
        if (-not $existing.PSIsContainer -or ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "The existing $Name dependency has an unexpected location."
        }
        if ($Name -eq 'cosign') {
            $binary = Get-Item -LiteralPath (Join-Path $destination 'cosign.exe')
            if ($binary.PSIsContainer -or ($binary.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                @(Get-ChildItem -LiteralPath $destination -Force).Count -ne 1 -or
                (Get-FileHash -Algorithm SHA256 -LiteralPath $binary.FullName).Hash -ine $pin.SHA256) {
                throw 'The retained Cosign failed verification.'
            }
            return $destination
        }
    }
    Write-Host "Downloading verified $Name"
    $download = Join-Path $work "$Name.download"
    & curl.exe --fail --silent --show-error --location --proto '=https' --proto-redir '=https' `
        --max-time 180 --max-filesize $pin.Bytes --output $download $pin.URL
    if ($LASTEXITCODE -ne 0 -or (Get-Item -LiteralPath $download).Length -ne [long]$pin.Bytes -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $download).Hash -ine $pin.SHA256) {
        throw "Verification failed for $Name."
    }
    $staging = Join-Path $work $Name
    $null = New-Item -ItemType Directory -Path $staging
    if ($Name -eq 'cosign') {
        Move-Item -LiteralPath $download -Destination (Join-Path $staging 'cosign.exe')
    } else {
        $extract = @('-xzf', $download, '-C', $staging)
        if ($Name -eq 'csl') { $extract += '--strip-components=1' }
        & tar.exe @extract
        if ($LASTEXITCODE -ne 0) { throw "Could not extract $Name." }
    }
    if (Test-Path -LiteralPath $destination) {
        $verifier = $verifiedPython
        if ($Name -eq 'python') { $verifier = Join-Path $staging 'python\python.exe' }
        & $verifier -I -S -B (Join-Path $PSScriptRoot 'verify-setup-tree.py') $staging $destination
        if ($LASTEXITCODE -ne 0) { throw "The retained $Name dependency failed verification." }
    } else {
        [IO.Directory]::Move($staging, $destination)
    }
    return $destination
}

$verifiedPython = $null
$oldPath = $env:PATH
$oldApi = $env:VITRAL_API
$null = New-Item -ItemType Directory -Path $work -Force
try {
    # The upstream Cosign release provides a Windows x64 verifier. Windows 11
    # on ARM can run it under x64 emulation while installing a native ARM host.
    $cosign = Get-VerifiedDependency 'cosign' 'windows-amd64'
    $env:PATH = "$cosign;$env:PATH"
    $hostDir = Join-Path $root $Tag
    $install = @{Tag=$Tag; BinDir=$hostDir}
    if ($AssetsDir) { $install.AssetsDir = $AssetsDir }
    & (Join-Path $PSScriptRoot 'install.ps1') @install
    $hostExe = Join-Path $hostDir 'vitral-host.exe'
    $probe = Start-Process -FilePath $hostExe -ArgumentList 'setup','--help' -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $work 'setup.stdout') -RedirectStandardError (Join-Path $work 'setup.stderr')
    if ($probe.ExitCode -ne 0) { throw 'This signed host release does not support guided setup.' }

    $pythonDir = Get-VerifiedDependency 'python' "windows-$arch"
    $python = Join-Path $pythonDir 'python\python.exe'
    $verifiedPython = $python
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Python was not installed.' }
    $source = Get-VerifiedDependency 'csl' 'all'
    if (-not (Test-Path -LiteralPath (Join-Path $source 'csl_sync.py') -PathType Leaf)) {
        throw 'The verified CSL source is incomplete.'
    }
    $env:VITRAL_API = $Api
    & $hostExe setup --collector-home (Join-Path $root 'collector') --collector-runtime $python `
        --collector-source $source --registry $Registry --discovery-url $DiscoveryUrl --client-id $ClientId
    if ($LASTEXITCODE -ne 0) { throw 'Guided Vitral setup did not complete.' }
} finally {
    $env:PATH = $oldPath
    $env:VITRAL_API = $oldApi
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
