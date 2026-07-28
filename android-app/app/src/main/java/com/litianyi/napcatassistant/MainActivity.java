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
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONException;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/** NapCat 官方 Termux 运行链路的最小控制页面。 */
public final class MainActivity extends Activity {
    private static final int REQUEST_RUN_COMMAND = 2001;
    private static final String OFFICIAL_INSTALL_SCRIPT =
        "https://raw.githubusercontent.com/NapNeko/NapCat-Installer/"
            + "main/script/install.termux.sh";
    private static final String OFFICIAL_LINUX_INSTALL_SCRIPT =
        "https://raw.githubusercontent.com/NapNeko/NapCat-Installer/"
            + "main/script/install.sh";
    private static final String DOCUMENT_MIRROR_LINUX_INSTALL_SCRIPT =
        "https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh";
    private static final String INSTALL_COMMAND =
        "curl -fL --retry 3 -o \"$HOME/napcat.termux.sh\" " + OFFICIAL_INSTALL_SCRIPT
            + " && sed -i 's#" + DOCUMENT_MIRROR_LINUX_INSTALL_SCRIPT
            + "#" + OFFICIAL_LINUX_INSTALL_SCRIPT + "#g' \"$HOME/napcat.termux.sh\""
            + " && bash \"$HOME/napcat.termux.sh\"";
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
    private EditText serverUrlInput;
    private Button primaryButton;
    private Button loginButton;
    private Button oneBotButton;
    private Button mutualLikeButton;
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
    private final ExecutorService networkExecutor =
        Executors.newSingleThreadExecutor();

    private final BroadcastReceiver resultReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            renderLastResult();
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        bindViews();
        bindActions();
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
        serverUrlInput = findViewById(R.id.serverUrlInput);
        primaryButton = findViewById(R.id.primaryButton);
        loginButton = findViewById(R.id.loginButton);
        oneBotButton = findViewById(R.id.oneBotButton);
        mutualLikeButton = findViewById(R.id.mutualLikeButton);
        serverUrlInput.setText(AppSettings.getServerUrl(this));
    }

    private void bindActions() {
        primaryButton.setOnClickListener(view -> onPrimaryAction());
        loginButton.setOnClickListener(view -> readWebUiToken());
        findViewById(R.id.checkButton).setOnClickListener(view -> probeRuntime());
        findViewById(R.id.termuxButton).setOnClickListener(view -> openTermux());
        oneBotButton.setOnClickListener(view -> confirmConfigureOneBot());
        mutualLikeButton.setOnClickListener(view -> beginMutualLikeSync());
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
        try {
            TermuxBridge.run(this, "install", INSTALL_COMMAND, true);
        } catch (RuntimeException error) {
            primaryButton.setEnabled(true);
            handleCommandStartError(error);
        }
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

    private void handleCommandStartError(RuntimeException error) {
        appendLog("Termux 命令启动失败：" + safeMessage(error));
        showTermuxSetupDialog();
    }

    private void renderLastResult() {
        SharedPreferences preferences = getSharedPreferences(
            TermuxResultService.PREFERENCES,
            MODE_PRIVATE
        );
        String operation = preferences.getString("last_operation", "");
        String stdout = preferences.getString("last_stdout", "");
        String stderr = preferences.getString("last_stderr", "");
        String error = preferences.getString("last_error", "");
        int exitCode = preferences.getInt("last_exit_code", -1);
        primaryButton.setEnabled(true);

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
            appendLog(exitCode == 0 ? "官方安装脚本执行完成。" : "安装未完成，请查看 Termux 输出。");
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

    private String getAppVersion() {
        try {
            return getPackageManager()
                .getPackageInfo(getPackageName(), 0)
                .versionName;
        } catch (PackageManager.NameNotFoundException error) {
            return "unknown";
        }
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
