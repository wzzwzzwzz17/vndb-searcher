$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$sdk = $env:ANDROID_SDK_ROOT
if (-not $sdk) { $sdk = $env:ANDROID_HOME }
if (-not $sdk) { throw "请先设置 ANDROID_SDK_ROOT 或 ANDROID_HOME" }
$bt = Join-Path $sdk "build-tools\34.0.0"
$jar = Join-Path $sdk "platforms\android-34\android.jar"
$aapt2 = Join-Path $bt "aapt2.exe"
$d8 = Join-Path $bt "d8.bat"
$zipalign = Join-Path $bt "zipalign.exe"
$apksigner = Join-Path $bt "apksigner.bat"
$src = Join-Path $root "app\src\main"
$out = Join-Path $root "out"
$ks = Join-Path $root "vndb-debug.keystore"

if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out | Out-Null
New-Item -ItemType Directory -Path (Join-Path $out "classes") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $out "dex") | Out-Null

if (-not (Test-Path $ks)) {
  keytool -genkeypair -keystore $ks -alias vndb -keyalg RSA -keysize 2048 -validity 10000 `
    -storepass android -keypass android -dname "CN=Yulibaitiao, OU=VNDB, O=Local, C=CN"
}

Write-Host "compile resources"
& $aapt2 compile --dir (Join-Path $src "res") -o (Join-Path $out "compiled.zip")

Write-Host "link"
& $aapt2 link `
  -o (Join-Path $out "base.apk") `
  -I $jar `
  --manifest (Join-Path $src "AndroidManifest.xml") `
  --min-sdk-version 24 `
  --target-sdk-version 34 `
  --version-code 1 `
  --version-name 1.0 `
  -A (Join-Path $src "assets") `
  (Join-Path $out "compiled.zip")

Write-Host "javac"
$javaFiles = Get-ChildItem (Join-Path $src "java") -Recurse -Filter *.java | ForEach-Object { $_.FullName }
javac --release 8 -encoding UTF-8 -cp $jar -d (Join-Path $out "classes") $javaFiles
if ($LASTEXITCODE -ne 0) { throw "javac failed" }

Write-Host "d8"
$classFiles = Get-ChildItem (Join-Path $out "classes") -Recurse -Filter *.class | ForEach-Object { $_.FullName }
& $d8 --min-api 24 --lib $jar --output (Join-Path $out "dex") $classFiles
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }

Write-Host "add dex"
Push-Location (Join-Path $out "dex")
jar uf (Join-Path $out "base.apk") classes.dex
Pop-Location

Write-Host "fix asset separators"
python (Join-Path $root "fix_zip.py") (Join-Path $out "base.apk") (Join-Path $out "repack.apk")

Write-Host "zipalign"
& $zipalign -f -p 4 (Join-Path $out "repack.apk") (Join-Path $out "aligned.apk")

Write-Host "sign"
& $apksigner sign --ks $ks --ks-pass pass:android --key-pass pass:android --out (Join-Path $out "VNDB-searcher.apk") (Join-Path $out "aligned.apk")
python (Join-Path $root "copy_apk.py")
Write-Host "APK ready"
