# Photo Dream Web service

FastAPI 应用入口为 `web.app:app`。启动、PostgreSQL、Docker、数据迁移及测试说明见 [项目 README](../README.md)。

入口 `/photodream/` 为管理中台；`/photodream/login` 登录/注册；`/photodream/single` 为单图工作台；`/photodream/storyboard` 为分镜工作台。首次升级执行 `venv/bin/python -m web.manage admin --claim-legacy`，交互设置管理员密码并接收无归属的历史项目。
