# 微信 TTS 挂件

双击桌面的 `微信 TTS 挂件.vbs`，或项目目录中的 `启动微信TTS挂件.vbs`，打开
挂件。首次使用点击“设置”，填写 TTS 地址和新的 Bearer Token。Token 使用 Windows
DPAPI 加密，只能由当前 Windows 用户解密。

界面使用 Python `CustomTkinter` 构建，保持轻量桌面挂件形态，同时采用圆角卡片、
Material 3 色阶和清晰的可用/禁用状态。设计参考图保存在
`design/material-widget-mockup.png`。

## 使用方法

1. 在微信中打开准备接收语音的聊天。
2. 在挂件输入文字。
3. 点击“发送语音”或按 `Ctrl+Enter`。

挂件会生成 TTS、切换到微信、点击语音录制按钮、验证录音控件、播放音频并点击发送。
播放保持原有的阻塞式音频路径；若 VB-CABLE 的 WASAPI 端点启动失败，会按
DirectSound、MME 的顺序自动回退。
只有微信退出录音模式后，挂件才会把本次操作判定为成功。

## 发送保护

发送按钮只有在以下条件均满足时才可点击：

- 当前不是 RDP 远程桌面会话；
- 恰好有一个微信主窗口；
- Windows 默认麦克风是 `CABLE Output`；
- VB-CABLE 播放端可用；
- 已配置 TTS 地址和 Token；
- 输入框不为空。

挂件无法可靠读取微信自绘界面中的联系人名称，因此发送前仍要由用户确认微信当前
打开的是正确聊天。点击“发送语音”即表示发送给该当前聊天。

## 排错

日志文件位于：

```text
%APPDATA%\WeChatTTSWidget\widget.log
```

如需在控制台查看错误，运行 `debug-widget.cmd`。

## 构建发行包

在项目目录运行 `build-release.ps1`，会生成 `onedir` 版本并压缩为：

```text
release\WeChatTTS-win-x64.zip
```

发行包不包含 `%APPDATA%\WeChatTTSWidget` 中的本机配置和 Token。目标电脑需要单独
安装 VB-CABLE、重启 Windows，并重新填写可访问的 TTS 地址和 Token。
