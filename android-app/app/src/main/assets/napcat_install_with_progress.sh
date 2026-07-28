#!/data/data/com.termux/files/usr/bin/bash

set -o pipefail

PROGRESS_FILE="$HOME/.mutual_like_init_progress"
PROGRESS_TEMP="$PROGRESS_FILE.tmp"
INSTALL_LOG="$HOME/.mutual_like_init.log"
INSTALL_SCRIPT="$HOME/napcat.termux.sh"
ROOTFS_INSTALLER="$HOME/.mutual_like_install_rootfs.sh"
ROOTFS_DIR="$HOME/.cache/qq-like-runtime"
ROOTFS_NAME="debian-trixie-arm64-20260713.tar.gz"
ROOTFS_ARCHIVE="$ROOTFS_DIR/$ROOTFS_NAME"
ROOTFS_PART="$ROOTFS_ARCHIVE.part"
ROOTFS_URL="https://openmemory.cloud:18080/api/tools/qq-like/mobile/runtime/debian-arm64-rootfs"
ROOTFS_SIZE=49950316
ROOTFS_SHA256="ae6da09230365626e1faaf1cf339efec58db8790273d0f8a2c7b0caa2db0c7e6"

write_progress() {
    local state="$1"
    local percent="$2"
    local stage="$3"
    local detail="$4"
    {
        printf 'STATE=%s\n' "$state"
        printf 'PERCENT=%s\n' "$percent"
        printf 'STAGE=%s\n' "$stage"
        printf 'DETAIL=%s\n' "$detail"
        printf 'UPDATED_AT=%s\n' "$(date +%s)"
    } > "$PROGRESS_TEMP"
    mv "$PROGRESS_TEMP" "$PROGRESS_FILE"
}

finish_failed() {
    local exit_code="$1"
    local percent="0"
    local error_detail=""
    if [ -f "$PROGRESS_FILE" ]; then
        percent=$(sed -n 's/^PERCENT=//p' "$PROGRESS_FILE" | tail -n 1)
    fi
    if [ -f "$INSTALL_LOG" ]; then
        error_detail=$(tail -n 80 "$INSTALL_LOG" \
            | sed 's/\x1b\[[0-9;]*m//g' \
            | grep -E '失败|Failed|failed|错误|Error|error|timed out|unreachable' \
            | tail -n 1)
    fi
    if [ -z "$error_detail" ]; then
        error_detail="安装脚本退出码：$exit_code，请检查网络后重试"
    fi
    write_progress \
        "FAILED" \
        "$percent" \
        "运行环境准备失败" \
        "$error_detail"
}

verify_rootfs() {
    local archive="$1"
    [ -f "$archive" ] || return 1
    [ "$(stat -c '%s' "$archive" 2>/dev/null)" = "$ROOTFS_SIZE" ] \
        || return 1
    [ "$(sha256sum "$archive" | cut -d ' ' -f 1)" = "$ROOTFS_SHA256" ]
}

format_rootfs_bytes() {
    local bytes="$1"
    awk -v current="$bytes" -v total="$ROOTFS_SIZE" \
        'BEGIN {printf "%.1f MB / %.1f MB", current / 1048576, total / 1048576}'
}

download_rootfs() {
    mkdir -p "$ROOTFS_DIR"
    if verify_rootfs "$ROOTFS_ARCHIVE"; then
        write_progress \
            "RUNNING" \
            "35" \
            "Debian 基础环境已下载" \
            "阶段 2/6 · 安全校验已通过，继续初始化"
        return 0
    fi
    if [ -f "$ROOTFS_ARCHIVE" ]; then
        mv "$ROOTFS_ARCHIVE" "$ROOTFS_PART"
    fi
    local partial_size
    partial_size=$(stat -c '%s' "$ROOTFS_PART" 2>/dev/null || printf '0')
    if [ "$partial_size" -gt "$ROOTFS_SIZE" ]; then
        : > "$ROOTFS_PART"
        partial_size=0
    fi

    write_progress \
        "RUNNING" \
        "5" \
        "正在下载 Debian 基础环境" \
        "阶段 2/6 · 正在连接工具网"
    curl -fL -sS \
        --retry 3 \
        --retry-delay 2 \
        --connect-timeout 15 \
        -C - \
        -o "$ROOTFS_PART" \
        "$ROOTFS_URL" >> "$INSTALL_LOG" 2>&1 &
    local download_pid=$!
    while kill -0 "$download_pid" 2>/dev/null; do
        partial_size=$(stat -c '%s' "$ROOTFS_PART" 2>/dev/null || printf '0')
        local percent=$((5 + partial_size * 30 / ROOTFS_SIZE))
        if [ "$percent" -gt 34 ]; then
            percent=34
        fi
        write_progress \
            "RUNNING" \
            "$percent" \
            "正在下载 Debian 基础环境" \
            "阶段 2/6 · $(format_rootfs_bytes "$partial_size")"
        sleep 1
    done
    if ! wait "$download_pid"; then
        return 1
    fi
    if ! verify_rootfs "$ROOTFS_PART"; then
        printf '%s\n' "Debian 基础环境大小或 SHA-256 校验失败" \
            >> "$INSTALL_LOG"
        return 1
    fi
    mv "$ROOTFS_PART" "$ROOTFS_ARCHIVE"
    write_progress \
        "RUNNING" \
        "35" \
        "Debian 基础环境已下载" \
        "阶段 2/6 · 安全校验已通过"
}

trap 'exit_code=$?; if [ "$exit_code" -ne 0 ]; then finish_failed "$exit_code"; fi' EXIT

: > "$INSTALL_LOG"
write_progress "RUNNING" "2" "正在下载安装脚本" "阶段 1/6 · 连接 NapCat 官方安装源"
curl -fL --retry 3 -o "$INSTALL_SCRIPT" \
    "https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.termux.sh" \
    >> "$INSTALL_LOG" 2>&1 || exit 1
sed -i \
    's#https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh#https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.sh#g' \
    "$INSTALL_SCRIPT" || exit 1

if ! download_rootfs; then
    exit 1
fi

cat > "$ROOTFS_INSTALLER" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
set -e
container_dir="\$PREFIX/var/lib/proot-distro/containers/napcat"
legacy_dir="\$PREFIX/var/lib/proot-distro/installed-rootfs/napcat"
if [ -d "\$container_dir" ] || [ -d "\$legacy_dir" ]; then
    proot-distro remove napcat >/dev/null 2>&1 || true
fi
proot-distro install -n napcat "$ROOTFS_ARCHIVE"
EOF
chmod 700 "$ROOTFS_INSTALLER" || exit 1
sed -i \
    "s#proot-distro install debian --override-alias napcat#bash $ROOTFS_INSTALLER#g" \
    "$INSTALL_SCRIPT" || exit 1

write_progress "RUNNING" "38" "正在准备 Termux 依赖" "阶段 3/6 · 下载基础命令和容器工具"

set +e
bash "$INSTALL_SCRIPT" 2>&1 | while IFS= read -r line; do
    printf '%s\n' "$line"
    printf '%s\n' "$line" >> "$INSTALL_LOG"
    plain_line=$(printf '%s' "$line" | sed 's/\x1b\[[0-9;]*m//g')
    case "$plain_line" in
        *"准备proot-distro环境中"*)
            write_progress "RUNNING" "40" "正在准备 Termux 依赖" "阶段 3/6 · 更新软件源"
            ;;
        *"准备proot-distro环境成功"*)
            write_progress "RUNNING" "45" "Termux 依赖已准备" "阶段 3/6 · 基础工具下载完成"
            ;;
        *"安装napcat容器中"*)
            write_progress "RUNNING" "48" "正在安装 Debian 基础环境" "阶段 4/6 · 正在解压本机容器"
            ;;
        *"安装napcat容器"*"成功"*)
            write_progress "RUNNING" "58" "Debian 基础环境已准备" "阶段 4/6 · 本机容器安装完成"
            ;;
        *"正在初始化napcat容器"*)
            write_progress "RUNNING" "60" "正在初始化 NapCat 容器" "阶段 5/6 · 安装容器依赖"
            ;;
        *"安装系统依赖"*|*"更新软件包列表"*)
            write_progress "RUNNING" "68" "正在安装系统依赖" "阶段 5/6 · 配置 QQ 运行环境"
            ;;
        *"下载QQ"*|*"下载 QQ"*|*"安装QQ"*|*"安装 QQ"*)
            write_progress "RUNNING" "76" "正在下载 QQ" "阶段 5/6 · 获取 QQ Linux 组件"
            ;;
        *"下载NapCat"*|*"下载 NapCat"*)
            write_progress "RUNNING" "86" "正在下载 NapCat" "阶段 6/6 · 获取 NapCat 组件"
            ;;
        *"安装NapCat"*|*"安装 NapCat"*|*"注入NapCat"*)
            write_progress "RUNNING" "92" "正在安装 NapCat" "阶段 6/6 · 写入本机运行组件"
            ;;
        *"napcat容器安装成功"*)
            write_progress "RUNNING" "98" "正在完成初始化" "阶段 6/6 · 正在校验运行环境"
            ;;
    esac
done
install_exit_code=${PIPESTATUS[0]}
set -e

if [ "$install_exit_code" -ne 0 ]; then
    exit "$install_exit_code"
fi

rm -f "$ROOTFS_ARCHIVE" "$ROOTFS_INSTALLER"
write_progress "DONE" "100" "运行环境准备完成" "初始化完成，正在进入 QQ 登录"
trap - EXIT
