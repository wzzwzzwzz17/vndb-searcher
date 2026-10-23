import sys
import zipfile

src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(dst, "w") as zout:
    for info in zin.infolist():
        data = zin.read(info.filename)
        name = info.filename.replace("\\", "/")
        ni = zipfile.ZipInfo(filename=name, date_time=info.date_time)
        ni.compress_type = info.compress_type
        ni.external_attr = info.external_attr
        zout.writestr(ni, data)
print("fixed", dst)
