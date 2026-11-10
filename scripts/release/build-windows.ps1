#requires -Version 7.3
param([Parameter(Mandatory = $true)][string]$VcpkgRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
if (-not $IsWindows) { throw 'This builder requires native Windows.' }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$VcpkgRoot = (Resolve-Path $VcpkgRoot).Path
$env:VCPKG_ROOT = $VcpkgRoot
$env:VCPKG_DISABLE_METRICS = '1'
$buildRoot = Join-Path $env:RUNNER_TEMP 'ttyd-windows-build'
$installed = Join-Path $buildRoot 'installed'
$dist = Join-Path $repo 'dist'
$licenses = Join-Path $buildRoot 'licenses'
New-Item -ItemType Directory -Force $dist, $licenses, $env:VCPKG_DEFAULT_BINARY_CACHE | Out-Null

& (Join-Path $VcpkgRoot 'bootstrap-vcpkg.bat') -disableMetrics
$vcpkgExe = Join-Path $VcpkgRoot 'vcpkg.exe'
& $vcpkgExe install --triplet=x64-windows-static "--x-manifest-root=$PSScriptRoot" "--x-install-root=$installed" --clean-after-build

$cmakeBuild = Join-Path $buildRoot 'ttyd'
$toolchain = Join-Path $VcpkgRoot 'scripts/buildsystems/vcpkg.cmake'
& cmake -S $repo -B $cmakeBuild -G 'Visual Studio 17 2022' -A x64 `
    "-DCMAKE_TOOLCHAIN_FILE=$toolchain" "-DVCPKG_INSTALLED_DIR=$installed" `
    -DVCPKG_TARGET_TRIPLET=x64-windows-static -DVCPKG_MANIFEST_MODE=OFF `
    -DVCPKG_APPLOCAL_DEPS=OFF -DCMAKE_POLICY_DEFAULT_CMP0091=NEW `
    -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded -DCMAKE_C_STANDARD=11 -DCMAKE_C_STANDARD_REQUIRED=ON
& cmake --build $cmakeBuild --config Release --parallel
$binary = Join-Path $dist 'ttyd.windows-x86_64.exe'
Copy-Item (Join-Path $cmakeBuild 'Release/ttyd.exe') $binary -Force
& $binary --version

# Require system DLLs only; Visual C++ redistributable DLLs are also rejected.
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vsRoot = & $vswhere -latest -products '*' -version '[17.0,18.0)' -property installationPath
$dumpbin = Get-ChildItem "$vsRoot/VC/Tools/MSVC/*/bin/Hostx64/x64/dumpbin.exe" |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $dumpbin) { throw 'Cannot find dumpbin.exe.' }
$imports = & $dumpbin /DEPENDENTS $binary
$imports | Write-Host
$dllNames = [regex]::Matches(($imports -join "`n"), '(?im)^\s+([a-z0-9_.-]+\.dll)\s*$') |
    ForEach-Object { $_.Groups[1].Value }
if (-not $dllNames) { throw 'Cannot read executable DLL dependencies.' }
foreach ($dll in $dllNames) {
    if ($dll -match '^(vcruntime|msvcp|concrt)\d') { throw "Unexpected Visual C++ runtime dependency: $dll" }
    if ($dll -match '^(api-ms-win-|ext-ms-win-)') { continue }
    if (-not (Test-Path (Join-Path "$env:WINDIR/System32" $dll))) {
        throw "Unexpected third-party DLL dependency: $dll"
    }
}

Copy-Item (Join-Path $repo 'LICENSE') (Join-Path $licenses 'ttyd-LICENSE') -Force
Copy-Item (Join-Path $PSScriptRoot 'vcpkg.json') $licenses -Force
Copy-Item (Join-Path $installed 'vcpkg/status') (Join-Path $licenses 'VCPKG_STATUS.txt') -Force
$notices = @(Get-ChildItem (Join-Path $installed 'x64-windows-static/share') -Filter copyright -File -Recurse)
if ($notices.Count -lt 6) { throw 'Dependency license notices are missing.' }
foreach ($notice in $notices) {
    Copy-Item $notice.FullName (Join-Path $licenses ($notice.Directory.Name + '-copyright.txt')) -Force
}
Compress-Archive -Path (Join-Path $licenses '*') -DestinationPath (Join-Path $dist 'THIRD_PARTY_LICENSES_WINDOWS.zip') -Force
