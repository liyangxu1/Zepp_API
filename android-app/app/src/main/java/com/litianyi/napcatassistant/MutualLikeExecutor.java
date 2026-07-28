package com.litianyi.napcatassistant;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;

/** 用户点击后在 App 前台分批、串行完成当天全部互赞任务。 */
public final class MutualLikeExecutor {
    private final NapCatOneBotClient oneBot;
    private final MutualLikeServerClient server;
    private final TaskJournal journal;
    private final String installId;
    private final String appVersion;
    private final AtomicBoolean canceled;
    private final Listener listener;
    private final TokenListener tokenListener;
    private String accessToken;

    public MutualLikeExecutor(
        NapCatOneBotClient oneBot,
        MutualLikeServerClient server,
        TaskJournal journal,
        String installId,
        String appVersion,
        String existingAccessToken,
        AtomicBoolean canceled,
        Listener listener,
        TokenListener tokenListener
    ) {
        this.oneBot = oneBot;
        this.server = server;
        this.journal = journal;
        this.installId = installId;
        this.appVersion = appVersion;
        this.accessToken = existingAccessToken == null
            ? ""
            : existingAccessToken.trim();
        this.canceled = canceled;
        this.listener = listener;
        this.tokenListener = tokenListener;
    }

    public Summary runOnce() throws IOException, JSONException,
        TaskJournal.IOExceptionLike {
        progress("正在通过 get_status 和 get_login_info 确认登录…");
        NapCatOneBotClient.LoginInfo login = oneBot.verifyLogin();
        checkCanceled();
        progress("已确认 QQ " + maskQq(login.qqId) + " 在线。");

        JSONObject registerRequest = MutualLikeProtocol.registerRequest(
            login.qqId,
            installId,
            appVersion
        );
        accessToken = server.register(registerRequest, accessToken);
        if (tokenListener != null) {
            tokenListener.onToken(accessToken);
        }
        checkCanceled();
        JSONObject heartbeat = server.heartbeat(accessToken);

        journal.recoverInterrupted();
        flushPendingResults();
        checkCanceled();

        Summary summary = new Summary();
        summary.updateServerTasks(heartbeat);
        while (!canceled.get()) {
            JSONObject lease = server.lease(
                MutualLikeProtocol.leaseRequest(),
                accessToken
            );
            List<MutualLikeProtocol.Task> tasks =
                MutualLikeProtocol.parseTasks(lease);
            if (tasks.isEmpty()) {
                summary.updateServerTasks(server.heartbeat(accessToken));
                break;
            }
            summary.leased += tasks.size();
            progress(
                "服务器下发 " + tasks.size()
                    + " 条任务，将按顺序执行本批次。"
            );

            for (MutualLikeProtocol.Task task : tasks) {
                checkCanceled();
                JSONObject existing = journal.find(task.taskId);
                if (existing != null) {
                    progress("任务已在本机记录，跳过点赞并补报结果。");
                    reportStored(existing, summary);
                    continue;
                }
                executeOne(login.qqId, task, summary);
            }
            summary.updateServerTasks(server.heartbeat(accessToken));
        }
        summary.canceled = canceled.get();
        return summary;
    }

    private void executeOne(
        String ownQq,
        MutualLikeProtocol.Task task,
        Summary summary
    ) throws IOException, JSONException, TaskJournal.IOExceptionLike {
        String attemptId = UUID.randomUUID().toString();
        if (!task.targetQq.matches("[0-9]+")) {
            JSONObject invalid = MutualLikeProtocol.taskResult(
                task,
                attemptId,
                "failed",
                "invalid_target",
                "服务器下发的目标 QQ 号无效"
            );
            journal.finish(invalid);
            reportStored(invalid, summary);
            return;
        }
        if (task.leaseToken.isEmpty()) {
            JSONObject invalidLease = MutualLikeProtocol.taskResult(
                task,
                attemptId,
                "failed",
                "missing_lease_token",
                "服务器下发的任务缺少 lease_token"
            );
            journal.finish(invalidLease);
            reportStored(invalidLease, summary);
            return;
        }
        if (ownQq.equals(task.targetQq)) {
            JSONObject skipped = MutualLikeProtocol.taskResult(
                task,
                attemptId,
                "failed",
                "skipped_self",
                "客户端已排除当前登录账号"
            );
            journal.finish(skipped);
            reportStored(skipped, summary);
            summary.skipped++;
            return;
        }

        JSONObject sending = MutualLikeProtocol.taskResult(
            task,
            attemptId,
            "sending",
            "send_like_started",
            ""
        );
        journal.begin(sending);
        if (canceled.get()) {
            JSONObject canceledResult = MutualLikeProtocol.taskResult(
                task,
                attemptId,
                "failed",
                "canceled_before_send",
                "App 已离开前台，点赞请求未执行"
            );
            journal.finish(canceledResult);
            reportStored(canceledResult, summary);
            summary.canceled = true;
            return;
        }

        JSONObject result;
        progress("正在执行一条点赞任务（目标 " + maskQq(task.targetQq) + "）。");
        try {
            NapCatOneBotClient.ActionResult action = oneBot.sendLike(
                task.targetQq,
                task.times
            );
            result = MutualLikeProtocol.taskResult(
                task,
                attemptId,
                action.success ? "succeeded" : "failed",
                action.success
                    ? "onebot_ok"
                    : "onebot_retcode_" + action.retcode,
                action.message
            );
            if (action.success) {
                summary.succeeded++;
            } else {
                summary.failed++;
            }
        } catch (NapCatOneBotClient.OneBotCallException error) {
            boolean uncertain = error.wasRequestBodyWritten();
            result = MutualLikeProtocol.taskResult(
                task,
                attemptId,
                uncertain ? "uncertain" : "failed",
                uncertain ? "send_like_uncertain" : "send_like_not_sent",
                uncertain
                    ? "send_like 发包后响应异常；客户端不会自动重试"
                    : "send_like 请求未写出：" + safeMessage(error)
            );
            if (uncertain) {
                summary.uncertain++;
            } else {
                summary.failed++;
            }
        }
        journal.finish(result);
        reportStored(result, summary);
    }

    private void flushPendingResults() throws IOException,
        TaskJournal.IOExceptionLike {
        List<JSONObject> pending = journal.pendingResults();
        if (!pending.isEmpty()) {
            progress("正在补报 " + pending.size() + " 条本机任务结果。");
        }
        for (JSONObject result : pending) {
            checkCanceled();
            server.reportResult(result, accessToken);
            journal.markReported(result.optString("task_id", ""));
        }
    }

    private void reportStored(
        JSONObject result,
        Summary summary
    ) throws IOException, TaskJournal.IOExceptionLike {
        try {
            server.reportResult(result, accessToken);
        } catch (IOException error) {
            summary.reportFailed++;
            progress("任务结果暂未上报，本批已停止；下次只补报结果。");
            throw error;
        }
        journal.markReported(result.optString("task_id", ""));
        summary.reported++;
    }

    private void checkCanceled() throws IOException {
        if (canceled.get()) {
            throw new IOException("App 已离开前台，本次同步已停止");
        }
    }

    private void progress(String message) {
        if (listener != null) {
            listener.onProgress(message);
        }
    }

    private static String maskQq(String qq) {
        if (qq == null || qq.length() <= 4) {
            return "****";
        }
        return qq.substring(0, 2) + "****" + qq.substring(qq.length() - 2);
    }

    private static String safeMessage(Exception error) {
        String message = error.getMessage();
        return message == null || message.trim().isEmpty()
            ? error.getClass().getSimpleName()
            : message;
    }

    public interface Listener {
        void onProgress(String message);
    }

    public interface TokenListener {
        void onToken(String accessToken);
    }

    public static final class Summary {
        public int leased;
        public int succeeded;
        public int failed;
        public int uncertain;
        public int skipped;
        public int reported;
        public int reportFailed;
        public boolean canceled;
        public int pendingToday;
        public int succeededToday;
        public int failedToday;
        public int uncertainToday;

        private Summary() {
        }

        private void updateServerTasks(JSONObject response) {
            JSONObject tasks = response.optJSONObject("tasks");
            if (tasks == null) {
                tasks = response.optJSONObject("summary");
            }
            if (tasks == null) {
                return;
            }
            pendingToday = tasks.optInt(
                "pending",
                tasks.optInt("queued", 0) + tasks.optInt("leased", 0)
            );
            succeededToday = tasks.optInt("succeeded", 0);
            failedToday = tasks.optInt("failed", 0);
            uncertainToday = tasks.optInt("uncertain", 0);
        }
    }
}
