# 阅见 · Django 本地版

标准 Django Templates 项目。Django 同时负责页面、登录、业务逻辑和 SQLite 数据库；页面交互不依赖 React。

## 初次安装

双击 `setup.bat`。完成后双击 `start.bat`，打开 http://127.0.0.1:8000/。

- 初始用户名：`teacher`
- 初始密码：`reading123`（首次登录后请在 `/admin/` 修改）
- 数据库文件：`db.sqlite3`

手机与电脑连接同一 Wi-Fi 后，在手机访问 `http://电脑局域网IP:8000/`。Windows 防火墙首次询问时允许“专用网络”。

## 已迁移功能

老师登录、多个班级、学生、404 本书及现有题库、阅读记录、周榜/月榜、词数和时长统计、60 分过关、首次过关累计词数、Excel 导出。
