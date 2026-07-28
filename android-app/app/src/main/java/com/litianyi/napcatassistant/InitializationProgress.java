package com.litianyi.napcatassistant;

import android.content.Context;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;

/** 生成官方 NapCat 安装命令并解析本机进度文件。 */
public final class InitializationProgress {
    private static final String ASSET_NAME =
        "napcat_install_with_progress.sh";
    private static final String SCRIPT_PATH =
        "$HOME/.mutual_like_install.sh";
    private static final String PROGRESS_PATH =
        "$HOME/.mutual_like_init_progress";

    public static final class Status {
        public final String state;
        public final int percent;
        public final String stage;
        public final String detail;

        Status(String state, int percent, String stage, String detail) {
            this.state = state;
            this.percent = percent;
            this.stage = stage;
            this.detail = detail;
        }

        public boolean isRunning() {
            return "RUNNING".equals(state);
        }

        public boolean isDone() {
            return "DONE".equals(state);
        }

        public boolean isFailed() {
            return "FAILED".equals(state);
        }
    }

    private InitializationProgress() {
    }

    public static String installCommand(Context context) throws IOException {
        byte[] script = readAsset(context, ASSET_NAME);
        String encoded = Base64.encodeToString(script, Base64.NO_WRAP);
        return "printf '%s' '" + encoded + "' | base64 -d > "
            + SCRIPT_PATH
            + " && chmod 700 "
            + SCRIPT_PATH
            + " && bash "
            + SCRIPT_PATH;
    }

    public static String probeCommand() {
        return "if [ -f " + PROGRESS_PATH + " ]; then cat "
            + PROGRESS_PATH
            + "; else printf 'STATE=IDLE\\nPERCENT=0\\n"
            + "STAGE=等待初始化\\nDETAIL=尚未开始下载\\n'; fi";
    }

    public static Status parse(String raw) {
        Map<String, String> values = new HashMap<>();
        String[] lines = raw == null ? new String[0] : raw.split("\\r?\\n");
        for (String line : lines) {
            int separator = line.indexOf('=');
            if (separator <= 0) {
                continue;
            }
            values.put(
                line.substring(0, separator).trim(),
                line.substring(separator + 1).trim()
            );
        }
        int percent;
        try {
            percent = Integer.parseInt(values.getOrDefault("PERCENT", "0"));
        } catch (NumberFormatException error) {
            percent = 0;
        }
        return new Status(
            values.getOrDefault("STATE", "IDLE"),
            Math.max(0, Math.min(100, percent)),
            values.getOrDefault("STAGE", "正在准备运行环境"),
            values.getOrDefault("DETAIL", "正在读取初始化进度")
        );
    }

    private static byte[] readAsset(Context context, String name)
        throws IOException {
        try (
            InputStream input = context.getAssets().open(name);
            ByteArrayOutputStream output = new ByteArrayOutputStream()
        ) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        }
    }
}
