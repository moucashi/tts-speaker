# tts-speaker

基于 Piper 的中文交互式语音生成命令行工具。程序会读取用户输入的文本，调用本地 Piper 中文模型生成 WAV 语音文件，并在生成完成后询问是否立即播放。

## 功能

- 交互式输入文本并生成语音。
- 生成完成后可选择立即播放。
- 输入过程中可清空当前文本。
- 生成过程中可取消当前任务。
- 语音文件按日期自动分类保存。

## 环境要求

- Python 3.14
- uv
- 默认 Piper 中文语音：
  - `zh_CN-xiao_ya-medium.onnx`
  - `zh_CN-xiao_ya-medium.onnx.json`

默认语音文件需要位于项目根目录。如果当前目录缺少默认语音文件，程序会通过 Piper 的 Python 下载接口自动下载，并在终端显示下载状态。

## 安装依赖

同步 uv 环境：

```powershell
uv sync
```

项目依赖已在 `pyproject.toml` 中声明，`uv sync` 会自动创建 `.venv` 并安装依赖。首次生成中文语音时，`piper` 和 `g2pw` 可能会下载中文音素化相关资源。

如需重新生成锁文件：

```powershell
uv lock
```

## 使用方式

启动交互式程序：

```powershell
uv run python -X utf8 interactive_tts.py
```

交互式程序会通过 Python API 直接加载 Piper 模型并生成音频，不会在每次生成时启动 `python -m piper` 子进程。

指定模型：

```powershell
uv run python -X utf8 interactive_tts.py --model zh_CN-xiao_ya-medium
```

自动下载仅支持语音名形式的模型参数。如果通过 `--model` 传入具体 `.onnx` 文件路径，请先手动准备对应的 `.onnx` 和 `.onnx.json` 文件。

指定输出目录：

```powershell
uv run python -X utf8 interactive_tts.py --output-dir voices
```

## 交互流程

1. 输入一段文本并按回车。
2. 程序生成对应的语音文件。
3. 生成完成后提示是否播放。
4. 播放选择完成后，继续输入下一段文本。

## 按键说明

| 场景 | 按键 | 行为 |
| --- | --- | --- |
| 输入文本时 | 回车 | 生成语音 |
| 输入文本时 | Esc 或 Ctrl+C | 清空当前输入 |
| 空输入等待时 | Ctrl+C | 退出程序 |
| 生成语音时 | Esc 或 Ctrl+C | 取消当前生成 |
| 询问是否播放时 | 回车或 y | 播放语音 |
| 询问是否播放时 | Esc 或 n | 不播放语音 |

## 输出文件

默认输出目录为 `voices`，程序会按日期创建子目录：

```text
voices/YYYY-MM-DD/HHMMSS_文本开头.wav
```

示例：

```text
voices/2026-05-08/194136_测试日期分类文件名.wav
```

文件名规则：

- 使用当天时间作为前缀，格式为 `HHMMSS`。
- 使用输入文本开头作为文件名主体。
- 文本中的空白会被移除。
- Windows 不允许的文件名字符会替换为 `_`。
- 文本片段最多保留 24 个字符。
- 如果同名文件已存在，会自动追加 `_01`、`_02` 等序号。

## 常见问题

### 中文资源读取出现 GBK 解码错误

在 Windows 上，`g2pw` 读取 UTF-8 资源文件时可能触发默认 GBK 编码问题。请使用 UTF-8 模式启动：

```powershell
uv run python -X utf8 interactive_tts.py
```

脚本内部调用 Piper 时也会设置 `PYTHONUTF8=1`。

### 缺少中文音素化依赖

如果出现 `ModuleNotFoundError`，请确认依赖已安装：

```powershell
uv sync
```

常见缺失模块包括：

- `g2pw`
- `torch`
- `unicode_rbnf`
- `requests`
- `sentence_stream`

## 直接调用 Piper

如需绕过交互式程序，可直接运行：

```powershell
uv run python -X utf8 -m piper -m zh_CN-xiao_ya-medium -f test.wav -- '测试'
```
