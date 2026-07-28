package com.litianyi.napcatassistant;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;

/**
 * 本机任务幂等日志。
 *
 * <p>在 send_like 前同步写入 sending。App 中断后 sending 会恢复为 uncertain，
 * 同一 task_id 永远不会再次触发 send_like；失败的结果上报可安全补报。</p>
 */
public final class TaskJournal {
    private static final String PREFERENCES = "mutual_like_task_journal";
    private static final String KEY_ENTRIES = "entries";
    private static final int MAX_REPORTED_ENTRIES = 500;

    private final SharedPreferences preferences;

    public TaskJournal(Context context) {
        preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    public synchronized JSONObject find(String taskId) {
        JSONObject entries = readEntries();
        JSONObject entry = entries.optJSONObject(keyFor(taskId));
        if (entry == null) {
            return null;
        }
        JSONObject payload = entry.optJSONObject("payload");
        return payload == null ? null : copy(payload);
    }

    public synchronized void begin(JSONObject sendingPayload) throws IOExceptionLike {
        save(sendingPayload, false);
    }

    public synchronized void finish(JSONObject resultPayload) throws IOExceptionLike {
        save(resultPayload, false);
    }

    public synchronized void markReported(String taskId) throws IOExceptionLike {
        JSONObject entries = readEntries();
        String key = keyFor(taskId);
        JSONObject entry = entries.optJSONObject(key);
        if (entry == null) {
            return;
        }
        try {
            entry.put("reported", true);
            entry.put("updated_at", System.currentTimeMillis());
            writeEntries(entries);
        } catch (JSONException error) {
            throw new IOExceptionLike("无法更新本机任务日志", error);
        }
    }

    /** 把上次进程中断时的 sending 保守恢复为 uncertain。 */
    public synchronized void recoverInterrupted() throws IOExceptionLike {
        JSONObject entries = readEntries();
        boolean changed = false;
        Iterator<String> keys = entries.keys();
        while (keys.hasNext()) {
            JSONObject entry = entries.optJSONObject(keys.next());
            JSONObject payload = entry == null ? null : entry.optJSONObject("payload");
            if (payload == null
                || !"sending".equals(payload.optString("outcome", ""))) {
                continue;
            }
            try {
                payload.put("outcome", "uncertain");
                payload.put("result_code", "app_interrupted");
                payload.put(
                    "result_message",
                    "App 在任务执行期间中断；为避免重复点赞，本任务不会再次执行"
                );
                entry.put("reported", false);
                entry.put("updated_at", System.currentTimeMillis());
                changed = true;
            } catch (JSONException error) {
                throw new IOExceptionLike("无法恢复本机任务日志", error);
            }
        }
        if (changed) {
            writeEntries(entries);
        }
    }

    public synchronized List<JSONObject> pendingResults() {
        JSONObject entries = readEntries();
        List<JSONObject> results = new ArrayList<>();
        Iterator<String> keys = entries.keys();
        while (keys.hasNext()) {
            JSONObject entry = entries.optJSONObject(keys.next());
            if (entry == null || entry.optBoolean("reported", false)) {
                continue;
            }
            JSONObject payload = entry.optJSONObject("payload");
            if (payload != null
                && !"sending".equals(payload.optString("outcome", ""))) {
                results.add(copy(payload));
            }
        }
        return results;
    }

    private void save(JSONObject payload, boolean reported) throws IOExceptionLike {
        String taskId = payload.optString("task_id", "");
        if (taskId.isEmpty()) {
            throw new IOExceptionLike("任务日志缺少 task_id");
        }
        JSONObject entries = readEntries();
        JSONObject entry = new JSONObject();
        try {
            entry.put("payload", copy(payload));
            entry.put("reported", reported);
            entry.put("updated_at", System.currentTimeMillis());
            entries.put(keyFor(taskId), entry);
            prune(entries);
            writeEntries(entries);
        } catch (JSONException error) {
            throw new IOExceptionLike("无法写入本机任务日志", error);
        }
    }

    private void prune(JSONObject entries) {
        while (entries.length() > MAX_REPORTED_ENTRIES) {
            String oldestKey = null;
            long oldestTime = Long.MAX_VALUE;
            Iterator<String> keys = entries.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                JSONObject entry = entries.optJSONObject(key);
                if (entry == null || !entry.optBoolean("reported", false)) {
                    continue;
                }
                long updatedAt = entry.optLong("updated_at", 0L);
                if (updatedAt < oldestTime) {
                    oldestTime = updatedAt;
                    oldestKey = key;
                }
            }
            if (oldestKey == null) {
                return;
            }
            entries.remove(oldestKey);
        }
    }

    private JSONObject readEntries() {
        String raw = preferences.getString(KEY_ENTRIES, "{}");
        try {
            return new JSONObject(raw == null ? "{}" : raw);
        } catch (JSONException ignored) {
            return new JSONObject();
        }
    }

    private void writeEntries(JSONObject entries) throws IOExceptionLike {
        if (!preferences.edit().putString(KEY_ENTRIES, entries.toString()).commit()) {
            throw new IOExceptionLike("本机任务日志持久化失败");
        }
    }

    private static JSONObject copy(JSONObject object) {
        try {
            return new JSONObject(object.toString());
        } catch (JSONException ignored) {
            return new JSONObject();
        }
    }

    private static String keyFor(String taskId) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(taskId.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(bytes.length * 2);
            for (byte value : bytes) {
                result.append(String.format("%02x", value & 0xff));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("Android 缺少 SHA-256", error);
        }
    }

    public static final class IOExceptionLike extends Exception {
        private IOExceptionLike(String message) {
            super(message);
        }

        private IOExceptionLike(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
