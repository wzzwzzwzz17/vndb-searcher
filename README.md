# VNDB搜索器

用自然语言描述你想找的 Galgame。程序把描述变成 VNDB 官方筛选条件，返回高分作品的名称、原名、评分、中文简介、标签和 VNDB 链接，并尽量补上鲲 Galgame、TouchGal 的评分。点开作品时，给出的是这几个站上的作品参考页入口，不是压缩包、直链或磁力链接。

本项目为 **雨里百跳** 原创。开发期间使用了 AI 辅助。任何人都可以参考或继续优化，详见 [LICENSE](LICENSE)。

## 功能

- 中文描述映射到 VNDB 官方标签（内置约 3000 个标签名）
- 三种搜索方式，条件在本地强制执行，不单靠模型自觉
- 只取官方原始语言为日语的作品（olang=ja），不搜英语原版游戏
- 只按一个核心标签搜索
- 发售年份下限
- 刷新时避开已经看过的作品
- 在上一轮标签上增删（优化上轮分析）
- 简介翻译成中文
- 每部作品附带权重最高的 4 个其它标签（不含本次用来搜索的标签）
- 同时显示 VNDB、鲲、TouchGal 评分；有多个分数时按综合分排序
- 点开作品可看鲲 / TouchGal 的标签、中文简介，以及参考页入口
- 收藏夹（桌面版）
- 三个使用方式：桌面版、本地网页、安卓版

网页和安卓把功能分成三页：

| 页面 | 做什么 |
| --- | --- |
| 自然语言推荐 | 按上面的三种模式推荐。勾选「必须搜中」时，真红小站会用更短的日文片段多搜几轮 |
| 名称精确搜索 | 先查必应，再整理中文、英文、日文正式名称，然后到 VNDB、鲲和真红小站查找。页面显示耗时 |
| 高精度标签搜索 | 标签目录来自 VNDB 官方标签库，按主父标签归类，其它父标签标明「也属于」。权重 0.6–2.8，按评分从高到低，每次最多 10 部，刷新避开已看过的作品 |

TouchGal 需要开发者 Token，可以先留空，留空时这一项不参与评分。鲲的公开作品搜索不需要 Token。

### 三种模式实际会卡住什么

VNDB 的评分是 10 到 100，对应网站上的 1.0 到 10.0。标签强度是 0 到 3。

| 模式 | 评分下限 | 标签强度 |
| --- | --- | --- |
| 总质量优先 | ≥ 75（网站约 7.5） | 压在 0.8–1.2，避免把高分作品滤掉 |
| 内容权重优先 | ≥ 55（约 5.5） | 核心 ≥ 2.4；次要若模型给到 2.0 以上则保留，否则落到 1.6 |
| 硬核内容要求 | ≥ 62（约 6.2） | 核心 ≥ 2.8，模型若给出 3.0 则保持 3.0；次要 ≥ 2.0。允许没有结果 |

内容模式和硬核模式如果没能确定任何一个标签，会直接提示，不会拿无关高分作品凑数。

## 仓库结构

```text
desktop/     Python 逻辑，以及 PyQt5 桌面版
web/         本地网页（server.py、三个 HTML 页面）
android/     安卓源码（界面在 assets/www，逻辑主要在 app.js）
LICENSE      作者许可与第三方边界
```

桌面窗口目前是自然语言推荐。名称精确搜索和高精度标签搜索在网页和安卓里。

## 用到的开源项目和外部资料

本仓库没有把别人的源码拷进来。运行时依赖和参考如下。

| 名称 | 用途 | 许可 / 条款 |
| --- | --- | --- |
| [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) | 桌面窗口 | GPL v3，或 Riverbank 商业许可 |
| [requests](https://github.com/psf/requests) | 桌面版和网页版 HTTP | Apache-2.0 |
| [openai](https://github.com/openai/openai-python) | 调用 DeepSeek 的兼容接口 | Apache-2.0 |
| [VNDB](https://vndb.org/) 与 [API v2 (Kana)](https://api.vndb.org/kana) | 作品、评分、标签、简介 | 遵守 VNDB 数据许可和 API 使用条款（非商业、有频率限制） |
| [鲲 Galgame](https://www.kungal.com/) | 公开作品搜索、评分、简介和作品页 | 只读公开接口，页面入口形如 `https://www.kungal.com/galgame/{id}` |
| [TouchGal](https://www.touchgal.ink/) | 有 Token 时读取评分和作品页 | Token 从 [TouchGal 开发者站](https://developer.touchgal.com/) 获取，格式为 `tgal_live_` 开头 |
| [真红小站](https://www.shinnku.com/) | 用日文名片段找目录或搜索结果页 | 只保留页面入口，不返回压缩包或磁力链接 |
| [VndbSharp](https://github.com/wzzwzzwzz17/VndbSharp) | 仅作参考，未纳入本仓库 | 以该仓库自身许可为准 |
| Android SDK 构建工具 | 打包 APK | 使用 Android SDK 许可，工具不随本仓库分发 |

标签名快照在 `android/app/src/main/assets/www/tags_min.json`，标签树在 `desktop/data/vndb_tags_tree.json` 和安卓资源里的 `tags_tree.json`，都来自 VNDB，不属于本项目原创数据。DeepSeek 只负责把自然语言编成筛选条件，并翻译简介和未收录的标签名。

桌面版因为使用了 PyQt5，整体分发时还要同时满足 GPL v3。网页版和安卓版没有链接 PyQt，界面是项目自己的 HTML/CSS。

## 怎么使用

国内网络访问 `api.vndb.org` 通常需要 VPN。DeepSeek（`api.deepseek.com`）一般可以直连。API Key 只保存在本机，请不要把它提交到 Git。

描述必须以这句话开头：

```text
这是我想要的galgame描述：校园、日常、高分
```

网页版会自动补上这句前缀。桌面版请自己写在输入框里。

### 网页版

需要 Python 3.10 或更高版本。网页和桌面共用 `desktop/` 里的逻辑。

```powershell
cd desktop
pip install -r requirements.txt
cd ..\web
python server.py
```

浏览器打开 `http://127.0.0.1:8787/`。顶部三个链接分别是 `/`、`/name`、`/tags`。点开作品详情时，网页会按真红小站的方式查 GGBase：先只用日文名（先全名，再连续一半，再随机四分之一）；日文全部落空时，改用英文名（罗马字/官方英文名）再搜一轮，命中规则是包含关系加 0.82 相似度。某一页的栏目少于 4 条时，这些栏目页和栏目上显示的名字都会列出，方便自行判断。结果更多时，只保留日文全名或一半能对上的，最多 3 个栏目页（`view.so?id=`）。这一步只在网页端，不进安卓包，也不返回磁力或压缩包地址。

DeepSeek Key 可以写在页面上，也可以事先写进 `desktop/config.py` 或环境变量 `DEEPSEEK_API_KEY`。TouchGal Token 留空即可。端口和监听地址可用环境变量 `VNDB_WEB_PORT`、`VNDB_WEB_HOST` 改。

### 桌面版

```powershell
cd desktop
pip install -r requirements.txt
python main_gui.py
```

编辑 `desktop/config.py`，把 `DEEPSEEK_API_KEY` 换成你的 Key。也可以不改文件，改为设置环境变量 `DEEPSEEK_API_KEY`。

收藏写在仓库根目录的 `favorites.json`（已被 git 忽略）。第一次搜索如果本地没有标签库，会向 VNDB 拉取并缓存在 `desktop/cache/`。

### 安卓版

1. 安装 JDK 17，并设置 `ANDROID_SDK_ROOT`（或 `ANDROID_HOME`），其中要有 build-tools 34 和 platforms android-34。
2. 在 `android` 目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

3. 安装生成的 `android/out/VNDB-searcher.apk`。脚本会再复制一份到 `android/VNDB-searcher.apk`。当前签名是本机自动生成的 debug 证书，密码是 `android`，不要拿它发正式版。
4. 打开应用后先填写 DeepSeek API Key。底部切换「推荐 / 寻名 / 标签」。应用图标是仓库里的 512×512 图片。

安卓版把网页三页收进同一个 WebView，请求直接从手机发出，不依赖本机的 `web/server.py`。点结果卡片会加载鲲、TouchGal 和真红小站的参考页，再用系统浏览器打开。鲲使用公开接口，不需要 Token。

## 百度网盘登录（baidu_cloud 包）

`baidu_cloud/` 是为「vndb-searcher + 百度网盘分级评分捞取方案」准备的第一块基础：登录。整个方案的用途只有个人收藏保存，不商用、不传播。

需要提供百度网盘登录信息时，程序会打开一个浏览器窗口让你本人登录：

```powershell
cd vndb-searcher
python -m baidu_cloud.auth login
```

- 本机装了 playwright（`pip install playwright && playwright install chromium`）时：弹出真实浏览器窗口打开 pan.baidu.com，你手动扫码或输密码登录；脚本等到 `STOKEN` 落地、并且调接口确认登录态有效之后才保存凭据并关窗，所以你慢慢来也不会存错。
- 没装 playwright 时：用系统默认浏览器打开登录页，你登录后按提示把 Cookie 行粘贴回终端，脚本解析出 BDUSS / STOKEN 保存。
- 凭据保存在 `baidu_cloud/credentials.json`（已加入 .gitignore，不会提交），`status` 查看状态，`clear` 删除。

后续的磁力离线、分享转存、容量查询模块都会从 `baidu_cloud.auth` 取 Cookie；这些模块尚未编写，属于方案里的阶段 2–5。

## 已知问题

这些是目前看得到、以后可能踩到的问题，不是完整测试报告。

- 中文到标签的对应靠一小份同义词表和英文名模糊匹配。冷门说法、日文玩法名、错别字，可能对到相近但不对的标签，或对不上。
- 模型只能从候选标签里挑 ID，但仍可能挑错「哪一个才是用户真正想要的」。本地只强制评分线和强度，不保证题材语义一定正确。
- VNDB 的 `tag` 过滤会算上父标签。结果里展示的是作品上直接标注的标签，所以「其它高权重标签」和搜索条件不是同一层，读起来可能对不上。
- 总质量模式会把标签强度压到 1.2 以下。想要「又高分又很贴标签」时，应改用内容模式或硬核模式。
- 硬核模式和很窄的内容模式经常没有结果。这是故意的，不会为了凑列表把强度放宽。
- 发售日期未知的条目（例如 TBA）目前会保留，不会当成老作品删掉。
- 刷新最多排除最近看过的大约 25 个 ID。看过很多部之后，可能再次出现早期结果。
- 桌面版单次查询的 VNDB 超时写得比较短（约 2.5 秒），网络不稳时容易失败；网页和安卓的外部站点超时更长。
- TouchGal 没有 Token 时评分为空。鲲或真红暂时连不上时，对应项留空，不拿下载链接顶上。
- 真红小站对很长的日文标题，常常只能对上压缩包文件名，这时给出的是站内搜索页，不是文件地址。
- 网页进程里填写的 Key 和 TouchGal Token 会写进当前进程的配置，同一台机器上后一次请求会盖住前一次。
- 安卓版的 API Key 放在应用私有存储里，是明文，没有额外加密。
- 简介和标签翻译依赖 DeepSeek，可能漏译、缩短或措辞不稳。
- 没有自动化测试覆盖三种模式的全部筛选组合。模式规则有过手工核对；`galsave`（收藏库流水线）那一套有 91 个单元测试。
- 安卓打包脚本只在 Windows 上试过，而且依赖本机已安装的 SDK，不是 Gradle 工程。

## 可以怎么优化

- 补一份更全的中文标签表，并定期刷新 `tags_min.json` 和标签树。
- 让用户在自然语言推荐前看到将要使用的标签和强度，能手动删掉或改强度。
- 结果为 0 时说明是评分不够、标签太严，还是网络失败，而不是只给一句空结果。
- 桌面版补上名称精确搜索和高精度标签搜索，并和网页、安卓共用同一套筛选校正。
- 查询结果做本地缓存，并遵守 VNDB 的频率限制，不要在刷新时连打。
- API Key 改为系统密钥库或用户自己选择的加密存储。网页不要把 Key 写进进程全局配置。
- 改成 Gradle / Android Studio 工程，方便别人编译和签名。
- 给三种模式补一组固定夹具测试：错误的 10 分制评分、缺少标签、单核心、硬核 3.0 都要被本地改写或拒绝。
- 作品详情可补上长度、开发状态等仍然属于公开元数据的字段。
- GGBase 在网页版里只做「名字 → 站内搜索页」入口；`galsave` 里才做磁力链接解析，而且只写进本地收藏库。

## 收藏库流水线（galsave）

`galsave` 是仓库里独立的一条命令行流水线，和网页版、桌面版、安卓版互不影响。它做的事只有一件：从 VNDB 按评分从高到低取**官方原始语言是日语**（`olang=ja`）的作品，去三个资源站找对应资源，然后按资源类型用不同方式存进自己的百度网盘，把结果记进本地 SQLite，收满 4 TB 就停。

前提说清楚：这是一个**个人收藏**工具，只保存、不商用、不传播、不上传任何公开链接。用到的都是各站公开页面，请自己遵守它们的许可和使用条款，也遵守你所在学校或运营商对校园网流量的规定。

### 只取日语原版

VNDB 的 `olang` 字段是「官方原始语言」。`galsave/vndb.py` 在 VNDB 查询里直接带 `["olang","=","ja"]`，拿回结果后再本地过一遍 `olang`，所以英语原版游戏（例如本来就用英语写的作品）不会被选进来。网页版、桌面版、安卓版也做了同样的过滤。

### 三种资源来源

| 来源 | 站点 | 怎么进网盘 | 占不占本地网络 |
| --- | --- | --- | --- |
| `magnet` | GGBase（镜像 `ggb.dlgal.com`） | 解析出磁力链接；百度网盘现已不接受磁力离线（见下），所以只登记到「待处理磁力」清单 | 不占（不下载） |
| `share` | 鲲 kungal | 抓分享链接，云端转存到自己网盘 | 不占（云端转存） |
| `direct` | 真红 shinnku | 本地下载 → AES-GCM 加密 → 改名成 `.png` → 分片上传 | 占，按批配额 500 GB |

`source_priority` 决定同一部作品优先试哪种来源。

**关于磁力离线（实测结论）**：`POST /rest/2.0/services/cloud_dl?method=add_task` 对磁力（`type=4`）一律返回 `{"error_code":36001,"error_msg":"param error"}`，换磁力、换 `type`、换 `app_id`/`clienttype`、加 `select`/`dn` 都无效；同样参数换成 http 直链（`type=1`）立刻成功，说明不是凭据或会员的问题，而是**百度网盘已不再为个人账号提供 BT/磁力离线**（离线只剩 http/ftp）。SVIP 账号的会员权益里仍然写着「离线下载套餐」，但磁力任务进不去。

所以 `galsave` 现在的做法是：GGBase 解析出来的磁力不再白调接口，而是写进数据库 `magnet_pending` 表（`vndb_id / title / magnet / size_gb / site`），一条作品成功从别的源入库时会自动清掉对应记录。用 `python -m galsave magnets` 导出这份清单，之后可以自己用本地 BT 客户端挂机下载，或者等哪天网盘又支持了再重试。

直链路线会占本地网络，所以按批计：`c_quota_per_batch_gb: 500`，用完就只用转存和磁力登记，直到你执行 `python -m galsave quota --reset` 开新的一批（清零的只是本批用量，已入库的账不变）。直链在下载前先发一次 `HEAD` 探体积，超出本批剩余额度就不下载——校园流量不该花在注定要删的文件上。

**关于转存（实测结论）**：现在的分享页是服务端渲染的一坨 JSON，`/share/list` 之类的老接口全部报错，能用的顺序是：`GET /s/<surl>` 拿到跳转后的权威 `surl`（注意原始链接带的那个前导 `1` 是分享 ID 的一部分，`/share/verify` 要用去掉 `1` 的那段）→ `POST /share/verify` 拿 `BDCLND` Cookie → 带 Cookie 再 `GET /share/init` 读 `file_list` → `POST /share/transfer`，其中 `sekey` 必须是 **URL 解码后**的 `BDCLND`，否则回 `errno 200025 提取码输入错误`。

鲲的资源条目还有个坑：条目标注的体积（比如 7.46 GB）和它给的百度链接**没关系**，很多条目给的其实是「模拟器 + 解压工具」那个公共分享（实测只有几个 APK）。真正的下载地址要单独调一次 `POST /api/v1/galgame-resources/{resource_id}/downloads`（不需要登录，返回 `download_urls` 和 `extraction_code`，GET 同一个路径回 405），页面上的「获取链接」按钮走的就是这个接口。所以现在搜索阶段只产出候选，等到真要转存时才 `resolve_share()` 去取真链接。

`save_share` 在转存前还会先解析 `file_list` 累加真实体积，小于 `min_resource_gb`（0.3 GB）就判定「不是游戏本体」直接跳过，不浪费转存。条目自带 `state` 字段，`expired` 的分享也会提前跳过。

**关于上传（实测结论）**：`/api/precreate` 必须带 `autoinit=1` 才会返回 `uploadid`（不带只有 `return_type=1` 的秒传标记）；`/api/create?a=commit` 的 `block_list` 要用真实的分片 MD5，填空串会回一个极误导的 `errno -7「该文件所在的文件夹命名不符合要求」`。另外本地文件名要先统一成正斜杠再取 basename，否则 Windows 本机路径整串会被当成文件名传上去，也是 `-7`。

### 分级队列与停机

`rating_buckets: [8.0, 7.5, 7.0, 6.5, 6.0]`：先从 8.0 分以上捞，捞完这一档再往下走一档，不会一上来就把 6 分档掺进来。累计入库达到 `max_total_gb: 4000` 就软停机：不再接新作品，把手上这一条做完。

每次成功入库后都会重新读网盘里该目录的实际大小累加进 `done.size_gb`，同时写进 `runtime_meta`。启动时会用 `SELECT SUM(size_gb)` 兜底校准一次，所以手工删过网盘文件也不会算错。

### 百度网盘登录

需要登录信息的时候，程序会自己打开一个**真实浏览器窗口**让你本人登录（playwright，有头模式），确认登录态真的可用之后才保存到 `baidu_cloud/credentials.json`（已在 `.gitignore` 里，只存本机）。没装 playwright 时退化成「系统浏览器 + 粘贴 Cookie」。

判据不是「看到 BDUSS 就关窗」：新设备登录时 BDUSS 会先落地，`STOKEN` 等 Cookie 要等跳进网盘主界面才下发，早关窗会存下半截凭据（接口仍回「用户未登录」）。所以要求 BDUSS 之外还有 `STOKEN` 或页面已在 `/disk/home`，保存后立刻调一次 `/api/quota` 验证，不通过就删掉刚存的凭据、窗口继续开着等你完成验证。

```bash
python -m baidu_cloud.auth login              # 打开浏览器窗口登录
python -m baidu_cloud.auth login --force      # 凭据过期时强制重登
python -m baidu_cloud.auth status             # 看当前是否已登录（会真调一次接口）
python -m baidu_cloud.auth clear              # 清掉本机凭据
```

### 命令

```bash
pip install -r requirements.txt

python -m galsave selftest           # 本地自检，不联网
python -m galsave status --quota     # 数据库统计 + 网盘容量
python -m galsave plan --pages 3     # 各评分档还有多少部没捞
python -m galsave dryrun "ランス10"  # 只搜不下载，看三站各命中什么
python -m galsave run                # 正式跑
python -m galsave run --buckets 8.0 7.5 --max-vns 20
python -m galsave quota              # 只看 4TB / 500GB 配额
python -m galsave quota --reset      # C 源 500GB 用完后换新的一批（配额清零，账不会丢）
python -m galsave magnets --out magnets.txt   # 导出网盘收不了的磁力清单（自己用 BT 客户端处理）
python -m galsave decrypt 某个文件.png --out real.rar   # 解回本地下载的加密文件
```

配置在仓库根的 `config.yaml`，本地覆盖可以建 `config.local.yaml`（已忽略），或者用 `GALSAVE_CONFIG` 指定别的文件、`GALSAVE_MAX_GB` / `GALSAVE_C_QUOTA_GB` / `GALSAVE_FUZZY` / `GALSAVE_PROXY` 临时改值。

### 数据与测试

- 数据库：`galsave/data/task.db`（`done` / `retry` / `runtime_meta` / `vn_seen` / `magnet_pending` 五张表），日志：`galsave/logs/galsave.log`（4 MB 轮转 5 份）。
- 测试：`python -m unittest discover -s tests -t .`（91 项，全部离线夹具，不联网）。

### 已知局限

- 百度网盘已经不支持磁力离线（`add_task` 对 `type=4` 恒回 `36001 param error`），所以 GGBase 这条线目前只能产出「待处理磁力清单」，真正进网盘只能靠鲲转存或真红直链。
- 鲲的资源多为「合集」类条目，默认 `allow_collection: false` 会跳过，所以有些作品在鲲上捞不到本体。
- 鲲有些作品的候选条目里，真实分享已经被替换成「模拟器 + 解压工具」打包（转存前的体积筛选会挡掉），这类作品得等别家站点。
- 真红只有直链，走本地下载，受 500 GB/批 配额限制；合集区的直链主机和 ZD CDN 不一样，两种都支持。
- GGBase 镜像的翻页参数无效（`p=0/1/2` 返回同一批），已按条目 ID 去重，但深层的早期资源搜不到。
- 标题匹配用相似度（阈值 85），遇到「大戦国ランス / 戦国ランス」这种前缀只差一两个字的不同作品，靠规则压到阈值以下；极短的别名仍可能误命中，最终以 `dryrun` 的人工核对为准。

## 声明

- 作者：雨里百跳
- 本项目原创。编写和修改过程中使用了 AI 辅助。
- 允许任何人参考或优化作者编写的这部分代码，请保留署名。
- 请同时遵守 PyQt5、VNDB 以及其他第三方组件自己的许可和使用条款。
