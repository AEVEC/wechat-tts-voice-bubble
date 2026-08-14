# 微信 TTS 语音发送工具

本工具调用现有 TTS 服务生成 WAV，再将音频播放到 VB-CABLE。微信从
`CABLE Output` 虚拟麦克风录音，因此接收方看到的是正常语音气泡。

## 首次配置

1. 安装 VB-CABLE 并按安装器要求重启 Windows。
2. 在微信音频设置中，将麦克风选择为
   `CABLE Output (VB-Audio Virtual Cable)`。如果微信没有设备选择项，
   可将它暂时设为 Windows 默认输入设备。
3. 使用新的 TTS Token。不要继续使用已经公开过的 Token。

## 使用

在 PowerShell 中为当前窗口设置 Token：

```powershell
$env:TTS_TOKEN = "替换为新Token"
```

发送前先打开目标微信聊天窗口，然后执行：

```powershell
& "C:\Users\win\wechat-tts-voice\send-voice.cmd" "你好，这里是小菲。"
```

程序在生成音频后会倒计时 5 秒。倒计时期间保持正确的微信聊天窗口位于最前面。
若前台程序不是微信，程序会取消发送。

仅测试 TTS 并保存 WAV：

```powershell
& "C:\Users\win\wechat-tts-voice\send-voice.cmd" `
  --save-wav "C:\Users\win\wechat-tts-voice\test.wav" `
  "你好，这里是小菲。"
```

列出可用的音频播放设备：

```powershell
& "C:\Users\win\wechat-tts-voice\send-voice.cmd" --list-devices
```

## Conda 环境

环境名称：`wechat-tts-voice`

```powershell
& "C:\Users\win\miniforge3\Scripts\activate.bat" wechat-tts-voice
```

单条语音默认最多 58 秒。更长文本请拆分后发送。

## 远程桌面限制

在 Windows 远程桌面（RDP）会话中，远程电脑本机的录音端点通常不会暴露给
微信。此时即使 VB-CABLE 已正常安装，微信仍可能显示“未找到麦克风”。需要在
电脑的本地控制台会话中运行微信，或改用不会隔离主机音频设备的远控方式。

程序优先读取 Windows WTS 的实时会话协议。`SESSIONNAME` 仅在 WTS 查询失败时
作为兜底，因为从远程桌面返回本地控制台后，该环境变量可能仍残留为 `RDP-Tcp`。
