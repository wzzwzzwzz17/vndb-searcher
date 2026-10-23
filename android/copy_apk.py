import os
import shutil

root = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(root, "out", "VNDB-searcher.apk")
dst = os.path.join(root, "VNDB-searcher.apk")
shutil.copyfile(src, dst)
print("copied", dst)
