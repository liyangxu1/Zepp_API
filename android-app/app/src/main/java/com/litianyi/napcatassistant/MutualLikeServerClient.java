package com.litianyi.napcatassistant;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.MalformedURLException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/** 互赞调度服务器客户端；不会接收或上传 QQ 会话、Cookie 或 OneBot token。 */
public final class MutualLikeServerClient {
    private static final int CONNECT_TIMEOUT_MS = 8000;
    private static final int READ_TIMEOUT_MS = 15000;
    private static final int MAX_RESPONSE_BYTES = 256 * 1024;

    private final String baseUrl;

    public MutualLikeServerClient(String serverUrl) throws MalformedURLException {
        this.baseUrl = normalizeAndValidate(serverUrl);
    }

    public String register(JSONObject request, String existingAccessToken)
        throws IOException {
        JSONObject response = post(
            MutualLikeProtocol.REGISTER_PATH,
            request,
            existingAccessToken,
            ""
        );
        String accessToken = response.optString("access_token", "").trim();
        if (accessToken.isEmpty()) {
            throw new IOException("注册响应缺少 access_token");
        }
        return accessToken;
    }

    public JSONObject heartbeat(String accessToken) throws IOException {
        return post(
            MutualLikeProtocol.HEARTBEAT_PATH,
            new JSONObject(),
            accessToken,
            ""
        );
    }

    public JSONObject lease(JSONObject request, String accessToken) throws IOException {
        return post(MutualLikeProtocol.LEASE_PATH, request, accessToken, "");
    }

    public void reportResult(JSONObject storedResult, String accessToken)
        throws IOException {
        JSONObject body;
        try {
            body = new JSONObject(storedResult.toString());
        } catch (JSONException error) {
            throw new IOException("本机任务结果损坏", error);
        }
        String idempotencyKey = body.optString("_idempotency_key", "").trim();
        body.remove("_idempotency_key");
        if (idempotencyKey.isEmpty()) {
            throw new IOException("任务结果缺少幂等键");
        }
        post(
            MutualLikeProtocol.RESULT_PATH,
            body,
            accessToken,
            idempotencyKey
        );
    }

    private JSONObject post(
        String path,
        JSONObject payload,
        String accessToken,
        String idempotencyKey
    ) throws IOException {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(baseUrl + path).openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
            connection.setReadTimeout(READ_TIMEOUT_MS);
            connection.setDoOutput(true);
            connection.setUseCaches(false);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Accept", "application/json");
            if (accessToken != null && !accessToken.trim().isEmpty()) {
                connection.setRequestProperty(
                    "Authorization",
                    "Bearer " + accessToken.trim()
                );
            }
            if (idempotencyKey != null && !idempotencyKey.trim().isEmpty()) {
                connection.setRequestProperty(
                    "Idempotency-Key",
                    idempotencyKey.trim()
                );
            }

            byte[] requestBytes = payload.toString().getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(requestBytes.length);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(requestBytes);
            }

            int statusCode = connection.getResponseCode();
            InputStream stream = statusCode >= 200 && statusCode < 300
                ? connection.getInputStream()
                : connection.getErrorStream();
            String responseText = readLimited(stream);
            JSONObject response = parseResponse(responseText);
            if (statusCode < 200 || statusCode >= 300) {
                throw new ServerException(
                    statusCode,
                    response.optString("error_code", ""),
                    serverMessage(response)
                );
            }
            if (response.has("ok") && !response.optBoolean("ok", false)) {
                throw new IOException(serverMessage(response));
            }
            String status = response.optString("status", "");
            if ("error".equalsIgnoreCase(status)
                || "failed".equalsIgnoreCase(status)) {
                throw new IOException(serverMessage(response));
            }
            return response;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static JSONObject parseResponse(String responseText)
        throws IOException {
        if (responseText == null || responseText.trim().isEmpty()) {
            return new JSONObject();
        }
        try {
            return new JSONObject(responseText);
        } catch (JSONException error) {
            throw new IOException("任务服务器返回了无效 JSON", error);
        }
    }

    private static String normalizeAndValidate(String rawUrl) throws MalformedURLException {
        String value = rawUrl == null ? "" : rawUrl.trim();
        while (value.endsWith("/")) {
            value = value.substring(0, value.length() - 1);
        }
        URL url = new URL(value);
        String scheme = url.getProtocol().toLowerCase(Locale.ROOT);
        String host = url.getHost().toLowerCase(Locale.ROOT);
        boolean localHttp = "http".equals(scheme)
            && ("127.0.0.1".equals(host)
                || "localhost".equals(host)
                || "10.0.2.2".equals(host)
                || "10.0.3.2".equals(host));
        if (!"https".equals(scheme) && !localHttp) {
            throw new MalformedURLException(
                "服务器必须使用 HTTPS；模拟器本机测试可使用 127.0.0.1、"
                    + "10.0.2.2 或 10.0.3.2 的 HTTP"
            );
        }
        if (host.isEmpty()) {
            throw new MalformedURLException("服务器地址缺少主机名");
        }
        String path = url.getPath();
        if (path != null && !path.isEmpty() && !"/".equals(path)) {
            throw new MalformedURLException("服务器地址只填写根地址，不要附加 API 路径");
        }
        return value;
    }

    private static String serverMessage(JSONObject response) {
        String message = response.optString("error", "");
        if (message.isEmpty()) {
            message = response.optString("message", "");
        }
        if (message.isEmpty()) {
            message = response.optString("detail", "");
        }
        return message.isEmpty() ? "任务服务器拒绝请求" : limit(message, 240);
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
                    throw new IOException("任务服务器响应内容过大");
                }
                output.write(buffer, 0, count);
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String limit(String value, int maxLength) {
        return value.length() <= maxLength ? value : value.substring(0, maxLength);
    }

    public static final class ServerException extends IOException {
        public final int statusCode;
        public final String errorCode;

        private ServerException(
            int statusCode,
            String errorCode,
            String message
        ) {
            super(message);
            this.statusCode = statusCode;
            this.errorCode = errorCode == null ? "" : errorCode;
        }
    }
}
