#!/data/data/com.termux/files/usr/bin/bash

set -o pipefail

PROGRESS_FILE="$HOME/.mutual_like_init_progress"
PROGRESS_TEMP="$PROGRESS_FILE.tmp"
INSTALL_LOG="$HOME/.mutual_like_init.log"
INSTALL_SCRIPT="$HOME/napcat.termux.sh"

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
    write_progress \
        "FAILED" \
        "0" \
        "运行环境准备失败" \
        "安装脚本退出码：$exit_code，请检查网络后重试"
}

trap 'exit_code=$?; if [ "$exit_code" -ne 0 ]; then finish_failed "$exit_code"; fi' EXIT

write_progress "RUNNING" "3" "正在下载安装脚本" "阶段 1/5 · 连接 NapCat 官方安装源"
curl -fL --retry 3 -o "$INSTALL_SCRIPT" \
    "https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.termux.sh"
sed -i \
    's#https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh#https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.sh#g' \
    "$INSTALL_SCRIPT"

write_progress "RUNNING" "8" "正在准备 Termux 依赖" "阶段 2/5 · 下载基础命令和容器工具"
: > "$INSTALL_LOG"

set +e
bash "$INSTALL_SCRIPT" 2>&1 | while IFS= read -r line; do
    printf '%s\n' "$line"
    printf '%s\n' "$line" >> "$INSTALL_LOG"
    plain_line=$(printf '%s' "$line" | sed 's/\x1b\[[0-9;]*m//g')
    case "$plain_line" in
        *"准备proot-distro环境中"*)
            write_progress "RUNNING" "12" "正在准备 Termux 依赖" "阶段 2/5 · 更新软件源"
            ;;
        *"准备proot-distro环境成功"*)
            write_progress "RUNNING" "25" "Termux 依赖已准备" "阶段 2/5 · 基础工具下载完成"
            ;;
        *"安装napcat容器中"*)
            write_progress "RUNNING" "32" "正在下载 NapCat 容器" "阶段 3/5 · 下载 Debian 运行环境"
            ;;
        *"Downloading"*rootfs*|*"下载"*rootfs*)
            write_progress "RUNNING" "42" "正在下载 NapCat 容器" "阶段 3/5 · 正在接收容器文件"
            ;;
        *"安装napcat容器"*"成功"*)
            write_progress "RUNNING" "55" "NapCat 容器已准备" "阶段 3/5 · 容器文件下载完成"
            ;;
        *"正在初始化napcat容器"*)
            write_progress "RUNNING" "60" "正在初始化 NapCat 容器" "阶段 4/5 · 安装容器依赖"
            ;;
        *"安装系统依赖"*|*"更新软件包列表"*)
            write_progress "RUNNING" "68" "正在安装系统依赖" "阶段 4/5 · 配置 QQ 运行环境"
            ;;
        *"下载QQ"*|*"下载 QQ"*|*"安装QQ"*|*"安装 QQ"*)
            write_progress "RUNNING" "76" "正在下载 QQ" "阶段 4/5 · 获取 QQ Linux 组件"
            ;;
        *"下载NapCat"*|*"下载 NapCat"*)
            write_progress "RUNNING" "86" "正在下载 NapCat" "阶段 5/5 · 获取 NapCat 组件"
            ;;
        *"安装NapCat"*|*"安装 NapCat"*|*"注入NapCat"*)
            write_progress "RUNNING" "92" "正在安装 NapCat" "阶段 5/5 · 写入本机运行组件"
            ;;
        *"napcat容器安装成功"*)
            write_progress "RUNNING" "98" "正在完成初始化" "阶段 5/5 · 正在校验运行环境"
            ;;
    esac
done
install_exit_code=${PIPESTATUS[0]}
set -e

if [ "$install_exit_code" -ne 0 ]; then
    exit "$install_exit_code"
fi

write_progress "DONE" "100" "运行环境准备完成" "初始化完成，正在进入 QQ 登录"
trap - EXIT
