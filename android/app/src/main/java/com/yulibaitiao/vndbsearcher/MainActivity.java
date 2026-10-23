package com.yulibaitiao.vndbsearcher;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.util.Base64;
import android.os.Build;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;

public class MainActivity extends Activity {
    private WebView webView;
    private android.content.SharedPreferences prefs;
    private String tagsCache;
    private String zhCache;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("vndb_searcher", MODE_PRIVATE);
        webView = new WebView(this);
        setContentView(webView);

        if (Build.VERSION.SDK_INT >= 21) {
            getWindow().setStatusBarColor(0xFFFF8FAB);
            getWindow().setNavigationBarColor(0xFFFFF3F6);
        }

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        webView.setWebViewClient(new WebViewClient());
        webView.addJavascriptInterface(new Bridge(), "Android");

        String html = readAsset("www/index.html");
        html = html.replace("/*__CSS__*/", readAsset("www/app.css"));
        html = html.replace("/*__JS__*/", readAsset("www/app.js"));
        html = html.replace("/*__ICON__*/", iconDataUri());
        webView.loadDataWithBaseURL(
                "https://vndb.searcher.local/",
                html,
                "text/html",
                "UTF-8",
                null
        );
    }

    @Override
    public void onBackPressed() {
        webView.evaluateJavascript(
                "(function(){return window.handleBack?window.handleBack():false;})()",
                value -> {
                    if ("false".equals(value) || "null".equals(value)) {
                        runOnUiThread(MainActivity.this::finish);
                    }
                }
        );
    }

    private String iconDataUri() {
        try (InputStream in = getAssets().open("www/icon.png")) {
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) >= 0) bos.write(buf, 0, n);
            return "data:image/png;base64," + Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP);
        } catch (Exception e) {
            return "";
        }
    }

    private String readAsset(String name) {
        try (InputStream in = getAssets().open(name);
             BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            StringBuilder sb = new StringBuilder();
            char[] buf = new char[8192];
            int n;
            while ((n = reader.read(buf)) >= 0) {
                sb.append(buf, 0, n);
            }
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    private String httpGet(String urlStr, String headersJson) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(urlStr);
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(20000);
            conn.setRequestMethod("GET");
            conn.setRequestProperty("Accept", "application/json");
            conn.setRequestProperty("User-Agent", "VNDB-Searcher/1.0 (Android; author: yulibaitiao)");
            applyHeaders(conn, headersJson);
            int code = conn.getResponseCode();
            InputStream stream = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
            return packHttp(code, readStream(stream));
        } catch (Exception e) {
            return packError(e);
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private void applyHeaders(HttpURLConnection conn, String headersJson) throws Exception {
        if (headersJson == null || headersJson.length() == 0 || "null".equals(headersJson)) return;
        JSONObject headers = new JSONObject(headersJson);
        Iterator<String> keys = headers.keys();
        while (keys.hasNext()) {
            String k = keys.next();
            conn.setRequestProperty(k, headers.optString(k, ""));
        }
    }

    private String packHttp(int code, String text) throws Exception {
        JSONObject out = new JSONObject();
        out.put("ok", code >= 200 && code < 300);
        out.put("status", code);
        out.put("body", text == null ? "" : text);
        if (code < 200 || code >= 300) out.put("error", "HTTP " + code);
        return out.toString();
    }

    private String packError(Exception e) {
        try {
            JSONObject out = new JSONObject();
            out.put("ok", false);
            out.put("status", 0);
            out.put("body", "");
            String msg = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
            out.put("error", msg);
            return out.toString();
        } catch (Exception ignored) {
            return "{\"ok\":false,\"status\":0,\"error\":\"request failed\",\"body\":\"\"}";
        }
    }

    private String httpPost(String urlStr, String body, String headersJson) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(urlStr);
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(30000);
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setRequestProperty("Accept", "application/json");
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            conn.setRequestProperty("User-Agent", "VNDB-Searcher/1.0 (Android; author: yulibaitiao)");
            applyHeaders(conn, headersJson);
            byte[] bytes = body == null ? new byte[0] : body.getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(bytes);
            }
            int code = conn.getResponseCode();
            InputStream stream = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
            return packHttp(code, readStream(stream));
        } catch (Exception e) {
            return packError(e);
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private static String readStream(InputStream stream) throws Exception {
        if (stream == null) {
            return "";
        }
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = stream.read(buf)) >= 0) {
            bos.write(buf, 0, n);
        }
        stream.close();
        return bos.toString(StandardCharsets.UTF_8.name());
    }

    private void deliver(String id, String payload) {
        String js = "window.__httpDone(" + JSONObject.quote(id) + "," + JSONObject.quote(payload) + ")";
        runOnUiThread(() -> webView.evaluateJavascript(js, null));
    }

    public class Bridge {
        @JavascriptInterface
        public String getApiKey() {
            return prefs.getString("api_key", "");
        }

        @JavascriptInterface
        public void setApiKey(String key) {
            prefs.edit().putString("api_key", key == null ? "" : key.trim()).apply();
        }

        @JavascriptInterface
        public String getPref(String key) {
            return prefs.getString("p_" + key, "");
        }

        @JavascriptInterface
        public void setPref(String key, String value) {
            prefs.edit().putString("p_" + key, value == null ? "" : value).apply();
        }

        @JavascriptInterface
        public String getFavorites() {
            return prefs.getString("favorites", "[]");
        }

        @JavascriptInterface
        public void setFavorites(String json) {
            prefs.edit().putString("favorites", json == null ? "[]" : json).apply();
        }

        @JavascriptInterface
        public String getAsset(String name) {
            if ("www/tags_min.json".equals(name)) {
                if (tagsCache == null) {
                    tagsCache = readAsset(name);
                }
                return tagsCache;
            }
            if ("www/tag_zh.json".equals(name)) {
                if (zhCache == null) {
                    zhCache = readAsset(name);
                }
                return zhCache;
            }
            return readAsset(name);
        }

        @JavascriptInterface
        public void httpPostAsync(String id, String url, String body, String headersJson) {
            new Thread(() -> deliver(id, httpPost(url, body, headersJson))).start();
        }

        @JavascriptInterface
        public void httpGetAsync(String id, String url, String headersJson) {
            new Thread(() -> deliver(id, httpGet(url, headersJson))).start();
        }

        @JavascriptInterface
        public void openUrl(String url) {
            if (url == null) return;
            String trimmed = url.trim();
            String lower = trimmed.toLowerCase();
            if (!(lower.startsWith("https://") || lower.startsWith("http://"))) return;
            int query = lower.indexOf('?');
            String path = query >= 0 ? lower.substring(0, query) : lower;
            if (path.endsWith(".zip") || path.endsWith(".rar") || path.endsWith(".7z") || path.endsWith(".iso")) return;
            runOnUiThread(() -> {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(trimmed)));
                } catch (Exception ignored) {
                }
            });
        }
    }
}
