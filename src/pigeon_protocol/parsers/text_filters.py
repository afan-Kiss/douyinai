from __future__ import annotations

import re


METADATA_TEXT_RE = re.compile(
    r"^(CurrentServer|biz_sender_info|device|pb|false|true|default|web|pc|src|msg|type|text|"
    r"check_Send|flow_extra|displayType|remove_tips|attention|source|srcType|srcId|1|2)$",
    re.I,
)
PROTO_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
UI_NOISE_RE = re.compile(
    r"^\d+/\d+$|^发\s*送$|人工已回复|以上为历史|暂无咨询|已读$|未读$|从历史会话发起"
)
JSON_FRAGMENT_RE = re.compile(
    r"paas_|track_info|begin_create|message_logid|biz_aid|security_conversation|"
    r"c_foot|msg_foot|hierarchical_dimension|商家配置发送|"
    r'hierarchical_dimension|"[,}:]|\\",|"\s*,\s*"'
)
LONG_DIGITS_RE = re.compile(r"^\d{10,}")
TIME_ONLY_RE = re.compile(r"^(\d{1,2}:\d{2}(:\d{2})?|\d+秒)$")
UUID_ONLY_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
MEDIA_TAG_RE = re.compile(r"^\[(图片|表情|商品卡片)\]$")
SYSTEM_AUTO_RE = re.compile(
    r"客服\S*接入|超时未回复|系统关闭会话|欢迎光临|请问有什么可以帮助|"
    r"用户已等待|请尽快回复|以上是历史|暂无咨询|从历史会话发起|"
    r"人工客服为您服务|查阅.*对话记录"
)
SELLER_SCRIPT_RE = re.compile(
    r"商家配置发送|人工客服欢迎语|send_welcome|msg_foot|c_foot"
)
SELLER_WELCOME_RE = re.compile(
    r"^Hi[，,]?\s*欢迎光临|智能客服|正在为您处理|已收到.*消息"
)
LLM_LABEL_RE = re.compile(
    r"其他无意义|无意义内容|common_[A-Za-z0-9_]+|llm_intent|GM_QT|LT_QT"
)
EXACT_NOISE_PHRASES = frozenset(
    {
        "其他无意义内容",
        "客服饭饭接入",
        "未知买家",
    }
)
SINGLE_CHAR_OK_RE = re.compile(r"^[\u4e00-\u9fff]$")
TRIVIAL_ASCII_RE = re.compile(r"^(1|2|ok|OK|hi|HI)$")


def normalize_text(text: str = "") -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def is_noise_text(text: str = "") -> bool:
    value = normalize_text(text)
    if not value:
        return True
    if UUID_ONLY_RE.fullmatch(value):
        return True
    if TIME_ONLY_RE.fullmatch(value):
        return True
    if value.lower().startswith("[tea-sdk]"):
        return True
    if UI_NOISE_RE.search(value):
        return True
    if JSON_FRAGMENT_RE.search(value):
        return True
    if LONG_DIGITS_RE.fullmatch(value):
        return True
    if re.fullmatch(r"[\x20-\x7e]{1,200}", value) and not re.search(r"[\u4e00-\u9fff]", value):
        if not re.fullmatch(r"\[[^\]]+\]", value):
            return True
    if METADATA_TEXT_RE.fullmatch(value):
        return True
    if PROTO_FIELD_RE.fullmatch(value):
        return True
    if len(value) <= 3 and not re.search(r"[\u4e00-\u9fff]", value):
        return True
    return False


def is_meaningless_message(text: str = "", role: str = "", nickname: str = "") -> bool:
    """Drop protocol garbage, auto scripts, nickname-only, and seller/system boilerplate."""
    value = normalize_text(text)
    if is_noise_text(value):
        return True

    nick = normalize_text(nickname)
    if nick and value == nick:
        return True

    if SYSTEM_AUTO_RE.search(value):
        return True

    if value in EXACT_NOISE_PHRASES:
        return True

    if LLM_LABEL_RE.search(value):
        return True

    role = str(role or "").strip().lower()
    if role in {"seller", "system"} and MEDIA_TAG_RE.fullmatch(value):
        return True

    if role == "seller":
        if SELLER_SCRIPT_RE.search(value) or SELLER_WELCOME_RE.search(value):
            return True
        if SINGLE_CHAR_OK_RE.fullmatch(value) or TRIVIAL_ASCII_RE.fullmatch(value):
            return True

    if role == "buyer" and SELLER_WELCOME_RE.search(value):
        return True

    if role == "buyer" and SINGLE_CHAR_OK_RE.fullmatch(value):
        return True

    if role == "buyer" and TRIVIAL_ASCII_RE.fullmatch(value):
        return True

    return False
