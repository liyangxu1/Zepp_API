package com.litianyi.napcatassistant;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/** 仅供原生扫码页访问本机 QQ 登录接口。 */
public final class NapCatLoginClient {
    private static final String BASE_URL = "http://127.0.0.1:6099";
    private static final int MAX_RESPONSE_BYTES = 128 * 1024;

    private final String webUiToken;
    private String credential = "";

    public NapCatLoginClient(String webUiToken) {
        if (webUiToken == null || webUiToken.trim().isEmpty()) {
            throw new IllegalArgumentException("登录凭证为空");
        }
        this.webUiToken = webUiToken.trim();
    }

    public void refreshQrCode() throws IOException {
        authenticatedPost("/api/QQLogin/RefreshQRcode");
    }

    public boolean isLoggedIn() throws IOException {
        JSONObject data = authenticatedPost("/api/QQLogin/CheckLoginStatus");
        return data.optBoolean("isLogin", false);
    }

    private JSONObject authenticatedPost(String path) throws IOException {
        ensureCredential();
        return requireSuccessfulData(post(path, new JSONObject(), credential));
    }

    private void ensureCredential() throws IOException {
        if (!credential.isEmpty()) {
            return;
        }
        JSONObject body = new JSONObject();
        try {
            body.put("hash", sha256(webUiToken + ".napcat"));
        } catch (JSONException error) {
            throw new IOException("无法构造登录请求", error);
        }
        JSONObject data = requireSuccessfulData(
            post("/api/auth/login", body, "")
        );
        credential = data.optString("Credential", "").trim();
        if (credential.isEmpty()) {
            throw new IOException("本机登录服务未返回凭证");
        }
    }

    private JSONObject post(
        String path,
        JSONObject body,
        String bearerToken
    ) throws IOException {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(BASE_URL + path)
                .openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(10000);
            connection.setDoOutput(true);
            connection.setUseCaches(false);
            connection.setRequestProperty(
                "Content-Type",
                "application/json; charset=utf-8"
            );
            connection.setRequestProperty("Accept", "application/json");
            if (bearerToken != null && !bearerToken.isEmpty()) {
                connection.setRequestProperty(
                    "Authorization",
                    "Bearer " + bearerToken
                );
            }

            byte[] requestBytes = body.toString()
                .getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(requestBytes.length);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(requestBytes);
            }

            int statusCode = connection.getResponseCode();
            InputStream stream = statusCode >= 200 && statusCode < 300
                ? connection.getInputStream()
                : connection.getErrorStream();
            String responseText = readLimited(stream);
            if (statusCode < 200 || statusCode >= 300) {
                throw new IOException("本机登录服务暂不可用");
            }
            try {
                return new JSONObject(responseText);
            } catch (JSONException error) {
                throw new IOException("本机登录服务返回异常", error);
            }
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private JSONObject requireSuccessfulData(JSONObject response)
        throws IOException {
        if (response.optInt("code", -1) != 0) {
            throw new IOException("本机登录操作未完成");
        }
        JSONObject data = response.optJSONObject("data");
        return data == null ? new JSONObject() : data;
    }

    private static String sha256(String value) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hashed = digest.digest(
                value.getBytes(StandardCharsets.UTF_8)
            );
            StringBuilder result = new StringBuilder(hashed.length * 2);
            for (byte item : hashed) {
                result.append(String.format("%02x", item & 0xff));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new IOException("当前系统缺少 SHA-256", error);
        }
    }

    private static String readLimited(InputStream stream) throws IOException {
        if (stream == null) {
            return "";
        }
        try (InputStream input = stream;
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int total = 0;
            int count;
            while ((count = input.read(buffer)) != -1) {
                total += count;
                if (total > MAX_RESPONSE_BYTES) {
                    throw new IOException("本机登录响应过大");
                }
                output.write(buffer, 0, count);
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }
}
