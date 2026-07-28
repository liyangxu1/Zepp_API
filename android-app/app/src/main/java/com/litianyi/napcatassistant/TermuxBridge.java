package com.litianyi.napcatassistant;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

import java.util.concurrent.atomic.AtomicInteger;

/** 封装 Termux 官方 RUN_COMMAND 接口。 */
public final class TermuxBridge {
    public static final String TERMUX_PACKAGE = "com.termux";
    public static final String RUN_COMMAND_PERMISSION = "com.termux.permission.RUN_COMMAND";
    public static final String TERMUX_HOME = "/data/data/com.termux/files/home";
    private static final String TERMUX_BASH = "/data/data/com.termux/files/usr/bin/bash";
    private static final AtomicInteger EXECUTION_ID = new AtomicInteger(1000);

    private TermuxBridge() {
    }

    private static boolean isEmbedded(Context context) {
        return TERMUX_PACKAGE.equals(context.getPackageName());
    }

    public static boolean isInstalled(Context context) {
        if (isEmbedded(context)) {
            return true;
        }
        try {
            context.getPackageManager().getPackageInfo(TERMUX_PACKAGE, 0);
            return true;
        } catch (PackageManager.NameNotFoundException ignored) {
            return false;
        }
    }

    public static boolean hasRunPermission(Context context) {
        if (isEmbedded(context)) {
            return true;
        }
        return context.checkSelfPermission(RUN_COMMAND_PERMISSION)
            == PackageManager.PERMISSION_GRANTED;
    }

    public static void requestRunPermission(Activity activity, int requestCode) {
        if (isEmbedded(activity)) {
            return;
        }
        activity.requestPermissions(new String[]{RUN_COMMAND_PERMISSION}, requestCode);
    }

    public static int run(
        Context context,
        String operation,
        String command,
        boolean background
    ) {
        int executionId = EXECUTION_ID.incrementAndGet();
        Intent resultIntent = new Intent(context, TermuxResultService.class);
        resultIntent.putExtra(TermuxResultService.EXTRA_OPERATION, operation);
        resultIntent.putExtra(TermuxResultService.EXTRA_EXECUTION_ID, executionId);
        int flags = PendingIntent.FLAG_ONE_SHOT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            flags |= PendingIntent.FLAG_MUTABLE;
        }
        PendingIntent pendingIntent = PendingIntent.getService(
            context,
            executionId,
            resultIntent,
            flags
        );

        Intent intent = new Intent();
        intent.setComponent(new ComponentName(
            isEmbedded(context) ? context.getPackageName() : TERMUX_PACKAGE,
            "com.termux.app.RunCommandService"
        ));
        intent.setAction("com.termux.RUN_COMMAND");
        intent.putExtra("com.termux.RUN_COMMAND_PATH", TERMUX_BASH);
        intent.putExtra(
            "com.termux.RUN_COMMAND_ARGUMENTS",
            new String[]{"-lc", command}
        );
        intent.putExtra("com.termux.RUN_COMMAND_WORKDIR", TERMUX_HOME);
        intent.putExtra("com.termux.RUN_COMMAND_BACKGROUND", background);
        intent.putExtra("com.termux.RUN_COMMAND_SESSION_ACTION", "0");
        intent.putExtra("com.termux.RUN_COMMAND_COMMAND_LABEL", operation);
        intent.putExtra("com.termux.RUN_COMMAND_PENDING_INTENT", pendingIntent);
        context.startService(intent);
        return executionId;
    }

    public static Intent launchIntent(Context context) {
        return context.getPackageManager().getLaunchIntentForPackage(TERMUX_PACKAGE);
    }
}
