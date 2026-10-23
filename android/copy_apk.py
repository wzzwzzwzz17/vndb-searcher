import shutil

src = r"C:\VNDB_ai\android_app\out\VNDB-searcher.apk"
dst = r"C:\VNDB_ai\VNDB搜索器.apk"
shutil.copyfile(src, dst)
print("copied", dst)
