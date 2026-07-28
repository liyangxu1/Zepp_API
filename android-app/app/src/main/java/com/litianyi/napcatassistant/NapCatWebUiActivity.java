package com.litianyi.napcatassistant;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/** 只展示二维码和登录结果的原生 QQ 登录页。 */
public final class NapCatWebUiActivity extends Activity {
    public static final String EXTRA_TOKEN = "webui_token";
    private static final String READ_QR_COMMAND =
        "proot-distro sh napcat -- bash -lc "
            + "\"base64 -w0 "
            + "/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png"
            + " 2>/dev/null || true\"";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final AtomicBoolean pollRunning = new AtomicBoolean(false);
    private NapCatLoginClient loginClient;
    private ImageView qrImage;
    private ProgressBar progress;
    private TextView statusText;
    private Button refreshButton;
    private boolean stopped;

    private final BroadcastReceiver resultReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if ("qr_image".equals(
                intent.getStringExtra(TermuxResultService.EXTRA_OPERATION)
            )) {
                renderQrImage();
            }
        }
    };

    private final Runnable pollRunnable = this::pollLogin;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_napcat_webui);
        qrImage = findViewById(R.id.qrImage);
        progress = findViewById(R.id.loginProgress);
        statusText = findViewById(R.id.loginPageStatus);
        refreshButton = findViewById(R.id.refreshQrButton);
        findViewById(R.id.backButton).setOnClickListener(view -> finish());
        refreshButton.setOnClickListener(view -> refreshQrCode());

        String token = getIntent().getStringExtra(EXTRA_TOKEN);
        try {
            loginClient = new NapCatLoginClient(token);
        } catch (IllegalArgumentException error) {
            showFailure("登录二维码暂时无法打开");
        }
    }

    @Override
    protected void onStart() {
        super.onStart();
        stopped = false;
        IntentFilter filter = new IntentFilter(
            TermuxResultService.ACTION_RESULT
        );
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(resultReceiver, filter, RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(resultReceiver, filter);
        }
        if (loginClient != null) {
            refreshQrCode();
        }
    }

    @Override
    protected void onStop() {
        stopped = true;
        handler.removeCallbacks(pollRunnable);
        unregisterReceiver(resultReceiver);
        super.onStop();
    }

    private void refreshQrCode() {
        if (loginClient == null) {
            return;
        }
        progress.setVisibility(ProgressBar.VISIBLE);
        refreshButton.setEnabled(false);
        statusText.setText("正在准备二维码…");
        executor.execute(() -> {
            try {
                if (loginClient.isLoggedIn()) {
                    runOnUiThread(this::showLoginSuccess);
                    return;
                }
                loginClient.refreshQrCode();
                Thread.sleep(1200);
                runOnUiThread(this::requestQrImage);
            } catch (Exception error) {
                runOnUiThread(() -> showFailure("二维码准备失败，请稍后重试"));
            }
        });
    }

    private void requestQrImage() {
        if (stopped) {
            return;
        }
        try {
            TermuxBridge.run(this, "qr_image", READ_QR_COMMAND, true);
        } catch (RuntimeException error) {
            showFailure("二维码读取失败，请稍后重试");
        }
    }

    private void renderQrImage() {
        SharedPreferences preferences = getSharedPreferences(
            TermuxResultService.PREFERENCES,
            MODE_PRIVATE
        );
        String raw = preferences.getString("last_stdout", "");
        if (raw == null || raw.trim().isEmpty()) {
            showFailure("二维码还没有准备好，请点击刷新");
            return;
        }
        try {
            byte[] bytes = Base64.decode(raw.trim(), Base64.DEFAULT);
            Bitmap bitmap = BitmapFactory.decodeByteArray(
                bytes,
                0,
                bytes.length
            );
            if (bitmap == null) {
                throw new IllegalArgumentException("二维码图片无效");
            }
            qrImage.setImageBitmap(bitmap);
            progress.setVisibility(ProgressBar.GONE);
            refreshButton.setEnabled(true);
            statusText.setText("请使用手机 QQ 扫描二维码");
            schedulePoll();
        } catch (IllegalArgumentException error) {
            showFailure("二维码读取失败，请点击刷新");
        }
    }

    private void schedulePoll() {
        handler.removeCallbacks(pollRunnable);
        if (!stopped) {
            handler.postDelayed(pollRunnable, 1800);
        }
    }

    private void pollLogin() {
        if (stopped || loginClient == null
            || !pollRunning.compareAndSet(false, true)) {
            return;
        }
        executor.execute(() -> {
            boolean loggedIn = false;
            try {
                loggedIn = loginClient.isLoggedIn();
            } catch (Exception ignored) {
                // 登录轮询失败时保持二维码，不向用户暴露内部错误。
            }
            boolean finalLoggedIn = loggedIn;
            runOnUiThread(() -> {
                pollRunning.set(false);
                if (finalLoggedIn) {
                    showLoginSuccess();
                } else {
                    schedulePoll();
                }
            });
        });
    }

    private void showLoginSuccess() {
        if (stopped) {
            return;
        }
        handler.removeCallbacks(pollRunnable);
        progress.setVisibility(ProgressBar.GONE);
        refreshButton.setEnabled(false);
        statusText.setText("登录成功");
        setResult(RESULT_OK);
        handler.postDelayed(this::finish, 900);
    }

    private void showFailure(String message) {
        progress.setVisibility(ProgressBar.GONE);
        refreshButton.setEnabled(true);
        statusText.setText(message);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        executor.shutdownNow();
        super.onDestroy();
    }
}
