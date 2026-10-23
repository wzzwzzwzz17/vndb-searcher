import os
import re
import shutil

src = r"C:\VNDB_ai"
dst = os.path.join(src, "vndb-searcher")
desk = os.path.join(dst, "desktop")
os.makedirs(desk, exist_ok=True)
for name in os.listdir(os.path.join(src, "vn_ai_skill")):
    if name in ("cache", "__pycache__"):
        continue
    path = os.path.join(src, "vn_ai_skill", name)
    if os.path.isfile(path) and not name.endswith(".pyc"):
        shutil.copy2(path, os.path.join(desk, name))
    elif os.path.isdir(path) and name == "data":
        target = os.path.join(desk, "data")
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.copytree(path, target)

android_src = os.path.join(src, "android_app")
android_dst = os.path.join(dst, "android")
os.makedirs(android_dst, exist_ok=True)
for name in ("build.ps1", "fix_zip.py", "copy_apk.py"):
    shutil.copy2(os.path.join(android_src, name), os.path.join(android_dst, name))
app_dst = os.path.join(android_dst, "app")
if os.path.exists(app_dst):
    shutil.rmtree(app_dst)
shutil.copytree(os.path.join(android_src, "app"), app_dst)

web_dst = os.path.join(dst, "web")
os.makedirs(web_dst, exist_ok=True)
for name in os.listdir(os.path.join(src, "web")):
    path = os.path.join(src, "web", name)
    if os.path.isfile(path) and name.endswith((".py", ".html")):
        shutil.copy2(path, os.path.join(web_dst, name))

config_path = os.path.join(desk, "config.py")
text = open(config_path, encoding="utf-8").read()
text = re.sub(
    r"DEEPSEEK_API_KEY = .*",
    'DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip() or "在此填入你的DeepSeek_API_Key"',
    text,
    count=1,
)
open(config_path, "w", encoding="utf-8").write(text)
print("key_leaked", "sk-" in open(config_path, encoding="utf-8").read())
