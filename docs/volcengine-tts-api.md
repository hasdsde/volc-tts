# 火山引擎豆包语音 TTS API 速查（脱敏公开版）

> 本文件是 `hermes-voice-config` 技能中火山 TTS 部分的公开版本，已清理本地路径与真实音色 ID。

## 关键事实（踩过的坑）
- **域名是 `openspeech.bytedance.com`**。`openspeech.volcengine.com` 在所有 DNS 上都是 NXDOMAIN（不存在）！官方文档里也是 bytedance.com
- 鉴权头是 **`X-Api-Key: <API_KEY>`**，不是 `Authorization: Bearer`（用 Bearer 会失败）
- 旧接口 `POST /api/v1/tts` 需要 AppID+Token 签名（报 `Missing required: app.appid`），不是新版 API Key 体系
- 不存在的路径会返回 404 + `Endpoint "xxx" does not exist`（如 /api/v3/tts/unified）

## 单向流式合成 HTTP（推荐，一次输入返回完整音频）
```
POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
Headers:
  X-Api-Key: <API_KEY>            # 控制台>API Key管理
  X-Api-Resource-Id: seed-tts-2.0 # 豆包语音合成大模型2.0
  X-Api-Request-Id: <uuid>
  Content-Type: application/json
Body:
  {"req_params":{"text":"...","speaker":"zh_female_vv_uranus_bigtts","audio_params":{"format":"mp3"}}}
```
- **响应是 NDJSON 流**: 每行 `{"code":0,"message":"","data":"<base64音频块>"}`，最后一行 `{"code":20000000,"message":"OK"}`
- 拼接: 逐行 JSON 解析，data 字段 base64 解码后 concat → mp3（64kbps 24kHz 单声道）
- 其他 resource: `seed-icl-2.0` = 豆包声音复刻大模型2.0（复刻音色）
- 相关参数: req_params.audio_params.format(mp3/wav/ogg_opus)、silence_duration(ms)；方言走 explicit_dialect（dongbei/shaanxi/sichuan，需配支持方言的音色）；语音指令 context_texts（2.0 音色可用，不计费）

## 语气指令 context_texts 与 tone_fidelity（2026-08-11 实测）
- **价格: 语音合成2.0(seed-tts-2.0) 与 声音复刻2.0(seed-icl-2.0) 完全同价** — 后付费均 3元/万字符；资源包 10万字28元(2.8元/万字符)起。想加语气不用换计费档
- **context_texts（语音指令，加语气）**:
  - 请求体字段: `"context_texts":["你可以用特别特别痛心的语气说话吗?"]`（自然语言描述情绪/语气）
  - **指令文本不参与计费** — 加语气免费
  - 标准音色(seed-tts-2.0)文档明确支持；**文档说复刻音色(seed-icl-2.0)"暂不支持"，但实测复刻音色 + context_texts 返回 code=0 且正常出音频**（不要盲信文档，真机测过才算数；听感是否真的生效需人工对比）
  - 复刻音色还带 model 参数默认 `seed-tts-2.0-standard`，文档标注该 model 不支持 context_texts
- **tone_fidelity（复刻音色专属的"还原模式"）**: 默认 false；true 时尽可能还原训练 prompt 音频的音色和说话风格（情感、韵律、口音）。仅 seed-icl-2.0 复刻音色可用；仅支持合成与训练音频同语种；不支持双向流合成接口
- section_id: 跨包语义保持（多轮上下文），合成2.0 与 复刻2.0 音色均支持；双向流接口另有 session_id

## 音色 ID
- `zh_female_vv_uranus_bigtts` = Vivi 2.0（青年女声，默认）
- 99 个标准音色，完整列表: 控制台>音色库 或 https://www.volcengine.com/docs/6561/1257544
- 大模型音色命名规律: `zh_female_<name>_uranus_bigtts` / `zh_male_<name>_uranus_bigtts`

## 文档
- 单向流式 HTTP: https://docs.volcengine.com/docs/6561/2528925
- 单向流式 WebSocket: https://docs.volcengine.com/docs/6561/2534913
- 双向流式 WebSocket: https://docs.volcengine.com/docs/6561/2532486（端点 /api/v3/tts/bidirection）
- 错误码: https://docs.volcengine.com/docs/6561/2534853
- 音色列表: https://www.volcengine.com/docs/6561/1257544

## 已验证 curl 示例
```bash
curl -sN -X POST "https://openspeech.bytedance.com/api/v3/tts/unidirectional" \
  -H "X-Api-Key: $VOLC_TTS_API_KEY" \
  -H "X-Api-Resource-Id: seed-tts-2.0" \
  -H "X-Api-Request-Id: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"req_params":{"text":"你好","speaker":"zh_female_vv_uranus_bigtts","audio_params":{"format":"mp3"}}}' \
  -o /tmp/out.ndjson
```
解析 NDJSON 为 mp3:
```python
import base64, json
b = b''
for line in open('/tmp/out.ndjson', 'rb'):
    if line.strip():
        d = json.loads(line)
        if d.get('data'):
            b += base64.b64decode(d['data'])
open('/tmp/o.mp3', 'wb').write(b)
```

## 自定义音色（声音复刻 / 音色设计）
两条路（都走免费额度，声音复刻2.0 开通即有 20,000 字 + 11 个复刻槽位）：
1. **声音复刻**（克隆声音）: 控制台侧边栏"声音复刻" → 上传 10-30s 音频（wav/mp3/m4a，<8M）或点"开始录制" → 训练完出现在音色库"我的音色" → 消耗 1 个复刻槽位
2. **音色设计**（从零设计虚拟音色）: 控制台侧边栏"音色设计"菜单
- 入口还有: 音色库页右上角"创作你的音色"按钮
- 复刻音色 API 调用: `X-Api-Resource-Id: seed-icl-2.0` + `speaker: <复刻音色ID>`（ID 在音色库/我的音色可见）
- **坑: 服务管理页"11 音色"是复刻槽位容量，不是已有复刻音色数**——"我的音色"初始为 0，容易误读
