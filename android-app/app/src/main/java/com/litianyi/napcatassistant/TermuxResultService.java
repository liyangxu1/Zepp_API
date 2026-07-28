package com.litianyi.napcatassistant;

import android.app.IntentService;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;

/** 接收 Termux 命令结果并保存给主页面。 */
public final class TermuxResultService extends IntentService {
    public static final String EXTRA_OPERATION = "operation";
    public static final String EXTRA_EXECUTION_ID = "execution_id";
    public static final String EXTRA_STDOUT = "stdout";
    public static final String EXTRA_STDERR = "stderr";
    public static final String EXTRA_ERROR = "error";
    public static final String EXTRA_EXIT_CODE = "exit_code";
    public static final String ACTION_RESULT =
        "com.litianyi.napcatassistant.COMMAND_RESULT";
    public static final String PREFERENCES = "termux_results";

    public TermuxResultService() {
        super("TermuxResultService");
    }

    @Override
    protected void onHandleIntent(Intent intent) {
        if (intent == null) {
            return;
        }
        String operation = intent.getStringExtra(EXTRA_OPERATION);
        if (operation == null || operation.trim().isEmpty()) {
            operation = "unknown";
        }
        Bundle result = intent.getBundleExtra("result");
        String stdout = "";
        String stderr = "";
        String errorMessage = "";
        int exitCode = -1;
        if (result != null) {
            stdout = result.getString("stdout", "");
            stderr = result.getString("stderr", "");
            errorMessage = result.getString("errmsg", "");
            exitCode = result.getInt("exitCode", -1);
        }

        SharedPreferences preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE);
        preferences.edit()
            .putString("last_operation", operation)
            .putString("last_stdout", trimResult(stdout))
            .putString("last_stderr", trimResult(stderr))
            .putString("last_error", trimResult(errorMessage))
            .putInt("last_exit_code", exitCode)
            .putLong("last_result_at", System.currentTimeMillis())
            .apply();

        Intent broadcast = new Intent(ACTION_RESULT);
        broadcast.setPackage(getPackageName());
        broadcast.putExtra(EXTRA_OPERATION, operation);
        broadcast.putExtra(EXTRA_STDOUT, trimResult(stdout));
        broadcast.putExtra(EXTRA_STDERR, trimResult(stderr));
        broadcast.putExtra(EXTRA_ERROR, trimResult(errorMessage));
        broadcast.putExtra(EXTRA_EXIT_CODE, exitCode);
        sendBroadcast(broadcast);
    }

    private String trimResult(String value) {
        if (value == null) {
            return "";
        }
        int maxLength = 12000;
        if (value.length() <= maxLength) {
            return value;
        }
        return value.substring(value.length() - maxLength);
    }
}
