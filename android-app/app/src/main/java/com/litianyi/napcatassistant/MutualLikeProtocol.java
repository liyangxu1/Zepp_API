package com.litianyi.napcatassistant;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 移动端互赞服务的暂定 JSON 协议。
 *
 * <p>服务端结构尚未最终确定，端点、字段名和兼容解析都集中在这里调整。</p>
 */
public final class MutualLikeProtocol {
    public static final String REGISTER_PATH = "/api/tools/qq-like/mobile/register";
    public static final String HEARTBEAT_PATH = "/api/tools/qq-like/mobile/heartbeat";
    public static final String LEASE_PATH = "/api/tools/qq-like/mobile/tasks/lease";
    public static final String RESULT_PATH = "/api/tools/qq-like/mobile/tasks/result";
    public static final int MAX_TASKS_PER_SYNC = 8;

    private MutualLikeProtocol() {
    }

    public static JSONObject registerRequest(
        String qqId,
        String installId,
        String appVersion
    ) throws JSONException {
        return new JSONObject()
            .put("qq_number", qqId)
            .put("install_id", installId)
            .put("app_version", appVersion);
    }

    public static JSONObject leaseRequest() throws JSONException {
        return new JSONObject().put("limit", MAX_TASKS_PER_SYNC);
    }

    public static JSONObject taskResult(
        Task task,
        String attemptId,
        String outcome,
        String resultCode,
        String detail
    ) throws JSONException {
        JSONObject result = new JSONObject()
            .put("task_id", task.taskId)
            .put("lease_token", task.leaseToken)
            .put("outcome", outcome)
            .put("result_code", resultCode)
            .put(
                "result_message",
                detail == null ? "" : limit(detail.trim(), 240)
            )
            // 本字段仅供本机日志和 HTTP Header 使用，发送前会从 body 移除。
            .put("_idempotency_key", attemptId);
        return result;
    }

    public static List<Task> parseTasks(JSONObject response) {
        JSONArray array = response.optJSONArray("tasks");
        if (array == null) {
            Object data = response.opt("data");
            if (data instanceof JSONArray) {
                array = (JSONArray) data;
            } else if (data instanceof JSONObject) {
                array = ((JSONObject) data).optJSONArray("tasks");
            }
        }
        if (array == null) {
            return Collections.emptyList();
        }

        List<Task> tasks = new ArrayList<>();
        int count = Math.min(array.length(), MAX_TASKS_PER_SYNC);
        for (int index = 0; index < count; index++) {
            JSONObject item = array.optJSONObject(index);
            if (item == null) {
                continue;
            }
            String taskId = firstString(item, "task_id", "id");
            if (taskId.isEmpty()) {
                continue;
            }
            String targetQq = firstString(item, "target_qq", "user_id", "qq");
            String leaseToken = firstString(item, "lease_token");
            int times = item.optInt("times", 1);
            tasks.add(new Task(
                taskId,
                targetQq,
                Math.max(1, Math.min(times, 10)),
                leaseToken,
                firstString(item, "lease_expires_at")
            ));
        }
        return tasks;
    }

    private static String firstString(JSONObject object, String... keys) {
        for (String key : keys) {
            Object value = object.opt(key);
            if (value != null && value != JSONObject.NULL) {
                String text = String.valueOf(value).trim();
                if (!text.isEmpty()) {
                    return text;
                }
            }
        }
        return "";
    }

    private static String limit(String value, int maxLength) {
        return value.length() <= maxLength ? value : value.substring(0, maxLength);
    }

    public static final class Task {
        public final String taskId;
        public final String targetQq;
        public final int times;
        public final String leaseToken;
        public final String leaseExpiresAt;

        private Task(
            String taskId,
            String targetQq,
            int times,
            String leaseToken,
            String leaseExpiresAt
        ) {
            this.taskId = taskId;
            this.targetQq = targetQq;
            this.times = times;
            this.leaseToken = leaseToken;
            this.leaseExpiresAt = leaseExpiresAt;
        }
    }
}
