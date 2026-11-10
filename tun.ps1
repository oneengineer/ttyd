# Windows 10 (1809+) / Windows 11, PowerShell 5.1+. No administrator required.
# Keep this file with tun.sh when using Git Bash. WSL runs tun.sh directly.
$ErrorActionPreference = 'Stop'

function Initialize-Context {
    $script:OnWindows = [Environment]::OSVersion.Platform -eq 'Win32NT'
    $script:Self = $PSCommandPath
    $script:PowerShellExe = (Get-Process -Id $PID).Path
    $script:ProfileDir = [Environment]::GetFolderPath('UserProfile')
    $script:HostId = [Environment]::MachineName -replace '[^A-Za-z0-9_.-]', '_'
    $script:DefaultSession = $script:HostId.Substring(0, [Math]::Min(64, $script:HostId.Length))
    if ($script:DefaultSession -notmatch '^[A-Za-z0-9]') { $script:DefaultSession = 'host-' + $script:DefaultSession.Substring(0, [Math]::Min(59, $script:DefaultSession.Length)) }
    $script:BinDir = Get-Setting 'HERDR_WEB_BIN' (Join-Path $script:ProfileDir '.local/bin')
    $script:DataDir = Get-Setting 'HERDR_WEB_DATA' (Join-Path $script:ProfileDir '.local/share/herdr-web-quick-tunnel')
    $script:StateDir = Get-Setting 'HERDR_WEB_STATE' (Join-Path $script:ProfileDir ".cache/herdr-web-quick-tunnel/$script:HostId")
    $script:Port = Get-Setting 'HERDR_WEB_PORT' '7681'
    $script:BrowserUser = Get-Setting 'HERDR_WEB_USER' 'herdr'
    $script:Commit = '2922cb89f518bae4d0fcf4d757a7419638fc71fc'
    $script:FrontendSha = '7834c368b7e4eb1a98c7a765e6ec1403e9ad4112477c0f122b70582492e798e3'
    $script:IndexFile = Join-Path $script:DataDir "ttyd-$script:Commit.index.html"
    if ($script:OnWindows) {
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        $global:OutputEncoding = [Console]::OutputEncoding
    }
}
function Get-Setting($Name, $Fallback) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { return $Fallback }
    return $value
}
function Read-State($Name) {
    $path = Join-Path $script:StateDir $Name
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { $stream = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite,Delete') }
    catch [IO.FileNotFoundException] { return $null }
    $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8)
    try { return $reader.ReadToEnd() | ConvertFrom-Json }
    finally { $reader.Dispose(); $stream.Dispose() }
}
function Write-State($Name, $Value) {
    $path = Join-Path $script:StateDir $Name
    $temp = "$path.$PID.tmp"
    [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 8 -Compress), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $path) { [IO.File]::Replace($temp, $path, [System.Management.Automation.Language.NullString]::Value) }
    else { [IO.File]::Move($temp, $path) }
}
function Remove-State($Name) {
    $path = Join-Path $script:StateDir $Name
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
function Open-Lock($Name) {
    try { return [IO.File]::Open((Join-Path $script:StateDir $Name), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw '另一项操作或通道正在运行，请先查看 status 或执行 stop。' }
}
function Initialize-State {
    $null = New-Item -ItemType Directory -Path $script:StateDir -Force
    if ($script:OnWindows) {
        # Protect generated credentials without changing the user's other files.
        $acl = [Security.AccessControl.DirectorySecurity]::new()
        $acl.SetAccessRuleProtection($true, $false)
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $script:StateDir -AclObject $acl
    }
}
function Get-Identity($Process) {
    return @{ Id = $Process.Id; Started = $Process.StartTime.ToUniversalTime().Ticks.ToString() }
}
function Find-RecordedProcess($Record) {
    if ($null -eq $Record -or [string]$Record.Id -notmatch '^[1-9][0-9]*$' -or -not $Record.Started) { return $null }
    $proc = Get-Process -Id ([int]$Record.Id) -ErrorAction SilentlyContinue
    if ($null -ne $proc) {
        try { if (-not $proc.HasExited -and $proc.StartTime.ToUniversalTime().Ticks.ToString() -eq [string]$Record.Started) { return $proc } }
        catch { return $null }
    }
    return $null
}
function Stop-Identity($Record) {
    $proc = Find-RecordedProcess $Record
    if ($null -ne $proc) {
        try { $proc.Kill(); $null = $proc.WaitForExit(5000) }
        catch { if (Find-RecordedProcess $Record) { throw } }
    }
}
function Find-Tool($Name) {
    if ($Name -eq 'ttyd') {
        $override = [Environment]::GetEnvironmentVariable('HERDR_WEB_TTYD')
        if (-not [string]::IsNullOrWhiteSpace($override)) {
            if (-not (Test-Path -LiteralPath $override -PathType Leaf)) { throw "HERDR_WEB_TTYD 不是可执行文件：$override" }
            return $override
        }
        $patched = Join-Path $script:BinDir 'ttyd-herdr-auth.exe'
        if (Test-Path -LiteralPath $patched -PathType Leaf) { return $patched }
    }
    $candidate = Join-Path $script:BinDir "$Name.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    $cmd = Get-Command "$Name.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    if ($Name -eq 'herdr') {
        $herdrHome = Get-Setting 'HERDR_HOME' (Join-Path $script:ProfileDir '.herdr')
        $paths = @((Join-Path $herdrHome 'packages/standalone/current/herdr.exe'))
        if ($env:LOCALAPPDATA) { $paths += Join-Path $env:LOCALAPPDATA 'Programs/Herdr/bin/herdr.exe' }
        foreach ($candidate in $paths) { if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate } }
    }
    return $null
}
function Quote-Argument([string]$Value) {
    # Windows CommandLineToArgvW quoting, including paths ending in backslashes.
    return '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}
function Start-Detached($File, [string[]]$Arguments, $LogName, $WorkDir) {
    $options = @{
        FilePath = $File; ArgumentList = (($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
        WorkingDirectory = $WorkDir; PassThru = $true
        RedirectStandardOutput = Join-Path $script:StateDir "$LogName.out.log"
        RedirectStandardError = Join-Path $script:StateDir "$LogName.log"
    }
    if ($script:OnWindows) { $options.WindowStyle = 'Hidden' }
    return Start-Process @options
}
function Get-HttpCode($TargetPort, $Credential = '') {
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$TargetPort/")
    $request.Proxy = $null
    $request.Timeout = 1200
    $request.AllowAutoRedirect = $false
    if ($Credential) { $request.Headers['Authorization'] = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Credential)) }
    try { $response = $request.GetResponse(); try { return [int]$response.StatusCode } finally { $response.Close() } }
    catch [Net.WebException] {
        if ($_.Exception.Response) { $response = $_.Exception.Response; try { return [int]$response.StatusCode } finally { $response.Close() } }
        return 0
    }
}
function Test-PortFree {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, [int]$script:Port)
    try { $listener.Start() }
    catch { throw "端口 $script:Port 已被占用，请先关闭旧服务或设置 HERDR_WEB_PORT。" }
    finally { $listener.Stop() }
}
function Download-File($Url, $Destination) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination -TimeoutSec 180
}
function Install-Dependencies {
    $null = New-Item -ItemType Directory -Path $script:BinDir, $script:DataDir -Force
    if (-not (Find-Tool 'ttyd')) {
        $temp = Join-Path $script:BinDir "ttyd.$PID.tmp.exe"
        Download-File 'https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.win32.exe' $temp
        if ((Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash -ne 'e33a27501b10b96981335bcba938b1145c7f52551a343e72160f00ab71832b37') { throw 'ttyd 文件校验失败。' }
        Move-Item -LiteralPath $temp -Destination (Join-Path $script:BinDir 'ttyd.exe') -Force
    }
    if (-not (Find-Tool 'cloudflared')) {
        $temp = Join-Path $script:BinDir "cloudflared.$PID.tmp.exe"
        Download-File 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' $temp
        Move-Item -LiteralPath $temp -Destination (Join-Path $script:BinDir 'cloudflared.exe') -Force
    }
    $probe = New-Object Diagnostics.Process
    try {
        $probe.StartInfo = New-Object Diagnostics.ProcessStartInfo
        $probe.StartInfo.FileName = Find-Tool 'ttyd'
        $probe.StartInfo.Arguments = '--help'
        $probe.StartInfo.UseShellExecute = $false
        $probe.StartInfo.RedirectStandardOutput = $true
        $probe.StartInfo.RedirectStandardError = $true
        $probe.StartInfo.CreateNoWindow = $true
        [void]$probe.Start()
        $stdout = $probe.StandardOutput.ReadToEndAsync()
        $stderr = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(5000)) { $probe.Kill(); throw 'ttyd 功能检测超时。' }
        $script:ImageUpload = ($stdout.Result + $stderr.Result).Contains('--image-upload')
    } finally { $probe.Dispose() }
    $hasFrontend = (Test-Path -LiteralPath $script:IndexFile) -and ([IO.File]::ReadAllText($script:IndexFile).Contains('registerOscHandler(52'))
    if (-not $script:ImageUpload -and -not $hasFrontend) {
        $header = Join-Path $script:StateDir 'frontend.h'
        Download-File "https://raw.githubusercontent.com/tsl0922/ttyd/$script:Commit/src/html.h" $header
        if ((Get-FileHash -LiteralPath $header -Algorithm SHA256).Hash -ne $script:FrontendSha) { throw 'ttyd 剪贴板前端校验失败。' }
        $array = [regex]::Match([IO.File]::ReadAllText($header), '(?s)unsigned char index_html\[\].*?\{(.*?)\};')
        if (-not $array.Success) { throw 'ttyd 前端格式异常。' }
        $bytes = [Collections.Generic.List[byte]]::new()
        foreach ($hex in [regex]::Matches($array.Groups[1].Value, '0x([0-9a-fA-F]{2})')) { $bytes.Add([Convert]::ToByte($hex.Groups[1].Value, 16)) }
        $inputStream = [IO.MemoryStream]::new($bytes.ToArray(), $false)
        $gzip = [IO.Compression.GZipStream]::new($inputStream, [IO.Compression.CompressionMode]::Decompress)
        $outputStream = [IO.MemoryStream]::new()
        try { $gzip.CopyTo($outputStream); $html = $outputStream.ToArray() }
        finally { $gzip.Dispose(); $inputStream.Dispose(); $outputStream.Dispose() }
        if (-not [Text.Encoding]::UTF8.GetString($html).Contains('registerOscHandler(52')) { throw '前端缺少剪贴板支持。' }
        [IO.File]::WriteAllBytes("$script:IndexFile.tmp", $html)
        Move-Item -LiteralPath "$script:IndexFile.tmp" -Destination $script:IndexFile -Force
    }
    if (-not (Find-Tool 'herdr')) {
        $installer = Join-Path $script:StateDir 'install-herdr.ps1'
        Download-File 'https://herdr.dev/install.ps1' $installer
        & $script:PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0 -or -not (Find-Tool 'herdr')) { throw 'Herdr 安装失败，请查看 supervisor.log。' }
    }
}
function Get-WebClients($TtydRecord) {
    if (-not (Find-RecordedProcess $TtydRecord)) { return }
    # Only direct Herdr clients belong to ttyd. Never walk/kill the server tree.
    foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($TtydRecord.Id)")) {
        # The installer uses junction aliases; CIM may report the resolved path.
        if ($child.ExecutablePath -and $child.Name -eq [IO.Path]::GetFileName($TtydRecord.ClientPath)) {
            $proc = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            if ($proc -and $proc.StartTime.ToUniversalTime().Ticks -ge [long]$TtydRecord.Started) { Get-Identity $proc }
        }
    }
}
function Save-WebClients {
    $record = Read-State 'ttyd.json'
    if (Find-RecordedProcess $record) { Write-State 'clients.json' @{ Items = @(Get-WebClients $record) } }
}
function Stop-Components {
    Stop-Identity (Read-State 'cloudflared.json')
    Save-WebClients
    $clients = Read-State 'clients.json'
    Stop-Identity (Read-State 'ttyd.json')
    if ($clients) { foreach ($client in @($clients.Items)) { Stop-Identity $client } }
}
function Clear-Instance {
    foreach ($name in @('worker.json', 'instance.json', 'ttyd.json', 'cloudflared.json', 'clients.json', 'stop.request')) { Remove-State $name }
}
function Show-Status {
    $record = Read-State 'worker.json'
    if (-not (Find-RecordedProcess $record)) { Write-Host '当前通道未运行。'; return $false }
    $info = Read-State 'instance.json'
    if (-not $info) { Write-Host "通道正在启动，PID：$($record.Id)"; return $true }
    $healthy = (Find-RecordedProcess (Read-State 'ttyd.json')) -and (Find-RecordedProcess (Read-State 'cloudflared.json')) -and (Get-HttpCode $info.Port) -eq 401
    Write-Host "`n========== Herdr 网页终端 =========="
    if ($healthy) { Write-Host '状态：后台进程和本机 ttyd 正常（未探测公网）' }
    else { Write-Host '状态：后台仍在，但服务检查异常；请查看 logs。' }
    Write-Host "地址：$($info.Url)`nSession：$($info.Session)`n目录：$($info.WorkDir)`nPID：$($record.Id)`n日志：$script:StateDir"
    Write-Host '-----------------------------------'
    Write-Host "用户名：$($info.Username)`n密码：$($info.Password)"
    return [bool]$healthy
}
function Validate-Start($Name) {
    if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') { throw 'Session 名请用字母、数字、点、下划线或横线，最长 64 字符。' }
    if ($script:Port -notmatch '^[1-9][0-9]{0,4}$' -or [int]$script:Port -gt 65535) { throw 'HERDR_WEB_PORT 必须是 1～65535。' }
    if ($script:BrowserUser -notmatch '^[A-Za-z0-9._-]{1,64}$') { throw 'HERDR_WEB_USER 格式错误。' }
}
function Invoke-Worker($RequestPath) {
    $request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Remove-Item -LiteralPath $RequestPath -Force
    $guard = Open-Lock 'worker.guard'
    $owns = $false
    $ready = $false
    try {
        if (Find-RecordedProcess (Read-State 'worker.json')) { throw '已有通道，请先 stop。' }
        $owns = $true
        Clear-Instance
        Write-State 'worker.json' (Get-Identity (Get-Process -Id $PID))
        Set-Location -LiteralPath $request.WorkDir
        Install-Dependencies
        Test-PortFree
        $passwordBytes = New-Object byte[] 16
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($passwordBytes) } finally { $rng.Dispose() }
        $password = ([BitConverter]::ToString($passwordBytes)).Replace('-', '').ToLowerInvariant()
        $herdr = Find-Tool 'herdr'
        # Native ttyd launches Herdr directly, so disconnect terminates that client.
        foreach ($key in @('HERDR_SESSION','HERDR_SOCKET_PATH','HERDR_ENV','HERDR_PANE_ID','HERDR_TAB_ID','HERDR_WORKSPACE_ID')) { Remove-Item -LiteralPath "Env:$key" -ErrorAction SilentlyContinue }
        $env:SSH_TTY = 'ttyd'
        $terminalArgs = @('-i','127.0.0.1','-p',$script:Port,'-W','-s','15')
        if ($script:ImageUpload) { $terminalArgs += '--image-upload' }
        else { $terminalArgs += @('-I',$script:IndexFile) }
        $terminalArgs += @('-t','scrollback=10000','-c',"$($script:BrowserUser):$password",$herdr)
        if ($request.Session -ne 'default') { $terminalArgs += @('--session', $request.Session) }
        $ttyd = Start-Detached (Find-Tool 'ttyd') $terminalArgs 'ttyd' $request.WorkDir
        $ttydRecord = Get-Identity $ttyd
        $ttydRecord.ClientPath = $herdr
        Write-State 'ttyd.json' $ttydRecord
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        do {
            if (-not (Find-RecordedProcess $ttydRecord)) { throw 'ttyd 启动失败，请查看 ttyd.log。' }
            $code = Get-HttpCode $script:Port "$($script:BrowserUser):$password"
            if ($code -eq 200) { break }
            Start-Sleep -Milliseconds 250
        } while ([DateTime]::UtcNow -lt $deadline)
        if ($code -ne 200) { throw 'ttyd 本机检查失败。' }
        $config = Join-Path $script:StateDir 'tunnel.yml'
        [IO.File]::WriteAllText($config, "loglevel: info`nmetrics: 127.0.0.1:0`n", [Text.UTF8Encoding]::new($false))
        $cf = Start-Detached (Find-Tool 'cloudflared') @('tunnel','--config',$config,'--no-autoupdate','--url',"http://127.0.0.1:$script:Port") 'cloudflared' $request.WorkDir
        $cfRecord = Get-Identity $cf
        Write-State 'cloudflared.json' $cfRecord
        $deadline = [DateTime]::UtcNow.AddSeconds(60)
        $url = ''
        do {
            if (-not (Find-RecordedProcess $ttydRecord) -or -not (Find-RecordedProcess $cfRecord)) { throw '服务启动失败，请查看 logs。' }
            $log = (Get-Content -LiteralPath (Join-Path $script:StateDir 'cloudflared.log') -Raw -ErrorAction SilentlyContinue) + (Get-Content -LiteralPath (Join-Path $script:StateDir 'cloudflared.out.log') -Raw -ErrorAction SilentlyContinue)
            $match = [regex]::Match([string]$log, 'https://[a-z0-9-]+\.trycloudflare\.com')
            if ($match.Success -and $log.Contains('Registered tunnel connection')) { $url = $match.Value; break }
            Start-Sleep -Milliseconds 250
        } while ([DateTime]::UtcNow -lt $deadline)
        if (-not $url) { throw '60 秒内未建立 Cloudflare 连接。' }
        Write-State 'instance.json' @{ Session = $request.Session; Url = $url; Username = $script:BrowserUser; Password = $password; WorkDir = $request.WorkDir; Port = $script:Port }
        Write-State $request.Result @{ Ready = $true }
        $ready = $true
        while (-not (Test-Path -LiteralPath (Join-Path $script:StateDir 'stop.request'))) {
            if (-not (Find-RecordedProcess $ttydRecord) -or -not (Find-RecordedProcess $cfRecord)) { throw 'ttyd 或 Cloudflare 已退出；没有自动重启。' }
            Save-WebClients
            Start-Sleep -Milliseconds 500
        }
    } catch {
        if (-not $ready) { Write-State $request.Result @{ Ready = $false; Error = $_.Exception.Message } }
        throw
    } finally {
        try { if ($owns) { Stop-Components; Clear-Instance } }
        finally { $guard.Dispose() }
    }
}
function Stop-Tunnel {
    $record = Read-State 'worker.json'
    if (Find-RecordedProcess $record) {
        [IO.File]::WriteAllText((Join-Path $script:StateDir 'stop.request'), 'stop')
        $deadline = [DateTime]::UtcNow.AddSeconds(12)
        while ((Find-RecordedProcess $record) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
        if (Find-RecordedProcess $record) { Stop-Components; Stop-Identity $record }
    }
    # Take the runtime lock before recovering recorded orphans.
    $guard = Open-Lock 'worker.guard'
    try { Stop-Components; Clear-Instance } finally { $guard.Dispose() }
    Write-Output '通道已停止；Herdr 会话和里面的程序继续运行。'
}
function Start-Tunnel($Name) {
    Validate-Start $Name
    if (Find-RecordedProcess (Read-State 'worker.json')) { $null = Show-Status; throw '已有通道，请先执行 tun.ps1 stop。' }
    $guard = Open-Lock 'worker.guard'
    try { Stop-Components; Clear-Instance } finally { $guard.Dispose() }
    $token = [Guid]::NewGuid().ToString('N')
    $resultName = "result-$token.json"
    $requestName = "request-$token.json"
    Write-State $requestName @{ Session = $Name; WorkDir = (Get-Location).ProviderPath; Result = $resultName }
    $requestPath = Join-Path $script:StateDir $requestName
    Write-Output '正在后台启动；首次运行可能需要下载依赖……'
    $proc = Start-Detached $script:PowerShellExe @('-NoProfile','-ExecutionPolicy','Bypass','-File',$script:Self,'__worker',$requestPath) 'supervisor' (Get-Location).ProviderPath
    $worker = Get-Identity $proc
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(600)
        do {
            $outcome = Read-State $resultName
            if ($outcome -or -not (Find-RecordedProcess $worker)) { break }
            Start-Sleep -Milliseconds 250
        } while ([DateTime]::UtcNow -lt $deadline)
        if (-not $outcome -or -not $outcome.Ready) {
            if (Find-RecordedProcess $worker) { Stop-Tunnel }
            $detail = if ($outcome) { $outcome.Error } else { '后台进程退出或启动超时' }
            throw "$detail；日志：$(Join-Path $script:StateDir 'supervisor.log')"
        }
        if (-not (Show-Status)) { throw '启动后检查异常，请查看 logs。' }
    } finally { Remove-State $resultName; Remove-State $requestName }
}
function Show-Usage {
    Write-Output @'
Windows：PowerShell 5.1+，Windows 10 1809+ / Windows 11；不需要 WSL 或管理员权限。
首次运行：powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\tun.ps1" list
.\tun.ps1 list [--json]   列出本机 Herdr sessions，不启动通道
.\tun.ps1 [session]       后台启动，默认 session 为主机名
.\tun.ps1 start [session] 同上，可连接名为 list/status 的 session
.\tun.ps1 default         连接默认 Herdr 会话
.\tun.ps1 status          查看地址、用户名、密码和本机健康状况
.\tun.ps1 stop            只关闭网页通道，保留 Herdr
.\tun.ps1 restart [name]  重开通道，默认沿用当前 session
.\tun.ps1 logs            查看日志，Ctrl+C 退出查看
Linux/macOS/WSL：运行 ./tun.sh；Git Bash：与 tun.ps1 放同一目录后运行 bash tun.sh。
'@
}
function Watch-Logs {
    $positions = @{}
    $files = @(Get-ChildItem -LiteralPath $script:StateDir -Filter '*.log' -ErrorAction SilentlyContinue)
    if (-not $files.Count) { Write-Output '还没有日志。'; return }
    foreach ($file in $files) {
        $positions[$file.FullName] = $file.Length
        Write-Host "`n--- $($file.Name) ---"
        Get-Content -LiteralPath $file.FullName -Tail 30
    }
    while ($true) {
        foreach ($file in @(Get-ChildItem -LiteralPath $script:StateDir -Filter '*.log')) {
            $offset = $positions[$file.FullName]
            if ($null -eq $offset -or $file.Length -lt $offset) { $offset = 0 }
            if ($file.Length -le $offset) { continue }
            $stream = [IO.File]::Open($file.FullName, 'Open', 'Read', 'ReadWrite,Delete')
            $reader = [IO.StreamReader]::new($stream)
            try {
                $null = $stream.Seek($offset, 'Begin')
                Write-Host "`n--- $($file.Name) ---"
                Write-Host -NoNewline $reader.ReadToEnd()
                $positions[$file.FullName] = $stream.Position
            } finally { $reader.Dispose(); $stream.Dispose() }
        }
        Start-Sleep -Milliseconds 500
    }
}
function Assert-WindowsRuntime {
    if (-not $script:OnWindows) { throw 'tun.ps1 用于 Windows；Linux / Mac / WSL 请运行 tun.sh。' }
    $arch = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITEW6432')
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    if ($arch -notin @('AMD64', 'ARM64')) { throw 'Windows 需要 64 位系统；ARM64 使用官方 x64 版本并要求系统支持 x64 模拟。' }
    if ([Environment]::OSVersion.Version.Build -lt 17763) { throw 'Windows 需要 Windows 10 1809 或更高版本（ConPTY）。' }
}
function Invoke-Tun([string[]]$CommandArgs) {
    $action = if ($CommandArgs.Count) { $CommandArgs[0] } else { 'start' }
    if ($action -in @('help','-h','--help')) { Show-Usage; return }
    Assert-WindowsRuntime
    if ($action -in @('list','llist')) {
        if ($CommandArgs.Count -gt 2 -or ($CommandArgs.Count -eq 2 -and $CommandArgs[1] -notin @('--json','-json'))) { throw '用法：tun.ps1 list [--json]' }
        $herdr = Find-Tool 'herdr'
        if (-not $herdr) { throw '找不到 herdr；请先启动一次通道以安装依赖，或安装 Herdr。' }
        $listArgs = @('session','list')
        if ($CommandArgs.Count -eq 2) { $listArgs += '--json' }
        & $herdr @listArgs
        if ($LASTEXITCODE) { throw 'Herdr session list 失败。' }
        return
    }
    if ($action -eq '__worker') {
        if ($CommandArgs.Count -ne 2) { throw '内部启动参数错误。' }
        Invoke-Worker $CommandArgs[1]; return
    }
    if (($action -in @('start','restart') -and $CommandArgs.Count -gt 2) -or ($action -notin @('start','restart') -and $CommandArgs.Count -ne 1)) { throw '参数太多。' }
    if ($action -eq 'status') { if (-not (Show-Status)) { throw '通道尚未正常运行。' }; return }
    if ($action -eq 'logs') {
        Watch-Logs; return
    }
    Initialize-State
    $control = Open-Lock 'control.lock'
    try {
        if ($action -eq 'stop') { Stop-Tunnel; return }
        $name = if ($CommandArgs.Count -eq 2) { $CommandArgs[1] } elseif ($action -notin @('start','restart')) { $action } else { $script:DefaultSession }
        if ($action -eq 'restart' -and $CommandArgs.Count -lt 2) {
            $old = Read-State 'instance.json'
            if ($old) { $name = $old.Session }
        }
        Validate-Start $name
        if ($action -eq 'restart') { Stop-Tunnel }
        Start-Tunnel $name
    } finally { $control.Dispose() }
}

# Dot-sourcing exposes functions for regression tests without starting anything.
if ($MyInvocation.InvocationName -ne '.') {
    try { Initialize-Context; Invoke-Tun -CommandArgs @($args) }
    catch { [Console]::Error.WriteLine('错误：' + $_.Exception.Message); exit 1 }
}
