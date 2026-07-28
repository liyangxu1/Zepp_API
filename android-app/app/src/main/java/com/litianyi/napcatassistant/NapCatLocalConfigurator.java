package com.litianyi.napcatassistant;

/** 生成在 Termux/proot 内执行的本机 OneBot 安全配置命令。 */
public final class NapCatLocalConfigurator {
    private NapCatLocalConfigurator() {
    }

    /**
     * token 在 proot 内生成，命令文本本身不包含 token。
     *
     * <p>命令只输出一段供 App 私下接收的 JSON；主页面不得把该 stdout
     * 写入可见运行日志。</p>
     */
    public static String configureCommand() {
        return String.join("\n",
            "proot-distro sh napcat -- bash -s <<'NAPCAT_APP_ONEBOT'",
            "set -eu",
            "config_dir=/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config",
            "token_file=\"$config_dir/.mobile_app_onebot_token\"",
            "mkdir -p \"$config_dir\"",
            "umask 077",
            "if [ -s \"$token_file\" ]; then",
            "  token=$(cat \"$token_file\")",
            "else",
            "  token=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \\n')",
            "  printf '%s' \"$token\" > \"$token_file\"",
            "fi",
            "case \"$token\" in",
            "  ''|*[!0-9a-f]*) echo '无法生成本机 OneBot token' >&2; exit 2 ;;",
            "esac",
            "write_config() {",
            "  target=$1",
            "  temp=\"${target}.mobile-app.tmp\"",
            "  jq -n --arg token \"$token\" '{",
            "    network: {",
            "      httpServers: [{",
            "        enable: true,",
            "        name: \"mobile-app-local\",",
            "        host: \"127.0.0.1\",",
            "        port: 3000,",
            "        enableCors: false,",
            "        enableWebsocket: false,",
            "        messagePostFormat: \"array\",",
            "        token: $token,",
            "        debug: false",
            "      }],",
            "      httpSseServers: [],",
            "      httpClients: [],",
            "      websocketServers: [],",
            "      websocketClients: [],",
            "      plugins: []",
            "    },",
            "    musicSignUrl: \"\",",
            "    enableLocalFile2Url: false,",
            "    parseMultMsg: false",
            "  }' > \"$temp\"",
            "  chmod 600 \"$temp\"",
            "  mv \"$temp\" \"$target\"",
            "}",
            "write_config \"$config_dir/onebot11.json\"",
            "login_dir=/root/.config/QQ/nt_qq/global/nt_data/Login",
            "account_file=$(find \"$login_dir\" -maxdepth 1 -type f -name '.*' "
                + "2>/dev/null | head -n 1 || true)",
            "account=${account_file##*/}",
            "account=${account#.}",
            "case \"$account\" in",
            "  ''|*[!0-9]*) ;;",
            "  *) write_config \"$config_dir/onebot11_${account}.json\" ;;",
            "esac",
            "printf '{\"configured\":true,\"token\":\"%s\"}\\n' \"$token\"",
            "NAPCAT_APP_ONEBOT"
        );
    }
}
