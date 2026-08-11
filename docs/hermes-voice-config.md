# Hermes 语音配置（TTS/STT）技能 — 公开版

> `hermes-voice-config` 技能的核心内容（脱敏公开版，已清理本地路径/主机名/真实音色 ID）。
> 完整技能还包括 STT（Qwen3-ASR 本地部署）与唤醒词（openwakeword）部分，见原技能。

## 架构速览（Hermes command provider）
- `tts.provider` 选内置 provider（edge/openai/elevenlabs/minimax/...）或 `tts.providers.<name>` 自定义 command provider
- command provider: `type: command` + `command` 模板，占位符 `{input_path} {output_path} {format} {voice} {model} {speed}`（字面量花括号用 `{{` `}}` 转义）
- 查看/修改: `hermes config get tts` / `hermes config set tts.provider <name>`

## 接入第三方 TTS: command provider
配置形状:
```yaml
tts:
  provider: volc-tts
  providers:
    volc-tts:
      type: command
      command: "python3 /path/to/volc_tts.py {input_path} {output_path} {voice}"
      output_format: mp3
      voice: zh_female_vv_uranus_bigtts
      max_text_length: 5000
      env_passthrough: [VOLC_TTS_API_KEY]
```
关键坑:
- **command 子进程环境默认被 scrub**（Hermes secrets 被清掉）→ key 必须列在 `env_passthrough` 白名单，或脚本自己读文件
- **`.env` 只在 Hermes 进程启动时加载**: 运行中往 .env 加 key，desktop/gateway 进程环境里没有 → provider 报 "XXX not set"。最稳: 脚本直接读 .env 文件兜底（见 volc_tts.py 的 `_env_value()` 模式）
- **config.yaml 对 agent 写保护**: patch/write_file 会被拒 → 必须用 `hermes config set tts.providers.<name>.<key> <value>`
- 改配置前先备份 config.yaml

## 火山引擎豆包 TTS（volc-tts）
- 脚本: `volc_tts.py`（本仓库根目录，纯标准库零依赖），key: `VOLC_TTS_API_KEY` 环境变量或 .env 文件
- 脚本内置 `clean_text()`：合成前清洗 markdown 噪音 — 整块删表格/代码块/行内代码/裸 URL，保留链接文字、去标题/列表/引用标记，实测省 60-70% 字符
- 默认音色 Vivi 2.0（青年女声）；99 个标准音色，换音色改 `tts.providers.volc-tts.voice`
- 免费额度: 语音合成2.0 开通即 20,000 字
- 完整 API 规范/文档链接/音色 ID/curl 示例: 见 `docs/volcengine-tts-api.md`

## 语气 LLM 层（volc_tts.py 内置，2026-08-11）
- 功能: 合成前用 DeepSeek 分析文本情绪 → 生成 `context_texts` 语气指令（如"你可以用特别特别生气的语气说话吗?"）塞进火山请求。**语气指令文本不参与计费**（火山文档明确）
- 开关: `VOLC_TTS_EMOTION_LLM=1`（**默认关**，避免每次合成多一次 LLM 往返 ~1-2s）
- key 复用 `DEEPSEEK_API_KEY`，model 默认 `deepseek-v4-flash`，可改 `VOLC_TTS_EMOTION_LLM_MODEL`
- **情绪预检** `has_strong_emotion()`: 感叹号/问号、情绪词表、情绪 emoji 命中才调 LLM；普通陈述句直接跳过（0ms）。**预检与 LLM 输入统一截断前 200 字**（长文本尾部情绪看不到，两边一致）
- 缓存: dict 缓存相同文本，避免重复分析
- 降级: LLM 超时(10s)/失败/key 缺失 → 静默返回 [] 正常合成，TTS 永不阻塞
- 延迟实测: 火山直连 ~1.1s；情绪句首次 +1.1s（LLM），重复文本 0ms；普通句 0ms
- **复刻音色 + context_texts 实测可用**: 文档标"复刻音色暂不支持语音指令"，但 seed-icl-2.0 + 复刻音色实测 code=0 正常出音频且听感有区别（2026-08-11 实测）

## Hermes 侧限制（为什么语气要在脚本内做）
- `text_to_speech` 工具的 `instructions` 参数只转发给 OpenAI provider（gpt-4o-mini-tts 系）；**command provider 完全不接收 instructions**，占位符仅 `{input_path} {output_path} {format} {voice} {model} {speed}`，无语气位
- → command provider 要语气只能靠脚本内 LLM 层（本仓库方案）或文本标记语法

## 边缘 provider 的坑（备选）
- **en-US 音色说不了纯中文**: `en-US-AriaNeural` + 纯中文文本 → `NoAudioReceived`（微软服务端拒绝）。含数字或拉丁字符的文本能过
- 修复: 用 `zh-CN-XiaoxiaoNeural`（晓晓，实测 OK）
- edge 免费、直连可用，适合做备用 provider
