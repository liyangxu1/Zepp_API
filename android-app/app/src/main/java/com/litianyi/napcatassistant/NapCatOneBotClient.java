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

/** 仅访问 127.0.0.1:3000 的 NapCat OneBot HTTP 客户端。 */
public final class NapCatOneBotClient {
    private static final String BASE_URL = "http://127.0.0.1:3000/";
    private static final int CONNECT_TIMEOUT_MS = 5000;
    private static final int READ_TIMEOUT_MS = 15000;
    private static final int MAX_RESPONSE_BYTES = 128 * 1024;

    private final String token;

    public NapCatOneBotClient(String token) {
        if (token == null || token.trim().isEmpty()) {
            throw new IllegalArgumentException("尚未配置本机 OneBot token");
        }
        this.token = token.trim();
    }

    /** 同时通过 get_status 与 get_login_info 确认真实在线账号。 */
    public LoginInfo verifyLogin() throws IOException {
        JSONObject statusResponse = post("get_status", new JSONObject());
        JSONObject statusData = requireSuccessfulData(statusResponse, "get_status");
        if (!statusData.optBoolean("online", false)) {
            throw new IOException("NapCat 已启动，但 QQ 当前不在线");
        }

        JSONObject loginResponse = post("get_login_info", new JSONObject());
        JSONObject loginData = requireSuccessfulData(loginResponse, "get_login_info");
        String qqId = readId(loginData, "user_id");
        if (qqId.isEmpty() || !qqId.matches("[0-9]+")) {
            throw new IOException("get_login_info 未返回有效 QQ 号");
        }
        return new LoginInfo(qqId, loginData.optString("nickname", ""));
    }

    /**
     * 发出一次 send_like，不做重试。
     *
     * <p>如果请求体写出后连接异常，异常对象会标记 requestBodyWritten，
     * 上层必须将任务记为 uncertain，不能再次执行。</p>
     */
    public ActionResult sendLike(String targetQq, int times)
        throws OneBotCallException {
        JSONObject body = new JSONObject();
        try {
            body.put("user_id", targetQq);
            body.put("times", Math.max(1, Math.min(times, 10)));
        } catch (JSONException error) {
            throw new OneBotCallException("无法构造 send_like 请求", false, error);
        }

        JSONObject response;
        try {
            response = post("send_like", body);
        } catch (OneBotCallException error) {
            throw error;
        } catch (IOException error) {
            throw new OneBotCallException(
                safeMessage(error),
                error instanceof RequestIOException
                    && ((RequestIOException) error).wasRequestBodyWritten(),
                error
            );
        }

        int retcode = response.optInt("retcode", -1);
        boolean success = "ok".equalsIgnoreCase(response.optString("status", ""))
            && retcode == 0;
        String message = response.optString("message", "");
        if (message.isEmpty()) {
            message = response.optString("wording", "");
        }
        return new ActionResult(success, retcode, limit(message, 240));
    }

    private JSONObject post(String action, JSONObject body) throws IOException {
        HttpURLConnection connection = null;
        boolean requestBodyWritten = false;
        try {
            connection = (HttpURLConnection) new URL(BASE_URL + action).openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
            connection.setReadTimeout(READ_TIMEOUT_MS);
            connection.setDoOutput(true);
            connection.setUseCaches(false);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Authorization", "Bearer " + token);

            byte[] requestBytes = body.toString().getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(requestBytes.length);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(requestBytes);
                output.flush();
                requestBodyWritten = true;
            }

            int statusCode = connection.getResponseCode();
            InputStream stream = statusCode >= 200 && statusCode < 300
                ? connection.getInputStream()
                : connection.getErrorStream();
            String responseText = readLimited(stream);
            if (statusCode < 200 || statusCode >= 300) {
                throw new RequestIOException(
                    "OneBot HTTP " + statusCode,
                    requestBodyWritten
                );
            }
            try {
                return new JSONObject(responseText);
            } catch (JSONException error) {
                throw new RequestIOException(
                    "OneBot 返回了无效 JSON",
                    requestBodyWritten,
                    error
                );
            }
        } catch (RequestIOException error) {
            throw error;
        } catch (IOException error) {
            throw new RequestIOException(
                safeMessage(error),
                requestBodyWritten,
                error
            );
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private JSONObject requireSuccessfulData(JSONObject response, String action)
        throws IOException {
        int retcode = response.optInt("retcode", -1);
        if (!"ok".equalsIgnoreCase(response.optString("status", ""))
            || retcode != 0) {
            String message = response.optString("message", "");
            if (message.isEmpty()) {
                message = response.optString("wording", "");
            }
            throw new IOException(action + " 失败"
                + (message.isEmpty() ? "" : "：" + limit(message, 160)));
        }
        JSONObject data = response.optJSONObject("data");
        if (data == null) {
            throw new IOException(action + " 未返回 data");
        }
        return data;
    }

    private static String readId(JSONObject object, String key) {
        Object value = object.opt(key);
        if (value == null || value == JSONObject.NULL) {
            return "";
        }
        return String.valueOf(value).trim();
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
                    throw new IOException("响应内容过大");
                }
                output.write(buffer, 0, count);
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String limit(String value, int maxLength) {
        if (value == null) {
            return "";
        }
        return value.length() <= maxLength ? value : value.substring(0, maxLength);
    }

    private static String safeMessage(Exception error) {
        String message = error.getMessage();
        return message == null || message.trim().isEmpty()
            ? error.getClass().getSimpleName()
            : message;
    }

    public static final class LoginInfo {
        public final String qqId;
        public final String nickname;

        private LoginInfo(String qqId, String nickname) {
            this.qqId = qqId;
            this.nickname = nickname;
        }
    }

    public static final class ActionResult {
        public final boolean success;
        public final int retcode;
        public final String message;

        private ActionResult(boolean success, int retcode, String message) {
            this.success = success;
            this.retcode = retcode;
            this.message = message;
        }
    }

    public static final class OneBotCallException extends IOException {
        private final boolean requestBodyWritten;

        private OneBotCallException(
            String message,
            boolean requestBodyWritten,
            Throwable cause
        ) {
            super(message, cause);
            this.requestBodyWritten = requestBodyWritten;
        }

        public boolean wasRequestBodyWritten() {
            return requestBodyWritten;
        }
    }

    private static final class RequestIOException extends IOException {
        private final boolean requestBodyWritten;

        private RequestIOException(String message, boolean requestBodyWritten) {
            super(message);
            this.requestBodyWritten = requestBodyWritten;
        }

        private RequestIOException(
            String message,
            boolean requestBodyWritten,
            Throwable cause
        ) {
            super(message, cause);
            this.requestBodyWritten = requestBodyWritten;
        }

        private boolean wasRequestBodyWritten() {
            return requestBodyWritten;
        }
    }
}
