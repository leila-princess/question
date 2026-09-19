# 智能试验方案平台：源码与部署脚本

本仓库保留 `project/` 中的前后端源码、项目文档和部署脚本，以及 `scripts/`、配置模板和根目录安装入口。

不包含离线运行环境、第三方依赖包、构建缓存、模型权重、数据库数据、生成结果和压缩包。原完整离线包保留在本地。

## 使用说明

- 前端依赖定义见 `project/fronted/package.json`，后端依赖见 `project/backend/requirements*.txt`。
- 使用前按实际环境配置 `.env` 和数据库连接；上传副本中发现的 API 密钥已替换为 `REPLACE_WITH_YOUR_API_KEY`，部分密码替换为 `CHANGE_ME`。
- 模型、数据库服务及运行环境需另行准备。仅克隆本仓库无法直接运行完整离线安装。
- `README_DEPLOY.md` 描述完整离线包的部署流程，其中引用的 `env/`、`runtime/`、`models/`、`data/` 等内容需自行准备。
