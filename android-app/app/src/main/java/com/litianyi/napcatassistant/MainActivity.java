package com.litianyi.napcatassistant;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/** NapCat 官方 Termux 运行链路的最小控制页面。 */
public final class MainActivity extends Activity {
    private static final int REQUEST_RUN_COMMAND = 2001;
    private static final String UPDATE_PREFERENCES = "app_update";
    private static final String KEY_PENDING_UPDATE_PATH = "pending_update_path";
    private static final String INITIALIZATION_PREFERENCES =
        "initialization_progress";
    private static final String KEY_INITIALIZATION_RUNNING =
        "initialization_running";
    private static final long INITIALIZATION_POLL_INTERVAL_MS = 1200L;
    private static final String FIND_NAPCAT_PIDS_COMMAND =
        "napcat_pids='';"
            + " for proc_path in /proc/[0-9]*; do"
            + " [ \"$(cat \"$proc_path/comm\" 2>/dev/null)\" = 'proot' ] || continue;"
            + " grep -aq 'containers/napcat' \"$proc_path/cmdline\" 2>/dev/null"
            + " || continue;"
            + " grep -aq 'Napcat/opt/QQ/qq' \"$proc_path/cmdline\" 2>/dev/null"
            + " || continue;"
            + " napcat_pids=\"$napcat_pids ${proc_path##*/}\";"
            + " done;";
    private static final String PROBE_COMMAND =
        "if [ -x \"$PREFIX/var/lib/proot-distro/containers/napcat/rootfs"
            + "/root/Napcat/opt/QQ/qq\" ]"
            + " || [ -x \"$PREFIX/var/lib/proot-distro/installed-rootfs/napcat"
            + "/root/Napcat/opt/QQ/qq\" ];"
            + " then echo 'ENV_READY=1'; else echo 'ENV_READY=0'; fi;"
            + FIND_NAPCAT_PIDS_COMMAND
            + " if [ -n \"$napcat_pids\" ];"
            + " then echo 'RUNTIME_RUNNING=1'; else echo 'RUNTIME_RUNNING=0'; fi";
    private static final String START_COMMAND =
        FIND_NAPCAT_PIDS_COMMAND
            + " if [ -n \"$napcat_pids\" ]; then"
            + " echo 'NAPCAT_ALREADY_RUNNING=1';"
            + " else screen -S napcat -X quit >/dev/null 2>&1 || true;"
            + " screen -wipe >/dev/null 2>&1 || true;"
            + " for socket in \"$HOME\"/.screen/*.napcat; do"
            + " [ -S \"$socket\" ] && unlink \"$socket\"; done;"
            + " login_dir=\"$PREFIX/var/lib/proot-distro/containers/napcat/rootfs"
            + "/root/.config/QQ/nt_qq/global/nt_data/Login\";"
            + " account_file=$(find \"$login_dir\" -maxdepth 1 -type f -name '.*'"
            + " 2>/dev/null | head -n 1);"
            + " account=${account_file##*/}; account=${account#.}; account_arg='';"
            + " case \"$account\" in ''|*[!0-9]*) ;; *) account_arg=\"-q $account\" ;; esac;"
            + " screen -dmS napcat bash -lc "
            + "\"proot-distro sh napcat -- bash -lc "
            + "'xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox $account_arg'\";"
            + " sleep 4; echo 'NAPCAT_START_SENT=1'; fi";
    private static final String RESTART_COMMAND =
        "screen -S napcat -X quit >/dev/null 2>&1 || true;"
            + FIND_NAPCAT_PIDS_COMMAND
            + " [ -z \"$napcat_pids\" ] || kill -TERM $napcat_pids"
            + " >/dev/null 2>&1 || true;"
            + " for wait_round in 1 2 3 4 5; do"
            + FIND_NAPCAT_PIDS_COMMAND
            + " [ -n \"$napcat_pids\" ] || break;"
            + " sleep 1; done;"
            + FIND_NAPCAT_PIDS_COMMAND
            + " [ -z \"$napcat_pids\" ] || kill -KILL $napcat_pids"
            + " >/dev/null 2>&1 || true;"
            + " screen -wipe >/dev/null 2>&1 || true; sleep 1; "
            + START_COMMAND;
    private static final String READ_WEBUI_COMMAND =
        "proot-distro sh napcat -- bash -lc "
            + "\"jq -c '{token:.token}' "
            + "/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json"
            + " 2>/dev/null || true\"";
    private static final String TERMUX_SETUP_COMMAND =
        "mkdir -p ~/.termux; "
            + "grep -q '^allow-external-apps=true$' ~/.termux/termux.properties 2>/dev/null "
            + "|| printf '\\nallow-external-apps=true\\n' >> ~/.termux/termux.properties; "
            + "termux-reload-settings";

    private TextView termuxStatus;
    private TextView environmentStatus;
    private TextView runtimeStatus;
    private TextView loginStatus;
    private TextView accountNickname;
    private TextView logView;
    private TextView mutualLikeStatus;
    private TextView pendingCount;
    private TextView completedCount;
    private TextView abnormalCount;
    private TextView taskRecordText;
    private TextView versionText;
    private TextView operationProgressTitle;
    private TextView operationProgressPercent;
    private TextView operationProgressDetail;
    private TextView operationProgressMeta;
    private ProgressBar operationProgressBar;
    private View operationProgressCard;
    private EditText serverUrlInput;
    private Button primaryButton;
    private Button loginButton;
    private Button oneBotButton;
    private Button mutualLikeButton;
    private Button checkUpdateButton;
    private boolean environmentReady;
    private boolean runtimeRunning;
    private boolean qqLoggedIn;
    private boolean openLoginWhenReady;
    private boolean mutualLikeRunning;
    private boolean mutualLikeRegistered;
    private AtomicBoolean mutualLikeCancellation;
    private final AtomicBoolean loginVerificationRunning =
        new AtomicBoolean(false);
    private final AtomicBoolean registrationRunning =
        new AtomicBoolean(false);
    private final AtomicBoolean updateCheckRunning =
        new AtomicBoolean(false);
    private final Handler progressHandler =
        new Handler(Looper.getMainLooper());
    private boolean initializationProbeRunning;
    private final Runnable initializationProgressPoll =
        this::probeInitializationProgress;
    private final ExecutorService networkExecutor =
        Executors.newSingleThreadExecutor();

    private final BroadcastReceiver resultReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            renderCommandResult(intent);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        bindViews();
        bindActions();
        versionText.setText("v" + getAppVersion());
        checkForUpdates(false);
    }

    @Override
    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    protected void onStart() {
        super.onStart();
        IntentFilter filter = new IntentFilter(TermuxResultService.ACTION_RESULT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(resultReceiver, filter, RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(resultReceiver, filter);
        }
    }

    @Override
    protected void onStop() {
        if (mutualLikeCancellation != null) {
            mutualLikeCancellation.set(true);
        }
        progressHandler.removeCallbacks(initializationProgressPoll);
        initializationProbeRunning = false;
        unregisterReceiver(resultReceiver);
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        if (mutualLikeCancellation != null) {
            mutualLikeCancellation.set(true);
        }
        networkExecutor.shutdownNow();
        super.onDestroy();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshLocalState();
        resumePendingUpdateInstall();
        resumeInitializationProgress();
    }

    private void bindViews() {
        termuxStatus = findViewById(R.id.termuxStatus);
        environmentStatus = findViewById(R.id.environmentStatus);
        runtimeStatus = findViewById(R.id.runtimeStatus);
        loginStatus = findViewById(R.id.loginStatus);
        accountNickname = findViewById(R.id.accountNickname);
        logView = findViewById(R.id.logView);
        mutualLikeStatus = findViewById(R.id.mutualLikeStatus);
        pendingCount = findViewById(R.id.pendingCount);
        completedCount = findViewById(R.id.completedCount);
        abnormalCount = findViewById(R.id.abnormalCount);
        taskRecordText = findViewById(R.id.taskRecordText);
        versionText = findViewById(R.id.versionText);
        operationProgressTitle = findViewById(R.id.operationProgressTitle);
        operationProgressPercent = findViewById(R.id.operationProgressPercent);
        operationProgressDetail = findViewById(R.id.operationProgressDetail);
        operationProgressMeta = findViewById(R.id.operationProgressMeta);
        operationProgressBar = findViewById(R.id.operationProgressBar);
        operationProgressCard = findViewById(R.id.operationProgressCard);
        serverUrlInput = findViewById(R.id.serverUrlInput);
        primaryButton = findViewById(R.id.primaryButton);
        loginButton = findViewById(R.id.loginButton);
        oneBotButton = findViewById(R.id.oneBotButton);
        mutualLikeButton = findViewById(R.id.mutualLikeButton);
        checkUpdateButton = findViewById(R.id.checkUpdateButton);
        serverUrlInput.setText(AppSettings.getServerUrl(this));
    }

    private void bindActions() {
        primaryButton.setOnClickListener(view -> onPrimaryAction());
        loginButton.setOnClickListener(view -> readWebUiToken());
        findViewById(R.id.checkButton).setOnClickListener(view -> probeRuntime());
        findViewById(R.id.termuxButton).setOnClickListener(view -> openTermux());
        oneBotButton.setOnClickListener(view -> confirmConfigureOneBot());
        mutualLikeButton.setOnClickListener(view -> beginMutualLikeSync());
        checkUpdateButton.setOnClickListener(
            view -> checkForUpdates(true)
        );
    }

    private void refreshLocalState() {
        boolean installed = TermuxBridge.isInstalled(this);
        if (!installed) {
            termuxStatus.setText("1  Termux：未安装");
            setStatusColor(termuxStatus, false);
            environmentStatus.setText("2  NapCat 环境：等待安装 Termux");
            runtimeStatus.setText("3  NapCat：未启动");
            loginStatus.setText("登录组件未就绪");
            accountNickname.setText("当前测试版需要预置本机登录组件");
            primaryButton.setText("了解详情");
            loginButton.setEnabled(false);
            oneBotButton.setEnabled(false);
            mutualLikeButton.setEnabled(false);
            appendLog("未检测到 Termux，请安装官方 GitHub 或 F-Droid 版本。");
            return;
        }

        termuxStatus.setText(
            TermuxBridge.hasRunPermission(this)
                ? "1  Termux：已安装，可以接受命令"
                : "1  Termux：已安装，等待授权"
        );
        setStatusColor(termuxStatus, TermuxBridge.hasRunPermission(this));
        if (!TermuxBridge.hasRunPermission(this)) {
            loginStatus.setText("首次登录需要授权");
            accountNickname.setText("授权后即可在本机打开 QQ 登录");
            primaryButton.setText("继续登录");
            mutualLikeButton.setEnabled(false);
            appendLog("需要授予“在 Termux 环境中运行命令”权限。");
            return;
        }
        loginStatus.setText("还未登录 QQ");
        accountNickname.setText("扫码后显示当前账号");
        primaryButton.setText("扫码登录");
        primaryButton.setEnabled(true);
        if (!AppSettings.getOneBotToken(this).isEmpty()) {
            verifyNapCatLogin();
        }
    }

    private void onPrimaryAction() {
        if (!TermuxBridge.isInstalled(this)) {
            new AlertDialog.Builder(this)
                .setTitle("登录组件尚未安装")
                .setMessage(
                    "当前测试版仍需要预置本机登录组件。"
                        + "后续正式安装包会把这部分合并，用户无需单独处理。"
                )
                .setPositiveButton("知道了", null)
                .show();
            return;
        }
        if (!TermuxBridge.hasRunPermission(this)) {
            showTermuxSetupDialog();
            return;
        }
        openLoginWhenReady = true;
        loginStatus.setText("正在准备扫码登录…");
        accountNickname.setText("首次启动可能需要一点时间");
        primaryButton.setEnabled(false);
        if (runtimeRunning) {
            openLoginWhenReady = false;
            readWebUiToken();
        } else {
            probeRuntime();
        }
    }

    private void showTermuxSetupDialog() {
        new AlertDialog.Builder(this)
            .setTitle("允许本机登录")
            .setMessage(
                "为打开 QQ 扫码登录，需要授权 App 启动本机登录组件。"
                    + "账号信息不会上传到任务服务器。"
            )
            .setPositiveButton("继续", (dialog, which) ->
                TermuxBridge.requestRunPermission(this, REQUEST_RUN_COMMAND)
            )
            .setNegativeButton("取消", null)
            .show();
    }

    @Override
    public void onRequestPermissionsResult(
        int requestCode,
        String[] permissions,
        int[] grantResults
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_RUN_COMMAND) {
            return;
        }
        boolean granted = grantResults.length > 0
            && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        appendLog(granted
            ? "Termux 命令权限已授予。"
            : "Termux 命令权限未授予，可在本 App 的系统权限页面重新设置。");
        refreshLocalState();
    }

    private void confirmInstallEnvironment() {
        new AlertDialog.Builder(this)
            .setTitle("准备 QQ 登录组件")
            .setMessage(
                "首次使用需要下载登录组件，并占用约 2.5GB 本机空间。"
                    + "下载完成后即可扫码登录，登录信息只保存在本机。"
            )
            .setPositiveButton("开始准备", (dialog, which) -> installEnvironment())
            .setNegativeButton("取消", null)
            .show();
    }

    private void openTermux() {
        Intent intent = TermuxBridge.launchIntent(this);
        if (intent != null) {
            startActivity(intent);
            return;
        }
        Toast.makeText(this, "未检测到 Termux", Toast.LENGTH_SHORT).show();
    }

    private void installEnvironment() {
        appendLog("开始执行 NapCat 官方 Termux 安装脚本。");
        openLoginWhenReady = true;
        primaryButton.setEnabled(false);
        setInitializationRunning(true);
        showOperationProgress(
            "正在准备运行环境",
            1,
            "正在启动 NapCat 官方安装流程",
            "阶段 1/5 · 请保持 App 在前台"
        );
        try {
            TermuxBridge.run(
                this,
                "install",
                InitializationProgress.installCommand(this),
                true
            );
            scheduleInitializationProgressPoll(300);
        } catch (RuntimeException | java.io.IOException error) {
            setInitializationRunning(false);
            primaryButton.setEnabled(true);
            handleCommandStartError(error);
        }
    }

    private void resumeInitializationProgress() {
        boolean running = getSharedPreferences(
            INITIALIZATION_PREFERENCES,
            MODE_PRIVATE
        ).getBoolean(KEY_INITIALIZATION_RUNNING, false);
        if (!running
            || !TermuxBridge.isInstalled(this)
            || !TermuxBridge.hasRunPermission(this)) {
            return;
        }
        showOperationProgress(
            "正在准备运行环境",
            1,
            "正在恢复初始化进度",
            "请保持 App 在前台"
        );
        scheduleInitializationProgressPoll(100);
    }

    private void setInitializationRunning(boolean running) {
        getSharedPreferences(INITIALIZATION_PREFERENCES, MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_INITIALIZATION_RUNNING, running)
            .apply();
    }

    private void scheduleInitializationProgressPoll(long delayMillis) {
        progressHandler.removeCallbacks(initializationProgressPoll);
        progressHandler.postDelayed(
            initializationProgressPoll,
            Math.max(0, delayMillis)
        );
    }

    private void probeInitializationProgress() {
        if (initializationProbeRunning
            || !TermuxBridge.isInstalled(this)
            || !TermuxBridge.hasRunPermission(this)) {
            return;
        }
        initializationProbeRunning = true;
        try {
            TermuxBridge.run(
                this,
                "install_progress",
                InitializationProgress.probeCommand(),
                true
            );
        } catch (RuntimeException error) {
            initializationProbeRunning = false;
            scheduleInitializationProgressPoll(
                INITIALIZATION_POLL_INTERVAL_MS
            );
        }
    }

    private void renderInitializationProgress(
        InitializationProgress.Status status
    ) {
        if (status.isRunning()) {
            showOperationProgress(
                status.stage,
                status.percent,
                status.detail,
                "初始化期间可以看到当前阶段和总体进度"
            );
            scheduleInitializationProgressPoll(
                INITIALIZATION_POLL_INTERVAL_MS
            );
            return;
        }
        if (status.isDone()) {
            setInitializationRunning(false);
            showOperationProgress(
                status.stage,
                100,
                status.detail,
                "正在检查 NapCat 运行状态"
            );
            probeRuntime();
            return;
        }
        if (status.isFailed()) {
            setInitializationRunning(false);
            showOperationFailure(status.stage, status.detail);
            primaryButton.setEnabled(true);
            return;
        }
        scheduleInitializationProgressPoll(
            INITIALIZATION_POLL_INTERVAL_MS
        );
    }

    private void startNapCat(boolean restart) {
        appendLog("正在后台启动 NapCat。");
        primaryButton.setEnabled(false);
        try {
            TermuxBridge.run(
                this,
                restart ? "restart" : "start",
                restart ? RESTART_COMMAND : START_COMMAND,
                true
            );
        } catch (RuntimeException error) {
            primaryButton.setEnabled(true);
            handleCommandStartError(error);
        }
    }

    private void confirmConfigureOneBot() {
        if (!environmentReady) {
            Toast.makeText(this, "请先准备 NapCat 环境", Toast.LENGTH_SHORT).show();
            return;
        }
        new AlertDialog.Builder(this)
            .setTitle("配置本机 OneBot")
            .setMessage(
                "将把 OneBot 配置为仅监听 127.0.0.1:3000，生成随机 token，"
                    + "并关闭 WebSocket、CORS、HTTP/WS 客户端。"
                    + "现有 OneBot 网络配置会被替换，随后 NapCat 会重启以生效。"
            )
            .setPositiveButton("配置并重启", (dialog, which) -> configureOneBot())
            .setNegativeButton("取消", null)
            .show();
    }

    private void configureOneBot() {
        appendLog("正在配置仅限本机访问的 OneBot HTTP 接口。");
        primaryButton.setEnabled(false);
        oneBotButton.setEnabled(false);
        mutualLikeButton.setEnabled(false);
        try {
            TermuxBridge.run(
                this,
                "onebot_config",
                NapCatLocalConfigurator.configureCommand(),
                true
            );
        } catch (RuntimeException error) {
            primaryButton.setEnabled(true);
            oneBotButton.setEnabled(true);
            handleCommandStartError(error);
        }
    }

    private void probeRuntime() {
        if (!TermuxBridge.isInstalled(this) || !TermuxBridge.hasRunPermission(this)) {
            return;
        }
        appendLog("正在检查 NapCat 环境和进程。");
        try {
            TermuxBridge.run(this, "probe", PROBE_COMMAND, true);
        } catch (RuntimeException error) {
            handleCommandStartError(error);
        }
    }

    private void readWebUiToken() {
        appendLog("正在读取本机 NapCat WebUI 配置。");
        try {
            TermuxBridge.run(this, "webui", READ_WEBUI_COMMAND, true);
        } catch (RuntimeException error) {
            handleCommandStartError(error);
        }
    }

    private void handleCommandStartError(Exception error) {
        appendLog("Termux 命令启动失败：" + safeMessage(error));
        showTermuxSetupDialog();
    }

    private void renderCommandResult(Intent resultIntent) {
        SharedPreferences preferences = getSharedPreferences(
            TermuxResultService.PREFERENCES,
            MODE_PRIVATE
        );
        boolean hasBroadcastResult = resultIntent != null
            && resultIntent.hasExtra(TermuxResultService.EXTRA_OPERATION);
        String operation = hasBroadcastResult
            ? resultIntent.getStringExtra(TermuxResultService.EXTRA_OPERATION)
            : preferences.getString("last_operation", "");
        String stdout = hasBroadcastResult
            ? resultIntent.getStringExtra(TermuxResultService.EXTRA_STDOUT)
            : preferences.getString("last_stdout", "");
        String stderr = hasBroadcastResult
            ? resultIntent.getStringExtra(TermuxResultService.EXTRA_STDERR)
            : preferences.getString("last_stderr", "");
        String error = hasBroadcastResult
            ? resultIntent.getStringExtra(TermuxResultService.EXTRA_ERROR)
            : preferences.getString("last_error", "");
        int exitCode = hasBroadcastResult
            ? resultIntent.getIntExtra(TermuxResultService.EXTRA_EXIT_CODE, -1)
            : preferences.getInt("last_exit_code", -1);
        operation = operation == null ? "" : operation;
        stdout = stdout == null ? "" : stdout;
        stderr = stderr == null ? "" : stderr;
        error = error == null ? "" : error;
        primaryButton.setEnabled(true);

        if ("install_progress".equals(operation)) {
            initializationProbeRunning = false;
            renderInitializationProgress(
                InitializationProgress.parse(stdout)
            );
            return;
        }

        if (!"webui".equals(operation)
            && !"onebot_config".equals(operation)
            && !stdout.trim().isEmpty()) {
            appendLog(stdout.trim());
        }
        if (!stderr.trim().isEmpty()) {
            appendLog("stderr: " + stderr.trim());
        }
        if (!error.trim().isEmpty()) {
            appendLog("Termux: " + error.trim());
        }

        if ("probe".equals(operation)) {
            environmentReady = stdout.contains("ENV_READY=1");
            runtimeRunning = stdout.contains("RUNTIME_RUNNING=1");
            renderRuntimeState();
        } else if ("install".equals(operation)) {
            setInitializationRunning(false);
            progressHandler.removeCallbacks(initializationProgressPoll);
            if (exitCode == 0) {
                showOperationProgress(
                    "运行环境准备完成",
                    100,
                    "NapCat 官方安装脚本执行完成",
                    "正在检查运行状态"
                );
                appendLog("官方安装脚本执行完成。");
            } else {
                showOperationFailure(
                    "运行环境准备失败",
                    "安装未完成，请检查网络后重试"
                );
                appendLog("安装未完成，请查看 Termux 输出。");
            }
            probeRuntime();
        } else if ("start".equals(operation)) {
            appendLog(exitCode == 0 ? "NapCat 启动命令已提交。" : "NapCat 启动失败。");
            probeRuntime();
        } else if ("restart".equals(operation)) {
            appendLog(exitCode == 0 ? "NapCat 已按本机接口配置重新启动。" : "NapCat 重启失败。");
            probeRuntime();
        } else if ("onebot_config".equals(operation)) {
            acceptOneBotConfiguration(stdout, exitCode);
            preferences.edit().remove("last_stdout").apply();
        } else if ("webui".equals(operation)) {
            openWebUi(stdout);
            preferences.edit().remove("last_stdout").apply();
        }
    }

    private void renderRuntimeState() {
        environmentStatus.setText(
            environmentReady
                ? "2  NapCat 环境：已安装"
                : "2  NapCat 环境：未安装"
        );
        setStatusColor(environmentStatus, environmentReady);
        runtimeStatus.setText(
            runtimeRunning
                ? "3  NapCat：运行中"
                : "3  NapCat：未启动"
        );
        setStatusColor(runtimeStatus, runtimeRunning);
        if (!qqLoggedIn) {
            loginStatus.setText(
                runtimeRunning
                    ? "还未登录 QQ"
                    : "正在准备扫码登录…"
            );
            accountNickname.setText(
                runtimeRunning
                    ? "点击下方按钮打开二维码"
                    : "首次启动可能需要一点时间"
            );
            setStatusColor(loginStatus, false);
        }
        if (!environmentReady) {
            primaryButton.setText("准备登录");
        } else if (!runtimeRunning) {
            primaryButton.setText("扫码登录");
        } else {
            primaryButton.setText(qqLoggedIn ? "已登录" : "扫码登录");
        }
        primaryButton.setEnabled(!qqLoggedIn);
        loginButton.setEnabled(runtimeRunning);
        oneBotButton.setEnabled(environmentReady);
        mutualLikeButton.setEnabled(
            runtimeRunning
                && qqLoggedIn
                && mutualLikeRegistered
                && !mutualLikeRunning
                && !AppSettings.getOneBotToken(this).isEmpty()
        );
        if (runtimeRunning && !AppSettings.getOneBotToken(this).isEmpty()) {
            verifyNapCatLogin();
        }
        if (openLoginWhenReady && !qqLoggedIn) {
            if (!environmentReady) {
                openLoginWhenReady = false;
                primaryButton.setEnabled(true);
                confirmInstallEnvironment();
            } else if (!runtimeRunning) {
                configureOneBot();
            } else {
                openLoginWhenReady = false;
                readWebUiToken();
            }
        }
    }

    private void acceptOneBotConfiguration(String rawResult, int exitCode) {
        if (exitCode != 0) {
            appendLog("本机 OneBot 配置失败，请查看 Termux 输出。");
            renderRuntimeState();
            return;
        }
        try {
            JSONObject result = extractJsonObject(rawResult);
            String token = result.optString("token", "");
            if (!result.optBoolean("configured", false) || token.trim().isEmpty()) {
                throw new JSONException("配置结果缺少 token");
            }
            AppSettings.saveOneBotToken(this, token);
            appendLog("本机 OneBot 已安全配置；token 仅保存在本机私有存储。");
            startNapCat(runtimeRunning);
        } catch (JSONException | IllegalArgumentException error) {
            appendLog("本机 OneBot 配置解析失败：" + safeMessage(error));
            renderRuntimeState();
        }
    }

    private void verifyNapCatLogin() {
        String token = AppSettings.getOneBotToken(this);
        if (token.isEmpty() || !loginVerificationRunning.compareAndSet(false, true)) {
            return;
        }
        loginStatus.setText("正在确认账号…");
        accountNickname.setText("请稍候");
        networkExecutor.execute(() -> {
            Exception lastError = null;
            for (int attempt = 0; attempt < 12; attempt++) {
                try {
                    NapCatOneBotClient.LoginInfo login =
                        new NapCatOneBotClient(token).verifyLogin();
                    runOnUiThread(() -> {
                        loginVerificationRunning.set(false);
                        qqLoggedIn = true;
                        runtimeRunning = true;
                        loginStatus.setText("已登录 " + maskQq(login.qqId));
                        accountNickname.setText(
                            login.nickname == null || login.nickname.trim().isEmpty()
                                ? "账号状态正常"
                                : login.nickname.trim()
                        );
                        setStatusColor(loginStatus, true);
                        primaryButton.setText("已登录");
                        primaryButton.setEnabled(false);
                        mutualLikeStatus.setText(
                            "正在加入今日互赞列表…"
                        );
                        mutualLikeButton.setEnabled(false);
                        registerAndHeartbeat(login.qqId);
                    });
                    return;
                } catch (Exception error) {
                    lastError = error;
                    try {
                        Thread.sleep(3000);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        loginVerificationRunning.set(false);
                        return;
                    }
                }
            }
            Exception error = lastError == null
                ? new IllegalStateException("OneBot 未就绪")
                : lastError;
            runOnUiThread(() -> {
                loginVerificationRunning.set(false);
                qqLoggedIn = false;
                mutualLikeRegistered = false;
                loginStatus.setText("还未登录 QQ");
                accountNickname.setText("扫码后显示当前账号");
                setStatusColor(loginStatus, false);
                primaryButton.setText("扫码登录");
                primaryButton.setEnabled(true);
                mutualLikeStatus.setText("登录后即可开始互赞");
                mutualLikeButton.setEnabled(false);
                appendLog("登录状态确认失败：" + safeMessage(error));
            });
        });
    }

    private void registerAndHeartbeat(String qqId) {
        if (!registrationRunning.compareAndSet(false, true)) {
            return;
        }
        String serverUrl = AppSettings.getServerUrl(this);
        networkExecutor.execute(() -> {
            try {
                MutualLikeServerClient server =
                    new MutualLikeServerClient(serverUrl);
                String accessToken = AppSettings.getMobileAccessToken(
                    getApplicationContext(),
                    serverUrl
                );
                String registeredToken = server.register(
                    MutualLikeProtocol.registerRequest(
                        qqId,
                        AppSettings.getOrCreateInstallId(
                            getApplicationContext()
                        ),
                        getAppVersion()
                    ),
                    accessToken
                );
                AppSettings.saveMobileAccessToken(
                    getApplicationContext(),
                    serverUrl,
                    registeredToken
                );
                JSONObject heartbeat = server.heartbeat(registeredToken);
                runOnUiThread(() -> {
                    registrationRunning.set(false);
                    mutualLikeRegistered = true;
                    renderDailyTasks(heartbeat);
                    mutualLikeStatus.setText("账号已加入今日互赞");
                    mutualLikeButton.setEnabled(
                        qqLoggedIn && !mutualLikeRunning
                    );
                });
            } catch (MutualLikeServerClient.ServerException error) {
                runOnUiThread(() -> {
                    registrationRunning.set(false);
                    mutualLikeRegistered = false;
                    mutualLikeButton.setEnabled(false);
                    if (error.statusCode == 403
                        && ("not_allowlisted".equals(error.errorCode)
                            || "allowlist_disabled".equals(error.errorCode))) {
                        mutualLikeStatus.setText("当前账号未加入测试名单");
                    } else if (error.statusCode == 409
                        && "binding_conflict".equals(error.errorCode)) {
                        mutualLikeStatus.setText(
                            "设备绑定不一致，请联系管理员重置绑定"
                        );
                    } else {
                        mutualLikeStatus.setText("互赞服务暂时不可用");
                    }
                    appendLog("互赞注册失败：" + safeMessage(error));
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    registrationRunning.set(false);
                    mutualLikeRegistered = false;
                    mutualLikeButton.setEnabled(false);
                    mutualLikeStatus.setText("互赞服务暂时不可用");
                    appendLog("互赞注册失败：" + safeMessage(error));
                });
            }
        });
    }

    private void renderDailyTasks(JSONObject response) {
        JSONObject tasks = response == null
            ? null
            : response.optJSONObject("tasks");
        if (tasks == null) {
            return;
        }
        pendingCount.setText(String.valueOf(tasks.optInt("pending", 0)));
        completedCount.setText(
            String.valueOf(tasks.optInt("succeeded", 0))
        );
        abnormalCount.setText(
            String.valueOf(
                tasks.optInt("failed", 0)
                    + tasks.optInt("uncertain", 0)
            )
        );
    }

    private void beginMutualLikeSync() {
        if (mutualLikeRunning) {
            return;
        }
        String serverUrl = AppSettings.getServerUrl(this);
        if (serverUrl.isEmpty()) {
            mutualLikeStatus.setText("互赞服务暂未开放");
            taskRecordText.setText("当前安装包还没有配置任务服务器");
            return;
        }
        String oneBotToken = AppSettings.getOneBotToken(this);
        if (oneBotToken.isEmpty()) {
            Toast.makeText(this, "请先完成 QQ 登录", Toast.LENGTH_SHORT).show();
            return;
        }
        AtomicBoolean cancellation = new AtomicBoolean(false);
        mutualLikeCancellation = cancellation;
        mutualLikeRunning = true;
        mutualLikeButton.setEnabled(false);
        serverUrlInput.setEnabled(false);
        mutualLikeStatus.setText("正在获取互赞任务…");
        taskRecordText.setText("正在同步今日任务");

        networkExecutor.execute(() -> {
            try {
                MutualLikeServerClient server =
                    new MutualLikeServerClient(serverUrl);
                MutualLikeExecutor executor = new MutualLikeExecutor(
                    new NapCatOneBotClient(oneBotToken),
                    server,
                    new TaskJournal(getApplicationContext()),
                    AppSettings.getOrCreateInstallId(getApplicationContext()),
                    getAppVersion(),
                    AppSettings.getMobileAccessToken(
                        getApplicationContext(),
                        serverUrl
                    ),
                    cancellation,
                    message -> runOnUiThread(() -> {
                        mutualLikeStatus.setText(friendlyProgress(message));
                        appendLog(message);
                    }),
                    accessToken -> AppSettings.saveMobileAccessToken(
                        getApplicationContext(),
                        serverUrl,
                        accessToken
                    )
                );
                MutualLikeExecutor.Summary summary = executor.runOnce();
                runOnUiThread(() -> finishMutualLikeSync(summary));
            } catch (Exception error) {
                appendLog("互赞同步失败：" + safeMessage(error));
                runOnUiThread(this::finishMutualLikeSyncFailed);
            }
        });
    }

    private void finishMutualLikeSync(MutualLikeExecutor.Summary summary) {
        mutualLikeRunning = false;
        mutualLikeCancellation = null;
        serverUrlInput.setEnabled(true);
        mutualLikeButton.setEnabled(
            runtimeRunning && qqLoggedIn && mutualLikeRegistered
        );
        int abnormal = summary.failedToday + summary.uncertainToday;
        pendingCount.setText(String.valueOf(summary.pendingToday));
        completedCount.setText(String.valueOf(summary.succeededToday));
        abnormalCount.setText(String.valueOf(abnormal));
        mutualLikeStatus.setText(
            summary.leased == 0
                ? "今天暂时没有待执行任务"
                : "本次互赞任务已完成"
        );
        taskRecordText.setText(
            "本次领取 " + summary.leased + " 条\n"
                + "完成 " + summary.succeeded + " 条"
                + ((summary.failed + summary.uncertain) > 0
                    ? " · 异常 "
                        + (summary.failed + summary.uncertain)
                        + " 条"
                    : "")
                + (summary.reportFailed > 0
                    ? " · 待回传 " + summary.reportFailed + " 条"
                    : "")
        );
        appendLog("互赞同步完成");
    }

    private void finishMutualLikeSyncFailed() {
        mutualLikeRunning = false;
        mutualLikeCancellation = null;
        serverUrlInput.setEnabled(true);
        mutualLikeButton.setEnabled(
            runtimeRunning && qqLoggedIn && mutualLikeRegistered
        );
        mutualLikeStatus.setText("互赞暂时无法开始，请稍后重试");
        taskRecordText.setText("本次任务未完成，可稍后重新尝试");
    }

    private String friendlyProgress(String message) {
        if (message == null) {
            return "正在同步任务…";
        }
        if (message.contains("确认登录")) {
            return "正在确认当前账号…";
        }
        if (message.contains("服务器下发")) {
            return "任务已获取，正在依次执行…";
        }
        if (message.contains("点赞任务")) {
            return "正在完成互赞任务…";
        }
        if (message.contains("补报")) {
            return "正在同步上次任务结果…";
        }
        return "正在同步今日任务…";
    }

    private void checkForUpdates(boolean manual) {
        if (!updateCheckRunning.compareAndSet(false, true)) {
            return;
        }
        checkUpdateButton.setEnabled(false);
        checkUpdateButton.setText("检查中…");
        String serverUrl = AppSettings.getServerUrl(this);
        networkExecutor.execute(() -> {
            try {
                AppUpdateClient client = new AppUpdateClient(serverUrl);
                AppUpdateClient.CheckResult result = client.check(
                    getAppVersionCode(),
                    getAppVersion()
                );
                runOnUiThread(() -> {
                    updateCheckRunning.set(false);
                    checkUpdateButton.setEnabled(true);
                    checkUpdateButton.setText("检查更新");
                    if (result.available && result.release != null) {
                        versionText.setText(
                            "v" + getAppVersion()
                                + " · 可更新 "
                                + result.release.versionName
                        );
                        showUpdateDialog(result.release);
                    } else {
                        versionText.setText("v" + getAppVersion() + " · 已是最新");
                        if (manual) {
                            Toast.makeText(
                                this,
                                "当前已是最新版本",
                                Toast.LENGTH_SHORT
                            ).show();
                        }
                    }
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    updateCheckRunning.set(false);
                    checkUpdateButton.setEnabled(true);
                    checkUpdateButton.setText("检查更新");
                    versionText.setText("v" + getAppVersion());
                    if (manual) {
                        Toast.makeText(
                            this,
                            "检查更新失败：" + safeMessage(error),
                            Toast.LENGTH_LONG
                        ).show();
                    }
                    appendLog("检查更新失败：" + safeMessage(error));
                });
            }
        });
    }

    private void showUpdateDialog(AppUpdateClient.Release release) {
        StringBuilder message = new StringBuilder()
            .append("当前版本：")
            .append(getAppVersion())
            .append("\n最新版本：")
            .append(release.versionName);
        if (!release.changelog.isEmpty()) {
            message.append("\n\n");
            for (String item : release.changelog) {
                message.append("• ").append(item).append("\n");
            }
        }
        message.append("\n下载后会打开 Android 系统安装确认页。");
        AlertDialog.Builder builder = new AlertDialog.Builder(this)
            .setTitle(release.title)
            .setMessage(message.toString().trim())
            .setPositiveButton(
                "下载更新",
                (dialog, which) -> downloadUpdate(release)
            );
        if (!release.forceUpdate) {
            builder.setNegativeButton("稍后", null);
        }
        AlertDialog dialog = builder.create();
        dialog.setCancelable(!release.forceUpdate);
        dialog.setCanceledOnTouchOutside(!release.forceUpdate);
        dialog.show();
    }

    private void downloadUpdate(AppUpdateClient.Release release) {
        checkUpdateButton.setEnabled(false);
        showOperationProgress(
            "正在下载 App 更新",
            0,
            "准备下载互赞助手 " + release.versionName,
            "0 B / " + formatBytes(release.sizeBytes)
        );
        AtomicInteger lastProgress = new AtomicInteger(-1);
        String serverUrl = AppSettings.getServerUrl(this);
        networkExecutor.execute(() -> {
            try {
                AppUpdateClient client = new AppUpdateClient(serverUrl);
                File apk = client.download(
                    getApplicationContext(),
                    release,
                    (downloadedBytes, totalBytes) -> {
                        int percent = totalBytes <= 0
                            ? 0
                            : (int) Math.min(
                                99,
                                downloadedBytes * 100L / totalBytes
                            );
                        if (lastProgress.getAndSet(percent) == percent) {
                            return;
                        }
                        runOnUiThread(() -> showOperationProgress(
                            "正在下载 App 更新",
                            percent,
                            "正在下载互赞助手 " + release.versionName,
                            formatBytes(downloadedBytes)
                                + " / "
                                + formatBytes(totalBytes)
                        ));
                    }
                );
                runOnUiThread(() -> {
                    checkUpdateButton.setEnabled(true);
                    showOperationProgress(
                        "更新包已准备好",
                        100,
                        "安全校验已通过",
                        "接下来由 Android 系统确认安装"
                    );
                    requestUpdateInstall(apk);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    checkUpdateButton.setEnabled(true);
                    showOperationFailure(
                        "App 更新失败",
                        safeMessage(error)
                    );
                    Toast.makeText(
                        this,
                        "更新失败：" + safeMessage(error),
                        Toast.LENGTH_LONG
                    ).show();
                });
            }
        });
    }

    private void requestUpdateInstall(File apk) {
        savePendingUpdatePath(apk);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            && !getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(this)
                .setTitle("允许安装 App 更新")
                .setMessage(
                    "Android 要求首次更新时允许本 App 安装更新包。"
                        + "开启后返回，系统会继续显示安装确认页。"
                )
                .setPositiveButton(
                    "去开启",
                    (dialog, which) -> openUnknownSourceSettings()
                )
                .setNegativeButton("稍后", null)
                .show();
            return;
        }
        startUpdateInstaller(apk);
    }

    private void openUnknownSourceSettings() {
        try {
            Intent intent = new Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + getPackageName())
            );
            startActivity(intent);
        } catch (RuntimeException error) {
            Toast.makeText(
                this,
                "请在系统设置中允许本 App 安装未知应用",
                Toast.LENGTH_LONG
            ).show();
        }
    }

    private void resumePendingUpdateInstall() {
        SharedPreferences preferences = getSharedPreferences(
            UPDATE_PREFERENCES,
            MODE_PRIVATE
        );
        String path = preferences.getString(KEY_PENDING_UPDATE_PATH, "");
        if (path == null || path.trim().isEmpty()) {
            return;
        }
        File apk = new File(path);
        if (!apk.isFile()) {
            preferences.edit().remove(KEY_PENDING_UPDATE_PATH).apply();
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            && !getPackageManager().canRequestPackageInstalls()) {
            return;
        }
        startUpdateInstaller(apk);
    }

    private void savePendingUpdatePath(File apk) {
        getSharedPreferences(UPDATE_PREFERENCES, MODE_PRIVATE)
            .edit()
            .putString(KEY_PENDING_UPDATE_PATH, apk.getAbsolutePath())
            .apply();
    }

    private void startUpdateInstaller(File apk) {
        try {
            Uri uri = UpdateFileProvider.uriFor(this, apk);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(
                uri,
                "application/vnd.android.package-archive"
            );
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            startActivity(intent);
            getSharedPreferences(UPDATE_PREFERENCES, MODE_PRIVATE)
                .edit()
                .remove(KEY_PENDING_UPDATE_PATH)
                .apply();
        } catch (RuntimeException error) {
            showOperationFailure(
                "无法打开系统安装器",
                safeMessage(error)
            );
        }
    }

    private void showOperationProgress(
        String title,
        int percent,
        String detail,
        String meta
    ) {
        int safePercent = Math.max(0, Math.min(100, percent));
        operationProgressCard.setVisibility(View.VISIBLE);
        operationProgressTitle.setText(title);
        operationProgressPercent.setText(safePercent + "%");
        operationProgressDetail.setText(detail);
        operationProgressMeta.setText(meta);
        operationProgressBar.setProgress(safePercent);
    }

    private void showOperationFailure(String title, String detail) {
        operationProgressCard.setVisibility(View.VISIBLE);
        operationProgressTitle.setText(title);
        operationProgressPercent.setText("失败");
        operationProgressDetail.setText(detail);
        operationProgressMeta.setText("请检查网络后重试");
        operationProgressBar.setProgress(0);
    }

    private String getAppVersion() {
        try {
            return getPackageManager()
                .getPackageInfo(getPackageName(), 0)
                .versionName;
        } catch (PackageManager.NameNotFoundException error) {
            return "unknown";
        }
    }

    private int getAppVersionCode() {
        try {
            android.content.pm.PackageInfo info = getPackageManager()
                .getPackageInfo(getPackageName(), 0);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                return (int) Math.min(
                    Integer.MAX_VALUE,
                    info.getLongVersionCode()
                );
            }
            return info.versionCode;
        } catch (PackageManager.NameNotFoundException error) {
            return 0;
        }
    }

    private String formatBytes(long bytes) {
        if (bytes <= 0) {
            return "0 B";
        }
        double value = bytes;
        String[] units = {"B", "KB", "MB", "GB"};
        int unit = 0;
        while (value >= 1024 && unit < units.length - 1) {
            value /= 1024;
            unit++;
        }
        return unit == 0
            ? String.format(Locale.getDefault(), "%.0f %s", value, units[unit])
            : String.format(Locale.getDefault(), "%.1f %s", value, units[unit]);
    }

    private String maskQq(String qq) {
        if (qq == null || qq.length() <= 4) {
            return "****";
        }
        return qq.substring(0, 2) + "****" + qq.substring(qq.length() - 2);
    }

    private void openWebUi(String rawConfig) {
        String token = "";
        try {
            JSONObject config = extractJsonObject(rawConfig);
            token = config.optString("token", "");
        } catch (JSONException error) {
            appendLog("WebUI 配置解析失败：" + safeMessage(error));
        }
        if (token.trim().isEmpty()) {
            appendLog("WebUI 尚未准备好，请稍后重新点击“打开 QQ 登录”。");
            return;
        }
        Intent intent = new Intent(this, NapCatWebUiActivity.class);
        intent.putExtra(NapCatWebUiActivity.EXTRA_TOKEN, token);
        startActivity(intent);
    }

    private JSONObject extractJsonObject(String value) throws JSONException {
        int start = value.indexOf('{');
        int end = value.lastIndexOf('}');
        if (start < 0 || end <= start) {
            throw new JSONException("没有找到 JSON 配置");
        }
        return new JSONObject(value.substring(start, end + 1));
    }

    private void setStatusColor(TextView view, boolean ready) {
        view.setTextColor(getColor(ready ? R.color.green_ok : R.color.gray_700));
    }

    private void appendLog(String message) {
        if (message == null || message.trim().isEmpty()) {
            return;
        }
        String timestamp = new SimpleDateFormat(
            "HH:mm:ss",
            Locale.getDefault()
        ).format(new Date());
        String previous = logView == null ? "" : logView.getText().toString();
        if ("等待检查运行环境…".equals(previous)) {
            previous = "";
        }
        String next = previous + (previous.isEmpty() ? "" : "\n")
            + "[" + timestamp + "] " + message;
        if (next.length() > 12000) {
            next = next.substring(next.length() - 12000);
        }
        if (logView != null) {
            logView.setText(next);
        }
    }

    private String safeMessage(Exception error) {
        String message = error.getMessage();
        return message == null || message.trim().isEmpty()
            ? error.getClass().getSimpleName()
            : message;
    }
}
