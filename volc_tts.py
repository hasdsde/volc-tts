#!/usr/bin/env python3
"""火山引擎豆包语音 TTS — Hermes command provider 后端
用法: volc_tts.py <input_path> <output_path> <speaker>
读 input_path 的文本, 调火山单向流式合成 API, 写 mp3 到 output_path。
API Key 从环境变量 VOLC_TTS_API_KEY 读取, 兜底解析 ~/.hermes/.env
(Hermes desktop 进程可能早于 .env 修改启动, 环境变量没加载)。
"""
import base64
import json
import os
import sys
import urllib.request
import uuid

API_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_SPEAKER = "zh_female_vv_uranus_bigtts"  # Vivi 2.0

# 语气 LLM 层: 合成前用 LLM 分析文本情绪, 生成 context_texts 语气指令。
# 开关: VOLC_TTS_EMOTION_LLM=1 开启(默认关, 避免每次合成多一次 LLM 往返)。
# key 复用 DEEPSEEK_API_KEY(~/.hermes/.env), model 默认 deepseek-v4-flash。
# LLM 失败/超时 → 静默跳过语气, 正常合成, 绝不阻塞 TTS 主链路。
# 注意: _EMOTION_LLM_ENABLED 在 _env_value() 定义后求值(见下方),
# 因为 Hermes command provider 子进程环境被 scrub, 开关必须像 key 一样
# 从 ~/.hermes/.env 文件兜底读取。
_EMOTION_LLM_MODEL = os.environ.get("VOLC_TTS_EMOTION_LLM_MODEL", "deepseek-v4-flash")
_EMOTION_LLM_URL = "https://api.deepseek.com/v1/chat/completions"
_EMOTION_CACHE = {}  # text -> context_texts 指令(缓存相同文本, 避免重复分析)

# 情绪预检: 明显有情绪的文本才走 LLM, 普通句子零 LLM 延迟。
# 命中条件: 感叹号/问号(或连用) / 情绪词 / 情绪 emoji, 任一命中即分析。
_EMOTION_PUNCT = ("！", "!", "？", "?")
_EMOTION_WORDS = (
    "生气", "愤怒", "火大", "气死", "气人", "气炸", "发火", "恼火", "怒",
    "开心", "高兴", "太棒", "太好了", "惊喜", "兴奋", "激动", "快乐", "耶",
    "难过", "伤心", "悲痛", "哭泣", "哭", "崩溃", "绝望", "痛苦", "委屈",
    "烦", "讨厌", "厌恶", "恶心", "嫌弃", "无语", "无奈", "头疼", "抓狂",
    "紧张", "害怕", "恐惧", "担心", "焦虑", "慌", "吓",
    "爱", "喜欢", "想念", "心疼", "温暖", "感动", "幸福", "美好",
    "佩服", "厉害", "牛", "绝了", "救命", "天哪", "我的天", "天呐", "哇",
    "震惊", "惊讶", "惊呆", "离谱", "过分", "岂有此理",
    "求求", "拜托", "谢天谢地", "终于", "总算",
)
_EMOTION_EMOJI = ("😡", "😠", "🤬", "😢", "😭", "😄", "😂", "🤣", "🥰", "😍",
                  "😱", "😨", "🤯", "😤", "💢", "🔥", "❤️", "💔", "🎉", "🙏")


def has_strong_emotion(text: str) -> bool:
    """快速预检: 文本是否明显带情绪。命中才值得花一次 LLM 调用。"""
    # 1) 感叹号/问号(含连用): "真的假的?!" "你太过分了!" 等
    if any(c in text for c in _EMOTION_PUNCT):
        return True
    # 2) 情绪词
    for w in _EMOTION_WORDS:
        if w in text:
            return True
    # 3) 情绪 emoji
    for e in _EMOTION_EMOJI:
        if e in text:
            return True
    return False


def _env_value(key: str) -> str:
    """读环境变量, 兜底解析 ~/.hermes/.env(Hermes 进程可能早于 .env 修改启动)。"""
    val = os.environ.get(key, "")
    if val:
        return val
    env_file = os.path.expanduser("~/.hermes/.env")
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# 开关在 _env_value 定义后求值: 环境变量优先, 兜底 .env 文件
# (Hermes command provider 子进程 env 被 scrub, 只能靠文件兜底)
_EMOTION_LLM_ENABLED = _env_value("VOLC_TTS_EMOTION_LLM") == "1"


def emotion_context_texts(text: str) -> list:
    """用 LLM 分析文本情绪, 返回 context_texts 语气指令列表; 失败返回 []。

    prompt 要求 LLM 只输出一条语气指令原文, 不解释不加引号,
    直接作为 context_texts 数组元素传给火山 TTS。
    """
    if not _EMOTION_LLM_ENABLED:
        return []
    cached = _EMOTION_CACHE.get(text)
    if cached is not None:
        return cached
    # 与 LLM 输入一致: 预检只看前 200 字(长文本尾部情绪 LLM 也看不到)
    head = text[:200]
    # 情绪预检: 普通句子(无感叹号/情绪词/emoji)直接跳过, 零 LLM 延迟
    if not has_strong_emotion(head):
        _EMOTION_CACHE[text] = []
        return []
    api_key = _env_value("DEEPSEEK_API_KEY")
    if not api_key:
        return []
    prompt = (
        "你是语音合成情绪分析器。分析下面文本的情绪, 输出一条中文语气指令, "
        "用于 TTS 合成时控制朗读语气。只输出指令本身, 不要解释、不要引号、不要标点后缀。\n"
        "格式: 你可以用<情绪描述>的语气说话吗?\n"
        "示例: 你可以用特别特别生气的语气说话吗?\n"
        "文本: " + text[:200]  # 情绪前 200 字足够判断, 截断省 token
    )
    payload = json.dumps({
        "model": _EMOTION_LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 64,
    }).encode("utf-8")
    req = urllib.request.Request(
        _EMOTION_LLM_URL,
        data=payload,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        instruction = data["choices"][0]["message"]["content"].strip()
        # 清理 LLM 可能带的多余引号/换行
        instruction = instruction.strip('"\'“”‘’。\n ')
        if not instruction:
            return []
        result = [instruction]
        _EMOTION_CACHE[text] = result
        print(f"[volc_tts] emotion LLM -> {instruction}", file=sys.stderr)
        return result
    except Exception as exc:
        print(f"[volc_tts] emotion LLM failed, skip: {exc}", file=sys.stderr)
        return []

def resource_id_for(speaker: str) -> str:
    """复刻/自定义音色(S_ 开头)走 seed-icl-2.0, 标准音色走 seed-tts-2.0"""
    return "seed-icl-2.0" if speaker.startswith("S_") else "seed-tts-2.0"


def clean_text(text: str) -> str:
    """合成前清洗: 整块删除表格/代码块, 去掉 markdown 噪音(省~20-50%字符)。
    表格和代码本身不适合朗读, 整块删掉; 只保留正文。"""
    import re
    # 1) 整块删除表格: 连续的 | 开头行(含表头/分隔线/数据行)
    text = re.sub(r'(?:^[ \t]*\|[^\n]*\n?)+', '\n', text, flags=re.M)
    # 2) 整块删除代码块: ``` 围栏
    text = re.sub(r'```[\s\S]*?```', '', text)
    # 3) 删除行内代码内容
    text = re.sub(r'`[^`]*`', '', text)
    # 4) 加粗/斜体标记(保留文字)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    # 5) 标题标记
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.M)
    # 6) 引用标记
    text = re.sub(r'^>\s?', '', text, flags=re.M)
    # 7) 链接 [文字](url) 保留文字; 裸 URL 删除(念出来是乱码)
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    # 7b) 删除 MEDIA: 附件标签(Hermes 桌面端混入的本地路径, 念出来是乱码)
    # 兼容 MEDIA:/path 和 MEDIA: /path(冒号后带空格, Hermes 官方正则允许 \s*)
    text = re.sub(r'MEDIA:\s*\S+', '', text)
    # 7c) 兜底: 7b 只删带前缀的, 桌面端语音模式可能只传残留的绝对路径或
    # 裸文件名(如 tts_20260811_233613_376221.mp3), 同样念出来是乱码。
    # 绝对路径(至少一段含字母, 避免误删 2026/08/11、3/4 这类正文):
    text = re.sub(r'/(?:[\w.-]*[A-Za-z][\w.-]*/)+[\w.-]*', '', text)
    # 裸附件文件名(带扩展名, 词中须含 . - _ 之一, 避免误删 "mp3" 等单词):
    text = re.sub(
        r'(?<![\w.])[\w][\w.-]*[._-][\w.-]*\.'
        r'(?:mp3|wav|ogg|opus|m4a|flac|png|jpe?g|gif|webp|pdf|docx?|xlsx?|pptx?|zip|7z|tar(?:\.gz)?)\b',
        '', text)
    # 8) 行首列表符号
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.M)
    # 9) 折叠空白, 去掉标点前的空格
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\s+([，。、！？；：,.!?;:])', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: volc_tts.py <input_path> <output_path> <speaker>", file=sys.stderr)
        return 2
    input_path, output_path, speaker = sys.argv[1], sys.argv[2], sys.argv[3]
    speaker = speaker or DEFAULT_SPEAKER

    with open(input_path, encoding="utf-8") as f:
        text = f.read().strip()
    text = clean_text(text)
    if not text:
        print("empty input text", file=sys.stderr)
        return 1

    api_key = os.environ.get("VOLC_TTS_API_KEY", "")
    if not api_key:
        # Hermes 进程可能早于 .env 修改启动, 直接从 .env 文件读
        env_file = os.path.expanduser("~/.hermes/.env")
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("VOLC_TTS_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except OSError:
            pass
    if not api_key:
        print("VOLC_TTS_API_KEY not set", file=sys.stderr)
        return 1

    req_params = {
        "text": text,
        "speaker": speaker,
        "audio_params": {"format": "mp3"},
    }
    # 语气 LLM 层: 分析情绪生成 context_texts(文本不参与计费, 失败自动跳过)
    ctx = emotion_context_texts(text)
    if ctx:
        req_params["context_texts"] = ctx

    payload = json.dumps({
        "req_params": req_params,
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": resource_id_for(speaker),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except Exception as exc:
        print(f"volc tts request failed: {exc}", file=sys.stderr)
        return 1

    buf = bytearray()
    for line in raw.splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("data"):
            buf += base64.b64decode(d["data"])

    if not buf:
        print(f"no audio in response: {raw[:300]!r}", file=sys.stderr)
        return 1

    with open(output_path, "wb") as f:
        f.write(buf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
