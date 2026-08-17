微信 TTS 挂件（Windows x64）

一、安装
1. 解压整个 WeChatTTS 文件夹，不要只复制 WeChatTTS.exe。
2. 在目标电脑安装 VB-CABLE，安装后重启 Windows。
3. 在 Windows 声音设置中，把默认麦克风设为 CABLE Output。

二、首次运行
1. 打开微信桌面客户端并进入要接收语音的聊天。
2. 运行 WeChatTTS.exe。
3. 点击“设置”，填写目标电脑可访问的 TTS 地址和 Bearer Token。
4. 输入文本，检查状态显示设备已连接后，再点击“发送语音”。

三、说明
- 关闭主窗口后，软件会继续在系统托盘运行。
- 按 Ctrl+Alt+Z 可唤起无边框、只有文本框的快速发送浮层。
- 浮层按 Enter 发送、Shift+Enter 换行、Esc 隐藏；提交后会立即隐藏。
- 左键托盘图标恢复主窗口，右键菜单可以快速发送或彻底退出。
- 软件配置和 Token 保存在当前 Windows 用户的 %APPDATA%\WeChatTTSWidget 中，不包含在压缩包里。
- 日志位于 %APPDATA%\WeChatTTSWidget\widget.log。
- 如果 TTS 服务位于另一台机器，请确保目标电脑能解析服务地址并访问对应端口。
- 软件会操作当前打开的微信聊天；发送前请确认联系人正确。
