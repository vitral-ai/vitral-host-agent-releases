[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateLength(1, 149)]
    [ValidatePattern('^host-agent-publish-v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.+-]+)?$')]
    [string]$Tag,
    [string]$AssetsDir,
    [string]$BinDir = (Join-Path $env:LOCALAPPDATA 'Vitral\bin')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = 'https://github.com/vitral-ai/vitral-host-agent-releases'
$identity = "$repo/.github/workflows/sign-host-release.yml@refs/tags/$Tag"
$cosign = (Get-Command cosign -CommandType Application).Source

$architectures = @(Get-CimInstance -ClassName Win32_Processor |
    Select-Object -ExpandProperty Architecture -Unique)
if ($architectures.Count -ne 1) { throw 'Could not determine the Windows CPU architecture.' }
switch ($architectures[0]) {
    9 { $arch = 'amd64' }
    12 { $arch = 'arm64' }
    default { throw 'This Windows architecture has no Vitral host release.' }
}
$binary = "vitral-host-windows-$arch.exe"
$expected = @(
    'vitral-host-darwin-amd64',
    'vitral-host-darwin-arm64',
    'vitral-host-linux-amd64',
    'vitral-host-linux-arm64',
    'vitral-host-windows-amd64.exe',
    'vitral-host-windows-arm64.exe'
)

$work = Join-Path ([IO.Path]::GetTempPath()) "vitral-host-$([guid]::NewGuid())"
$null = New-Item -ItemType Directory -Path $work
$installTemp = $null
$oldTuf = $env:TUF_ROOT
function Get-ReleaseAsset([string]$Name, [long]$Limit) {
    $target = Join-Path $work $Name
    if ($AssetsDir) {
        $source = Get-Item -LiteralPath (Join-Path $AssetsDir $Name)
        if ($source.PSIsContainer -or ($source.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $source.Length -lt 1 -or $source.Length -gt $Limit) {
            throw "Invalid release asset: $Name"
        }
        Copy-Item -LiteralPath $source.FullName -Destination $target
    }
    else {
        & curl.exe --fail --silent --show-error --location --proto '=https' --proto-redir '=https' `
            --max-time 180 --max-filesize $Limit --output $target "$repo/releases/download/$Tag/$Name"
        if ($LASTEXITCODE -ne 0) { throw "Could not download required release asset: $Name" }
    }
    $size = (Get-Item -LiteralPath $target).Length
    if ($size -lt 1 -or $size -gt $Limit) { throw "Invalid release asset size: $Name" }
    return $target
}

try {
    $verifyOptions = @()
    if ($Tag -cmatch '^host-agent-publish-v0\.0\.0-dryrun\.[0-9]+$') {
        if (!$AssetsDir) { throw 'Pass authenticated draft assets with -AssetsDir for a dry run.' }
        $env:TUF_ROOT = Join-Path $work 'tuf'
        & $cosign initialize --staging
        if ($LASTEXITCODE -ne 0) { throw 'Cosign staging initialization failed.' }
        $root = Join-Path $env:TUF_ROOT 'tuf-repo-cdn.sigstage.dev\targets\trusted_root.json'
        if (!(Test-Path -LiteralPath $root -PathType Leaf)) { throw 'Missing Sigstore staging trust root.' }
        $verifyOptions = @('--trusted-root', $root, '--use-signed-timestamps')
    }
    $manifest = Get-ReleaseAsset 'SHA256SUMS' 8192
    $bundle = Get-ReleaseAsset 'SHA256SUMS.sigstore.json' 1048576
    & $cosign verify-blob @verifyOptions --bundle $bundle --certificate-identity $identity `
        --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' $manifest
    if ($LASTEXITCODE -ne 0) { throw 'Vitral release signature verification failed.' }

    $lines = [IO.File]::ReadAllLines($manifest)
    if ($lines.Length -ne $expected.Count) { throw 'Incomplete signed checksum manifest.' }
    $checksum = $null
    for ($index = 0; $index -lt $expected.Count; $index++) {
        if ($lines[$index] -cnotmatch '^([a-f0-9]{64})  (.+)$' -or $Matches[2] -cne $expected[$index]) {
            throw 'Invalid signed checksum manifest.'
        }
        if ($expected[$index] -ceq $binary) { $checksum = $Matches[1] }
    }
    $file = Get-ReleaseAsset $binary 134217728
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -cne $checksum) {
        throw 'Vitral binary checksum verification failed.'
    }
    $directory = [IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($BinDir)).FullName
    $destination = Join-Path $directory 'vitral-host.exe'
    $installTemp = Join-Path $directory ".vitral-host-$([guid]::NewGuid()).tmp"
    [IO.File]::Copy($file, $installTemp)
    if ([IO.File]::Exists($destination)) { [IO.File]::Replace($installTemp, $destination, [NullString]::Value) }
    else { [IO.File]::Move($installTemp, $destination) }
    Write-Output "Verified and installed $destination"
}
finally {
    $env:TUF_ROOT = $oldTuf
    if ($installTemp -and [IO.File]::Exists($installTemp)) { [IO.File]::Delete($installTemp) }
    Remove-Item -LiteralPath $work -Recurse -Force
}
