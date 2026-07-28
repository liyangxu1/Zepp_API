package com.litianyi.napcatassistant;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/** 负责从工具网检查、下载并校验 App 更新包。 */
public final class AppUpdateClient {
    private static final int CONNECT_TIMEOUT_MS = 15_000;
    private static final int READ_TIMEOUT_MS = 60_000;
    private static final long MAX_APK_BYTES = 500L * 1024L * 1024L;

    public interface DownloadProgress {
        void onProgress(long downloadedBytes, long totalBytes);
    }

    public static final class Release {
        public final int versionCode;
        public final String versionName;
        public final String title;
        public final List<String> changelog;
        public final boolean forceUpdate;
        public final String sha256;
        public final long sizeBytes;
        public final String downloadUrl;

        Release(JSONObject value) throws JSONException {
            versionCode = value.getInt("version_code");
            versionName = value.getString("version_name");
            title = value.optString("title", "发现新版本");
            forceUpdate = value.optBoolean("force_update", false);
            sha256 = value.getString("sha256").toLowerCase(Locale.ROOT);
            sizeBytes = value.getLong("size_bytes");
            downloadUrl = value.getString("download_url");
            JSONArray rawChangelog = value.optJSONArray("changelog");
            List<String> items = new ArrayList<>();
            if (rawChangelog != null) {
                for (int index = 0; index < rawChangelog.length(); index++) {
                    String item = rawChangelog.optString(index, "").trim();
                    if (!item.isEmpty()) {
                        items.add(item);
                    }
                }
            }
            changelog = Collections.unmodifiableList(items);
        }
    }

    public static final class CheckResult {
        public final boolean available;
        public final Release release;

        CheckResult(boolean available, Release release) {
            this.available = available;
            this.release = release;
        }
    }

    private final String serverUrl;

    public AppUpdateClient(String serverUrl) {
        this.serverUrl = trimTrailingSlash(serverUrl);
    }

    public CheckResult check(int currentVersionCode, String currentVersionName)
        throws IOException, JSONException {
        String endpoint = serverUrl
            + "/api/tools/qq-like/mobile/app/update"
            + "?current_version_code=" + Math.max(0, currentVersionCode)
            + "&current_version_name=" + encodeQuery(currentVersionName);
        JSONObject response = requestJson(endpoint);
        if (!"success".equals(response.optString("status"))) {
            throw new IOException(
                response.optString("error", "更新检查失败")
            );
        }
        JSONObject rawRelease = response.optJSONObject("release");
        Release release = rawRelease == null ? null : new Release(rawRelease);
        return new CheckResult(
            response.optBoolean("available", false) && release != null,
            release
        );
    }

    public File download(
        Context context,
        Release release,
        DownloadProgress progress
    ) throws IOException {
        if (release == null
            || release.versionCode <= 0
            || release.sizeBytes <= 0
            || release.sizeBytes > MAX_APK_BYTES
            || !release.sha256.matches("[0-9a-f]{64}")) {
            throw new IOException("更新包信息无效");
        }
        File updateDir = new File(context.getCacheDir(), "updates");
        if (!updateDir.isDirectory() && !updateDir.mkdirs()) {
            throw new IOException("无法创建更新缓存目录");
        }
        File partial = new File(
            updateDir,
            "mutual-like-" + release.versionCode + ".apk.part"
        );
        File target = new File(
            updateDir,
            "mutual-like-" + release.versionCode + ".apk"
        );
        if (partial.exists() && !partial.delete()) {
            throw new IOException("无法清理未完成的更新包");
        }
        if (target.exists() && !target.delete()) {
            throw new IOException("无法替换旧更新包");
        }

        HttpURLConnection connection = open(resolveUrl(release.downloadUrl));
        connection.setRequestMethod("GET");
        int status = connection.getResponseCode();
        if (status != HttpURLConnection.HTTP_OK) {
            connection.disconnect();
            throw new IOException("更新包下载失败，HTTP " + status);
        }
        long contentLength = connection.getContentLengthLong();
        long totalBytes = release.sizeBytes > 0
            ? release.sizeBytes
            : contentLength;
        MessageDigest digest = sha256Digest();
        long downloadedBytes = 0;
        try (
            InputStream input = connection.getInputStream();
            FileOutputStream output = new FileOutputStream(partial)
        ) {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = input.read(buffer)) != -1) {
                downloadedBytes += read;
                if (downloadedBytes > MAX_APK_BYTES) {
                    throw new IOException("更新包超过大小限制");
                }
                output.write(buffer, 0, read);
                digest.update(buffer, 0, read);
                if (progress != null) {
                    progress.onProgress(downloadedBytes, totalBytes);
                }
            }
            output.getFD().sync();
        } catch (IOException error) {
            partial.delete();
            throw error;
        } finally {
            connection.disconnect();
        }

        if (downloadedBytes != release.sizeBytes) {
            partial.delete();
            throw new IOException("更新包大小校验失败");
        }
        String actualSha256 = toHex(digest.digest());
        if (!actualSha256.equals(release.sha256)) {
            partial.delete();
            throw new IOException("更新包安全校验失败");
        }
        if (!partial.renameTo(target)) {
            partial.delete();
            throw new IOException("无法保存已校验的更新包");
        }
        return target;
    }

    public static boolean verifyCachedRelease(File apk, Release release) {
        if (apk == null || release == null || !apk.isFile()) {
            return false;
        }
        if (apk.length() != release.sizeBytes) {
            return false;
        }
        try (
            FileInputStream input = new FileInputStream(apk)
        ) {
            MessageDigest digest = sha256Digest();
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = input.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
            return toHex(digest.digest()).equals(release.sha256);
        } catch (IOException error) {
            return false;
        }
    }

    private JSONObject requestJson(String endpoint)
        throws IOException, JSONException {
        HttpURLConnection connection = open(endpoint);
        connection.setRequestMethod("GET");
        int status = connection.getResponseCode();
        InputStream stream = status >= 200 && status < 300
            ? connection.getInputStream()
            : connection.getErrorStream();
        String body;
        try (InputStream input = stream) {
            body = readUtf8(input, 1024 * 1024);
        } finally {
            connection.disconnect();
        }
        JSONObject response = new JSONObject(body);
        if (status < 200 || status >= 300) {
            throw new IOException(
                response.optString("error", "更新服务暂不可用")
            );
        }
        return response;
    }

    private HttpURLConnection open(String endpoint) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(
            endpoint
        ).openConnection();
        connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
        connection.setReadTimeout(READ_TIMEOUT_MS);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("User-Agent", "MutualLikeUpdater/1");
        return connection;
    }

    private String resolveUrl(String value) throws IOException {
        try {
            URI base = URI.create(serverUrl + "/");
            URI resolved = base.resolve(value);
            String scheme = resolved.getScheme();
            if (!"https".equalsIgnoreCase(scheme)
                && !"http".equalsIgnoreCase(scheme)) {
                throw new IOException("更新地址协议无效");
            }
            return resolved.toString();
        } catch (IllegalArgumentException error) {
            throw new IOException("更新地址无效", error);
        }
    }

    private static String readUtf8(InputStream input, int maxBytes)
        throws IOException {
        if (input == null) {
            return "{}";
        }
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int total = 0;
        int read;
        while ((read = input.read(buffer)) != -1) {
            total += read;
            if (total > maxBytes) {
                throw new IOException("更新响应过大");
            }
            output.write(buffer, 0, read);
        }
        return output.toString(StandardCharsets.UTF_8.name());
    }

    private static MessageDigest sha256Digest() throws IOException {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException error) {
            throw new IOException("系统不支持 SHA-256", error);
        }
    }

    private static String toHex(byte[] bytes) {
        StringBuilder value = new StringBuilder(bytes.length * 2);
        for (byte item : bytes) {
            value.append(String.format(Locale.ROOT, "%02x", item & 0xff));
        }
        return value.toString();
    }

    private static String trimTrailingSlash(String value) {
        String result = value == null ? "" : value.trim();
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }

    private static String encodeQuery(String value) {
        try {
            return java.net.URLEncoder.encode(
                value == null ? "" : value,
                StandardCharsets.UTF_8.name()
            );
        } catch (Exception ignored) {
            return "";
        }
    }
}
