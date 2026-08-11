# volc-tts · 火山引擎豆包语音 TTS 合成脚本

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个**零依赖**（纯 Python 标准库）的火山引擎豆包语音 TTS 合成脚本，带 **LLM 自动语气层**——合成前用 DeepSeek 分析文本情绪，自动生成火山 `context_texts` 语气指令，让朗读自然带情绪（生气/开心/平静…），无需手动标注。

设计目标：作为 [Hermes Agent](https://hermes-agent.nousresearch.com/) 的 TTS command provider 后端使用，也可独立作为命令行工具调用。

## 特性

- 🎙️ **火山豆包语音合成大模型 2.0**（`seed-tts-2.0`）与 **声音复刻大模型 2.0**（`seed-icl-2.0`，`S_` 开头音色自动切换）— 同一 API Key 直接调用
- 🧹 **Markdown 清洗**：合成前自动删除表格/代码块/行内代码/裸 URL，保留链接文字，实测省 60-70% 字符（省钱）
- 🎭 **LLM 语气层（可选）**：DeepSeek 分析文本情绪 → 生成 `context_texts` 语气指令（火山端**指令文本不参与计费**）
  - 情绪预检：只有感叹号/情绪词/emoji 命中才调 LLM，普通句子零延迟
  - 输入截断：只看前 200 字，预检与 LLM 输入一致
  - 降级安全：LLM 超时/失败/无 key → 静默跳过语气，正常合成，永不阻塞
- ⚡ 单次请求返回完整音频（HTTP 单向流式），NDJSON 流式响应自动拼接

## 用法

### 命令行

```bash
# 需要环境变量或 .env 文件: VOLC_TTS_API_KEY=<你的火山 API Key>
echo "你好，欢迎使用语音合成服务" > /tmp/input.txt
python3 volc_tts.py /tmp/input.txt /tmp/output.mp3 zh_female_vv_uranus_bigtts
```

参数：`volc_tts.py <input_path> <output_path> <speaker>`

### 作为 Hermes TTS provider

在 `~/.hermes/config.yaml` 配置（修改用 `hermes config set`）：

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

> ⚠️ Hermes command provider 子进程环境会被 scrub：key 要么列在 `env_passthrough`，要么放在 `.env` 文件里让脚本兜底读取（本脚本两者都支持）。

## 配置

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `VOLC_TTS_API_KEY` | 火山引擎 API Key（必填） | — |
| `VOLC_TTS_EMOTION_LLM` | 开启 LLM 语气层 | `0`（关） |
| `VOLC_TTS_EMOTION_LLM_MODEL` | 语气分析用的 LLM 模型 | `deepseek-v4-flash` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（开语气层时需要） | — |

Key 读取顺序：环境变量 → `~/.hermes/.env` 文件（兜底）。

## 计费

- 语音合成2.0 与 声音复刻2.0 **同价**：后付费 3 元/万字符；资源包 10 万字 28 元（2.8 元/万字符）起
- `context_texts` 语气指令文本**不参与计费**
- 免费额度：开通即 20,000 字
- Markdown 清洗 + 200 字截断可显著降低计费字符

## 延迟实测

| 场景 | 延迟 | LLM 调用 |
|---|---|---|
| 普通陈述句（预检跳过） | ~0ms | 无 |
| 情绪句首次（冷） | ~1.1s | 1 次 |
| 情绪句重复（缓存命中） | ~0ms | 无 |
| 火山 TTS 直连基线 | ~1.14s | — |

## 文档

- [`docs/volcengine-tts-api.md`](docs/volcengine-tts-api.md) — 火山 API 速查：鉴权、参数、语气指令 `context_texts` / `tone_fidelity` 实测、curl 示例、声音复刻流程
- [`docs/hermes-voice-config.md`](docs/hermes-voice-config.md) — Hermes 接入技能（公开版）：command provider 配置、LLM 语气层说明、Hermes 语气通道限制

## 安全说明

- 本仓库不含任何密钥/真实音色 ID；所有密钥通过环境变量或 `.env` 文件注入
- 请勿将 `.env`、`*.key` 等敏感文件提交到任何公开仓库

## 鸣谢

- 火山引擎豆包语音（[文档](https://docs.volcengine.com/docs/6561/2528925)）
- 灵感来源：Hermes Agent command provider 机制
