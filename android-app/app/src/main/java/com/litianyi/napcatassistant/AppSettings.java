package com.litianyi.napcatassistant;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.UUID;

/** App 私有配置。OneBot token 只保存在本机私有 SharedPreferences。 */
public final class AppSettings {
    private static final String PREFERENCES = "app_settings";
    private static final String KEY_INSTALL_ID = "install_id";
    private static final String KEY_ONEBOT_TOKEN = "onebot_token";
    private static final String KEY_MOBILE_ACCESS_TOKEN = "mobile_access_token";
    private static final String KEY_MOBILE_TOKEN_SERVER = "mobile_token_server";

    private AppSettings() {
    }

    public static String getOrCreateInstallId(Context context) {
        SharedPreferences preferences = preferences(context);
        String value = preferences.getString(KEY_INSTALL_ID, "");
        if (value != null && !value.trim().isEmpty()) {
            return value;
        }
        String generated = UUID.randomUUID().toString();
        preferences.edit().putString(KEY_INSTALL_ID, generated).commit();
        return generated;
    }

    public static String getOneBotToken(Context context) {
        return preferences(context).getString(KEY_ONEBOT_TOKEN, "");
    }

    public static void saveOneBotToken(Context context, String token) {
        if (token == null || token.trim().isEmpty()) {
            throw new IllegalArgumentException("OneBot token 不能为空");
        }
        preferences(context).edit()
            .putString(KEY_ONEBOT_TOKEN, token.trim())
            .commit();
    }

    public static String getServerUrl(Context context) {
        return context.getString(R.string.mutual_like_server_url).trim();
    }

    public static void saveServerUrl(Context context, String serverUrl) {
        // 正式包地址由构建资源固定，本机调试通过 Gradle 参数覆盖。
    }

    public static String getMobileAccessToken(Context context, String serverUrl) {
        SharedPreferences preferences = preferences(context);
        String savedServer = preferences.getString(KEY_MOBILE_TOKEN_SERVER, "");
        if (!normalizeServerKey(serverUrl).equals(savedServer)) {
            return "";
        }
        return preferences.getString(KEY_MOBILE_ACCESS_TOKEN, "");
    }

    public static void saveMobileAccessToken(
        Context context,
        String serverUrl,
        String accessToken
    ) {
        if (accessToken == null || accessToken.trim().isEmpty()) {
            throw new IllegalArgumentException("手机互赞凭证不能为空");
        }
        preferences(context).edit()
            .putString(KEY_MOBILE_TOKEN_SERVER, normalizeServerKey(serverUrl))
            .putString(KEY_MOBILE_ACCESS_TOKEN, accessToken.trim())
            .commit();
    }

    public static void clearMobileAccessToken(Context context) {
        preferences(context).edit()
            .remove(KEY_MOBILE_ACCESS_TOKEN)
            .remove(KEY_MOBILE_TOKEN_SERVER)
            .commit();
    }

    private static String normalizeServerKey(String serverUrl) {
        String value = serverUrl == null ? "" : serverUrl.trim();
        while (value.endsWith("/")) {
            value = value.substring(0, value.length() - 1);
        }
        return value;
    }

    private static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }
}
