import os
import shutil

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, "out", "VNDB-searcher.apk")
dst = os.path.join(here, "VNDB-searcher.apk")
shutil.copyfile(src, dst)
print("copied", dst)
