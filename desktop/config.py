# -*- coding: utf-8 -*-
"""全局配置：请在下方填入你的 DeepSeek API Key。"""

import os

# 项目根目录（收藏夹存档写在这里）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FAVORITES_FILE = os.path.join(PROJECT_ROOT, "favorites.json")

# ========== 必填：DeepSeek API Key ==========
# 也可通过环境变量 DEEPSEEK_API_KEY 覆盖
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip() or "在此填入你的DeepSeek_API_Key"

# DeepSeek OpenAI 兼容接口
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

# VNDB Kana API
VNDB_API_BASE = "https://api.vndb.org/kana"
VNDB_TIMEOUT = 2.5  # 秒（文档要求约 2500ms）
VNDB_USER_AGENT = "VNDB-AI-Recommender/1.0 (local; contact: local-user)"

# 会话与限流
MAX_SESSION_CALL = 2  # 单次用户查询最多 2 轮 VNDB 调用（含宽松重试）
CACHE_TTL = 3600  # 缓存 1 小时
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

# 推荐默认参数
DEFAULT_MIN_RATING = 60  # VNDB Bayesian 评分 10–100，对应站点约 6.0
DEFAULT_RESULT_LIMIT = 10
INPUT_PREFIX = "这是我想要的galgame描述："
DEFAULT_MIN_YEAR = 2005  # 发售年份下限（可在 UI 修改）

# 搜索模式
SEARCH_MODE_QUALITY = "quality"  # 总质量优先：高分，标签强度可低
SEARCH_MODE_CONTENT = "content"  # 内容权重优先：核心标签高强度，评分次之
SEARCH_MODE_HARDCORE = "hardcore"  # 硬核内容：核心近满分强度 + 评分底线，允许 0 结果
SEARCH_MODE_LABELS = {
    SEARCH_MODE_QUALITY: "总质量优先",
    SEARCH_MODE_CONTENT: "内容权重优先",
    SEARCH_MODE_HARDCORE: "硬核内容要求",
}

# 总质量优先
QUALITY_MODE_MIN_RATING = 75

# 内容权重优先
CONTENT_MODE_MIN_RATING = 55
CONTENT_CORE_TAG_LEVEL = 2.4
CONTENT_SECONDARY_TAG_LEVELS = (2.0, 1.6)

# 硬核内容要求
HARDCORE_CORE_TAG_LEVEL = 2.8  # 能 3.0 就 3.0
HARDCORE_CORE_TAG_LEVEL_PREF = 3.0
HARDCORE_MIN_RATING = 62  # 站点约 6.2
HARDCORE_ALLOW_EMPTY = True  # 允许极端情况找不到，不做过度宽松

# 结果 enrichment
RESULT_TOP_TAG_COUNT = 4  # 每款游戏展示的权重前 N 标签（不含搜索标签）

# 社区评分（只读评分，不取下载链接）。Token 可留空；站点要求登录时会跳过。
KUNGAL_API_BASE = os.environ.get("KUNGAL_API_BASE", "https://www.kungal.com").strip()
KUNGAL_API_TOKEN = os.environ.get("KUNGAL_API_TOKEN", "").strip()
TOUCHGAL_API_BASE = os.environ.get("TOUCHGAL_API_BASE", "https://www.touchgal.io").strip()
TOUCHGAL_API_TOKEN = os.environ.get("TOUCHGAL_API_TOKEN", "").strip()
