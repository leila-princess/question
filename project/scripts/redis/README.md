# Redis 离线部署（ZNWD 会话记忆）

后端 `config.py` 启动时会连接 Redis。失败常见两类原因：

1. **未安装 Python 包** `redis` → 日志：`redis 包未安装`
2. **本机无 Redis 服务**（6379 未监听）→ 日志：`Redis 初始化失败: Error 10061` 等

## 一、有网机器：打包

```powershell
cd F:\antd-demo1
.\scripts\redis\pack_redis_offline.ps1 -DownloadServer
```

生成目录：`F:\antd-demo1\offline_packages\redis\`

| 子目录/文件 | 说明 |
|-------------|------|
| `wheels/` | `redis` Python wheel |
| `server/` | Windows `redis-server.exe`（需 `-DownloadServer`） |
| `requirements-redis.txt` | pip 依赖清单 |
| `redis.windows.conf` | 服务配置 |
| `install_redis_offline.ps1` | 离线安装脚本 |

将整个 `offline_packages\redis` 文件夹拷到 U 盘。

## 二、离线机器：一键安装

```powershell
cd F:\antd-demo1
.\offline_packages\redis\install_redis_offline.ps1 -ProjectRoot F:\antd-demo1 -StartAfterInstall
```

参数说明：

- `-CondaBase C:\Users\xxx\anaconda3`  conda 根目录
- `-MineruEnv agent`  Python 环境名
- `-SkipServer`  仅装 pip 包（已自备 Memurai/Redis 时）
- `-SkipPip`  仅装 redis-server
- `-StartAfterInstall`  安装后自动启动并 ping

## 三、启动 Redis

```powershell
F:\antd-demo1\tools\redis\start_redis.ps1
```

或在新窗口常驻：

```powershell
cd F:\antd-demo1\tools\redis
.\redis-server.exe redis.windows.conf
```

## 四、验证

```powershell
conda activate agent
python -c "import redis; r=redis.Redis(host='127.0.0.1',port=6379,decode_responses=True); print(r.ping())"
```

后端重启后应看到：`Redis 连接成功 (db=0)`

## 五、.env 配置

安装脚本会自动合并（若 `.env` 尚无 `REDIS_HOST`）：

```env
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_MEMORY_TTL=1800
```

## 六、纳入 start-all（可选）

在 `start-all.ps1` 后端启动前增加：

```powershell
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& 'F:\antd-demo1\tools\redis\start_redis.ps1'"
```
