#!/usr/bin/env bash
# Linux + macOS, no root. On Windows use the companion tun.ps1.
set -Eeuo pipefail
umask 077

# Git Bash/MSYS/Cygwin host native Windows Herdr; WSL hosts Linux Herdr.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    launcher="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/tun.ps1"
    [ -f "$launcher" ] || { echo 'Windows 请把 tun.ps1 与 tun.sh 放在同一个目录。' >&2; exit 1; }
    command -v cygpath >/dev/null || { echo '请在 PowerShell 中运行 .\tun.ps1。' >&2; exit 1; }
    launcher="$(cygpath -w "$launcher")"
    if command -v powershell.exe >/dev/null; then
      exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$launcher" "$@"
    elif command -v pwsh.exe >/dev/null; then
      exec pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "$launcher" "$@"
    else
      echo '未找到 Windows PowerShell，请在 PowerShell 中运行 .\tun.ps1。' >&2; exit 1
    fi ;;
esac

bin="${HERDR_WEB_BIN:-$HOME/.local/bin}"
data="${HERDR_WEB_DATA:-$HOME/.local/share/herdr-web-quick-tunnel}"
host_id="$(hostname -s 2>/dev/null || hostname)"
host_id="$(printf '%s' "$host_id" | tr -c 'A-Za-z0-9_.-' '_')"
default_session="${host_id:0:64}"
[[ "$default_session" =~ ^[A-Za-z0-9] ]] || default_session="host-${default_session:0:59}"
state="${HERDR_WEB_STATE:-$HOME/.cache/herdr-web-quick-tunnel/$host_id}"
port="${HERDR_WEB_PORT:-7681}"
user="${HERDR_WEB_USER:-herdr}"
self="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/$(basename -- "${BASH_SOURCE[0]}")"
commit=2922cb89f518bae4d0fcf4d757a7419638fc71fc
frontend_sha=7834c368b7e4eb1a98c7a765e6ec1403e9ad4112477c0f122b70582492e798e3
index="$data/ttyd-$commit.index.html"
platform="$(uname -s)"
ttyd_pid=''; cf_pid=''; result=''; owns=0

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
get() {
  [ -f "$state/instance.env" ] || return 0
  awk -F= -v k="$1" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$state/instance.env"
}
read_pid() {
  local p=''
  [ ! -f "$1" ] || IFS= read -r p < "$1" || true
  [[ "$p" =~ ^[1-9][0-9]*$ ]] && printf '%s\n' "$p"
}
alive() {
  [[ "${1:-}" =~ ^[1-9][0-9]*$ ]] || return 1
  kill -0 "$1" 2>/dev/null || return 1
  local s
  s="$(ps -p "$1" -o stat= 2>/dev/null)" || return 1
  [[ "$s" != *Z* && -n "$s" ]] || return 1
  # Darwin can retain an E (exiting) kernel record after the address space is gone.
  [ "$platform" != Darwin ] || [[ "$s" != *E* ]]
}
worker_alive() {
  local p="${1:-}" args
  alive "$p" || return 1
  args="$(ps -ww -p "$p" -o args= 2>/dev/null)" || return 1
  case "$args" in
    *"$self __worker "*|*"/tun.sh __worker "*|*"/herdr-web-quick-tunnel.sh __worker "*) return 0 ;;
  esac
  return 1
}
birth() {
  if [ -r "/proc/$1/stat" ]; then
    sed 's/^.*) //' "/proc/$1/stat" | awk '{print $20}'
  else
    ps -p "$1" -o lstart= 2>/dev/null || true
  fi
}
record_child() {
  printf '%s\n' "$2" > "$state/$1.pid"
  birth "$2" > "$state/$1.birth"
}
check_child_records() {
  local role p
  for role in ttyd cloudflared; do
    p="$(read_pid "$state/$role.pid" || true)"
    if alive "$p" && [ ! -s "$state/$role.birth" ]; then
      die "旧版 $role 进程 PID $p 仍在，缺少进程身份记录；请先用旧版脚本 stop，或核实并关闭该进程后重试。"
    fi
  done
}
stop_child() {
  local role="$1" p stamp i
  p="$(read_pid "$state/$role.pid" || true)"
  stamp="$(cat "$state/$role.birth" 2>/dev/null || true)"
  [ -n "$stamp" ] || return 0
  alive "$p" && [ "$(birth "$p")" = "$stamp" ] || return 0
  kill -TERM "$p" 2>/dev/null || true
  for i in {1..30}; do alive "$p" || return 0; sleep 0.1 9>&-; done
  [ "$(birth "$p")" != "$stamp" ] || kill -KILL "$p" 2>/dev/null || true
}
find_tool() {
  local name="$1" found
  if [ -x "$bin/$name" ]; then printf '%s\n' "$bin/$name"; return; fi
  found="$(command -v "$name" 2>/dev/null || true)"
  if [ -n "$found" ]; then printf '%s\n' "$found"; return; fi
  if [ "$platform" = Darwin ]; then
    for found in "/opt/homebrew/bin/$name" "/usr/local/bin/$name"; do
      if [ -x "$found" ]; then printf '%s\n' "$found"; return; fi
    done
  fi
  return 1
}
brew_install() {
  local brew_bin
  brew_bin="$(find_tool brew || true)"
  [ -n "$brew_bin" ] || die "Mac 缺少 $*，请先安装 Homebrew（https://brew.sh），或自行安装缺少的命令。"
  HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 \
    "$brew_bin" install "$@" 9>&-
}
prepare_runtime() {
  case "$platform" in Linux|Darwin) ;; *) die "暂不支持系统：$platform" ;; esac
  command -v curl >/dev/null || die '缺少命令：curl'
  command -v perl >/dev/null || die '缺少命令：perl（用于后台独立会话）'
  flock_bin="$(find_tool flock || true)"
  if [ -z "$flock_bin" ] && [ "$platform" = Darwin ]; then
    brew_install flock
    flock_bin="$(find_tool flock || true)"
  fi
  [ -n "$flock_bin" ] || die '缺少命令：flock'
}
sha_file() {
  if command -v sha256sum >/dev/null; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    die '文件校验需要 sha256sum 或 shasum。'
  fi
}
resolve_herdr() {
  herdr="$(find_tool herdr || true)"
  [ -n "$herdr" ] && [ -x "$herdr" ]
}
resolve_ttyd() {
  local candidate="${HERDR_WEB_TTYD:-}"
  if [ -n "$candidate" ]; then
    [ -x "$candidate" ] || die "HERDR_WEB_TTYD 不是可执行文件：$candidate"
    printf '%s\n' "$candidate"; return
  fi
  # Keep the patched web-terminal binary separate from the system ttyd.
  candidate="$bin/ttyd-herdr-auth"
  if [ -x "$candidate" ]; then printf '%s\n' "$candidate"; return; fi
  find_tool ttyd
}
list_sessions() {
  resolve_herdr || die '找不到 herdr；请在装有 Herdr 的服务器上执行。'
  "$herdr" session list "$@"
}
info() {
  local p code saved_port healthy=0
  p="$(read_pid "$state/instance.lock" || true)"
  worker_alive "$p" || { echo '当前通道未运行。'; return 1; }
  [ -n "$(get session)" ] || { echo "通道正在启动，PID：$p"; return 0; }
  saved_port="$(get port)"; saved_port="${saved_port:-$port}"
  code="$(curl -s --noproxy '*' --max-time 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$saved_port/" || true)"
  printf '\n========== Herdr 网页终端 ==========\n'
  if alive "$(read_pid "$state/ttyd.pid" || true)" &&
     alive "$(read_pid "$state/cloudflared.pid" || true)" && [ "$code" = 401 ]; then
    echo '状态：后台进程和本机 ttyd 正常（未探测公网）'
    healthy=1
  else
    echo '状态：后台仍在，但服务检查异常；请查看 logs。'
  fi
  printf '地址：%s\nSession：%s\n目录：%s\nPID：%s\n日志：%s\n' \
    "$(get public_url)" "$(get session)" "$(get started_in)" "$p" "$state"
  [ -z "$(get ttyd_path)" ] || printf 'ttyd：%s\n' "$(get ttyd_path)"
  printf '%s\n' '-----------------------------------'
  printf '用户名：%s\n密码：%s\n' "$(get username)" "$(get password)"
  [ "$healthy" = 1 ]
}
download() { curl -fL --retry 2 --connect-timeout 15 --max-time 180 "$1" -o "$2" 9>&-; }
install_deps() {
  local arch asset tmp expected actual
  for tmp in curl perl gzip; do
    command -v "$tmp" >/dev/null || die "缺少命令：$tmp"
  done
  case "$(uname -m)" in
    x86_64|amd64) arch=amd64; asset=ttyd.x86_64 ;;
    aarch64|arm64) arch=arm64; asset=ttyd.aarch64 ;;
    *) die '暂不支持这个 CPU 架构。' ;;
  esac
  mkdir -p "$bin" "$data"
  if [ "$platform" = Darwin ]; then
    if ! find_tool cloudflared >/dev/null; then brew_install cloudflared; fi
    if ! resolve_ttyd >/dev/null; then brew_install ttyd; fi
  else
    if ! find_tool cloudflared >/dev/null; then
      download "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$arch" "$bin/cloudflared.tmp.$$"
      chmod 755 "$bin/cloudflared.tmp.$$"
      mv "$bin/cloudflared.tmp.$$" "$bin/cloudflared"
    fi
    if ! resolve_ttyd >/dev/null; then
      download "https://github.com/tsl0922/ttyd/releases/download/1.7.7/$asset" "$bin/ttyd.tmp.$$"
      download 'https://github.com/tsl0922/ttyd/releases/download/1.7.7/SHA256SUMS' "$state/SHA256SUMS"
      expected="$(awk -v n="$asset" '{f=$2; sub(/^\*/, "", f); if(f==n) print $1}' "$state/SHA256SUMS")"
      actual="$(sha_file "$bin/ttyd.tmp.$$")"
      [ -n "$expected" ] && [ "$expected" = "$actual" ] || die 'ttyd 校验失败。'
      chmod 755 "$bin/ttyd.tmp.$$"
      mv "$bin/ttyd.tmp.$$" "$bin/ttyd"
    fi
  fi
  cf_bin="$(find_tool cloudflared)"
  ttyd_bin="$(resolve_ttyd)"
  ttyd_frontend=(-I "$index")
  if "$ttyd_bin" --help 2>&1 | grep -Fq -- '--image-upload'; then
    # Use the matching embedded frontend; the pinned old HTML has no image paste.
    ttyd_frontend=(--image-upload)
  elif [ ! -s "$index" ] || ! grep -aFq 'registerOscHandler(52' "$index"; then
    tmp="$state/frontend.$$"
    download "https://raw.githubusercontent.com/tsl0922/ttyd/$commit/src/html.h" "$tmp"
    actual="$(sha_file "$tmp")"
    [ "$actual" = "$frontend_sha" ] || die 'ttyd 剪贴板前端校验失败。'
    perl -ne 'if (/unsigned char index_html\[\]/ .. /^};/) {while (/0x([0-9a-fA-F]{2})/g) {print pack("C", hex($1));}}' "$tmp" > "$tmp.gz"
    gzip -dc "$tmp.gz" > "$index.tmp.$$"
    grep -aFq 'registerOscHandler(52' "$index.tmp.$$" || die '前端缺少剪贴板支持。'
    mv "$index.tmp.$$" "$index"
  fi
  if ! resolve_herdr; then
    download 'https://herdr.dev/install.sh' "$state/install-herdr.sh"
    sh "$state/install-herdr.sh" 9>&-
    resolve_herdr || die '安装后仍未找到 herdr。'
  fi
}
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [ "$owns" = 1 ]; then
    stop_child cloudflared; stop_child ttyd
    [ -z "$cf_pid" ] || wait "$cf_pid" 2>/dev/null || true
    [ -z "$ttyd_pid" ] || wait "$ttyd_pid" 2>/dev/null || true
    if [ "$(read_pid "$state/instance.lock" || true)" = "$$" ]; then
      for f in instance.lock instance.env ttyd.pid ttyd.birth cloudflared.pid cloudflared.birth; do
        [ ! -f "$state/$f" ] || unlink "$state/$f"
      done
    fi
  fi
  if [ -n "$result" ] && [ ! -s "$result" ]; then
    printf 'error:后台启动失败，请查看 %s/supervisor.log\n' "$state" > "$result"
  fi
  exit "$rc"
}
web_client() {
  [ "$#" -ge 1 ] || die '内部网页客户端缺少启动命令。'
  local client_tty
  client_tty="$(tty)" || die '网页客户端需要 ttyd 提供的终端。'
  # Route web copies through OSC 52 rather than the server's system clipboard.
  exec env -u HERDR_SESSION -u HERDR_SOCKET_PATH -u HERDR_ENV \
    -u HERDR_PANE_ID -u HERDR_TAB_ID -u HERDR_WORKSPACE_ID \
    SSH_TTY="$client_tty" "$@"
}
worker() {
  local session="$1" workdir="$2" password url='' code i p
  local args
  result="$3"
  trap cleanup EXIT
  trap 'exit 0' TERM INT
  exec 9> "$state/worker.guard"
  "$flock_bin" -n 9 || die '已有通道占用运行锁，请先 stop。'
  p="$(read_pid "$state/instance.lock" || true)"
  worker_alive "$p" && die '旧版通道仍在运行，请先 stop。'
  owns=1
  printf '%s\n' "$$" > "$state/instance.lock"
  for p in instance.env ttyd.pid ttyd.birth cloudflared.pid cloudflared.birth; do
    [ ! -f "$state/$p" ] || unlink "$state/$p"
  done
  : > "$state/ttyd.log"; : > "$state/cloudflared.log"
  cd -- "$workdir"
  install_deps
  code="$(curl -s --noproxy '*' --max-time 1 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/" || true)"
  [ "$code" = 000 ] || die "端口 $port 已有 HTTP 服务，请先关闭旧服务或设置 HERDR_WEB_PORT。"
  password="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
  args=("$herdr")
  [ "$session" = default ] || args+=(--session "$session")
  # SIGTERM works even when nohup's ignored SIGHUP is inherited by clients.
  "$ttyd_bin" -i 127.0.0.1 -p "$port" -W -s 15 "${ttyd_frontend[@]}" \
    -t scrollback=10000 -c "$user:$password" \
    bash "$self" __web_client "${args[@]}" > "$state/ttyd.log" 2>&1 9>&- &
  ttyd_pid=$!; record_child ttyd "$ttyd_pid"
  for i in {1..40}; do
    alive "$ttyd_pid" || die "ttyd 启动失败，请查看 $state/ttyd.log（可能端口被占用）。"
    code="$(curl -s --noproxy '*' --max-time 1 -u "$user:$password" -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/" || true)"
    [ "$code" = 200 ] && break
    sleep 0.25 9>&-
  done
  [ "$code" = 200 ] || die 'ttyd 本机检查失败。'
  # Nonempty explicit config: no /dev/null config and no HOME override.
  printf 'loglevel: info\nmetrics: 127.0.0.1:0\n' > "$state/tunnel.yml"
  "$cf_bin" tunnel --config "$state/tunnel.yml" --no-autoupdate \
    --url "http://127.0.0.1:$port" > "$state/cloudflared.log" 2>&1 9>&- &
  cf_pid=$!; record_child cloudflared "$cf_pid"
  for i in {1..240}; do
    alive "$ttyd_pid" && alive "$cf_pid" || die '服务启动失败，请查看 logs。'
    url="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$state/cloudflared.log" | head -n 1 || true)"
    if [ -n "$url" ] && grep -q 'Registered tunnel connection' "$state/cloudflared.log"; then break; fi
    sleep 0.25 9>&-
  done
  [ -n "$url" ] && grep -q 'Registered tunnel connection' "$state/cloudflared.log" || die '60 秒内未建立 Cloudflare 连接。'
  printf 'session=%s\npublic_url=%s\nusername=%s\npassword=%s\nstarted_in=%s\nport=%s\nttyd_path=%s\n' \
    "$session" "$url" "$user" "$password" "$workdir" "$port" "$ttyd_bin" > "$state/instance.env.tmp"
  mv "$state/instance.env.tmp" "$state/instance.env"
  printf 'ready\n' > "$result"
  result=''
  while alive "$ttyd_pid" && alive "$cf_pid"; do sleep 2 9>&-; done
  die 'ttyd 或 Cloudflare 已退出；没有自动重启。'
}
stop() {
  local p i stamp
  p="$(read_pid "$state/instance.lock" || true)"
  if worker_alive "$p"; then
    stamp="$(birth "$p")"
    kill -TERM "$p" 2>/dev/null || true
    for i in {1..60}; do worker_alive "$p" || break; sleep 0.2; done
    if worker_alive "$p" && [ "$(birth "$p")" = "$stamp" ]; then
      stop_child cloudflared; stop_child ttyd
      kill -KILL "$p" 2>/dev/null || true
    fi
  fi
  check_child_records
  stop_child cloudflared; stop_child ttyd
  for p in instance.lock instance.env ttyd.pid ttyd.birth cloudflared.pid cloudflared.birth; do
    [ ! -f "$state/$p" ] || unlink "$state/$p"
  done
  echo '通道已停止；Herdr 会话和里面的程序继续运行。'
}
start() {
  local session="$1" p result_file outcome i
  [[ "$session" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || die 'Session 名请用字母、数字、点、下划线或横线，最长 64 字符。'
  [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && ((port <= 65535)) || die 'HERDR_WEB_PORT 必须是 1～65535。'
  [[ "$user" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die 'HERDR_WEB_USER 请用字母、数字、点、下划线或横线，最长 64 字符。'
  p="$(read_pid "$state/instance.lock" || true)"
  if worker_alive "$p"; then info || true; die '已有通道，请先运行 ./tun.sh stop。'; fi
  # Recover only recorded child PIDs whose start identity still matches.
  check_child_records
  stop_child cloudflared; stop_child ttyd
  result_file="$(mktemp "$state/start-result.XXXXXX")"
  echo '正在后台启动；首次运行可能需要下载依赖……'
  # Give the supervisor its own session/process group as well as ignoring HUP.
  # SSH or a launcher may clean up its entire process group after it exits.
  nohup perl -MPOSIX=setsid -e \
    'defined(setsid()) or die "setsid: $!\n"; exec @ARGV or die "exec: $!\n"' \
    bash "$self" __worker "$session" "$PWD" "$result_file" \
    >> "$state/supervisor.log" 2>&1 < /dev/null 8>&- &
  p=$!
  for i in {1..1200}; do
    [ ! -s "$result_file" ] || break
    alive "$p" || break
    sleep 0.25
  done
  outcome="$(cat "$result_file")"
  unlink "$result_file"
  if [ "$outcome" != ready ]; then
    worker_alive "$p" && kill -TERM "$p" 2>/dev/null || true
    [ -z "$outcome" ] || wait "$p" 2>/dev/null || true
    tail -n 15 "$state/supervisor.log" >&2
    die "${outcome:-启动超时或后台进程退出。}"
  fi
  info
}
usage() {
  printf '系统：%s（支持 Linux / macOS；Mac 缺少依赖时使用 Homebrew）\n' "$platform"
  printf '%s\n' 'Windows：在 PowerShell 运行 .\tun.ps1；WSL 中照常运行 ./tun.sh。'
  printf '%s\n' \
    './tun.sh list [--json]   列出 Herdr sessions，不启动通道' \
    './tun.sh [session]       后台启动，默认 session 为主机名' \
    './tun.sh start [session] 同上（可连接名为 list/status 等的 session）' \
    './tun.sh default         连接不带 --session 启动的默认会话' \
    './tun.sh status          查看运行情况、地址、用户名和密码' \
    './tun.sh stop            只关闭网页通道，保留 Herdr' \
    './tun.sh restart [name]  重开通道，默认沿用当前 session' \
    './tun.sh logs            查看日志，Ctrl+C 退出查看'
}

action="${1:-start}"
case "$action" in
  __web_client)
    shift; web_client "$@"; exit ;;
  list|llist)
    [ "$#" -le 2 ] && { [ "$#" -lt 2 ] || [ "$2" = --json ]; } || die '用法：./tun.sh list [--json]'
    shift; list_sessions "$@"; exit ;;
  help|-h|--help) usage; exit ;;
esac
prepare_runtime
mkdir -p "$state"; chmod 700 "$state"
if [ "$action" = __worker ]; then
  [ "$#" = 4 ] || exit 2
  worker "$2" "$3" "$4"; exit
fi
case "$action" in
  start|restart) [ "$#" -le 2 ] || die '参数太多。' ;;
  *) [ "$#" -eq 1 ] || die '参数太多。' ;;
esac
case "$action" in
  status) info ;;
  logs) touch "$state/supervisor.log" "$state/ttyd.log" "$state/cloudflared.log"; tail -n 30 -F "$state/"*.log ;;
  *)
    exec 8> "$state/control.lock"
    "$flock_bin" -n 8 || die '另一项启动或停止操作正在进行，请稍后重试。'
    case "$action" in
      stop) stop ;;
      restart) session="${2:-$(get session)}"; stop; start "${session:-$default_session}" ;;
      start) start "${2:-$default_session}" ;;
      *) start "$action" ;;
    esac ;;
esac
