package com.litianyi.napcatassistant;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;

/** 只向系统安装器临时提供已校验的 App 更新包。 */
public final class UpdateFileProvider extends ContentProvider {
    public static Uri uriFor(android.content.Context context, File apk) {
        return new Uri.Builder()
            .scheme("content")
            .authority(context.getPackageName() + ".mutual_like_updates")
            .appendPath(apk.getName())
            .build();
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public String getType(Uri uri) {
        return "application/vnd.android.package-archive";
    }

    @Override
    public Cursor query(
        Uri uri,
        String[] projection,
        String selection,
        String[] selectionArgs,
        String sortOrder
    ) {
        File apk = resolveApk(uri);
        MatrixCursor cursor = new MatrixCursor(
            new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}
        );
        cursor.addRow(new Object[]{apk.getName(), apk.length()});
        return cursor;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode)
        throws FileNotFoundException {
        if (!"r".equals(mode)) {
            throw new FileNotFoundException("更新包仅允许读取");
        }
        return ParcelFileDescriptor.open(
            resolveApk(uri),
            ParcelFileDescriptor.MODE_READ_ONLY
        );
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("不支持写入");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("不支持删除");
    }

    @Override
    public int update(
        Uri uri,
        ContentValues values,
        String selection,
        String[] selectionArgs
    ) {
        throw new UnsupportedOperationException("不支持更新");
    }

    private File resolveApk(Uri uri) {
        if (getContext() == null
            || uri == null
            || uri.getLastPathSegment() == null) {
            throw new SecurityException("更新包地址无效");
        }
        String name = uri.getLastPathSegment();
        if (!name.matches("mutual-like-[0-9]+\\.apk")) {
            throw new SecurityException("更新包名称无效");
        }
        try {
            File root = new File(
                getContext().getCacheDir(),
                "updates"
            ).getCanonicalFile();
            File apk = new File(root, name).getCanonicalFile();
            if (!apk.getParentFile().equals(root) || !apk.isFile()) {
                throw new SecurityException("更新包不存在");
            }
            return apk;
        } catch (IOException error) {
            throw new SecurityException("无法读取更新包", error);
        }
    }
}
