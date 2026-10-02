# srun-login 

杭州电子科技大学校园网 Wi-Fi 登录 / 深澜（srun）校园网模拟登录

重写互联网上现存的登陆脚本，适应2024年暑假后的网络变化，支持`生活区`、`教学区`和`绍兴校区`的登录认证，同时支持`多用户账号`定时切换登录

## 开始使用

### 直接运行

```bash
# 克隆项目
git clone git@github.com:JBNRZ/srun-login.git

# 安装依赖
cd srun-login && pip3 install -r requirements.txt

# 创建并编辑auth.json
cat<<EOF>auth.json
[
  {"username": "username1", "password": "password1"},
  {"username": "username2", "password": "password2"},
  {"username": "username3", "password": "password3"} 
]
EOF

# 运行
nohup python3 login.py &
```

### 绍兴校区

使用 `--campus shaoxing` 选择绍兴门户 `https://yue.hdu.edu.cn`，接入控制器 ID 为 `1`。
不指定校区时，保持原有下沙校区门户和认证方式。

```bash
# 只查询在线状态，不读取账号配置，不登录或注销
python3 login.py --campus shaoxing --check

# 执行一轮登录流程，不先注销，不启动定时任务
python3 login.py --campus shaoxing --once

# 长期运行：每两分钟检查，每六小时注销并随机选择账号重新登录
nohup python3 login.py --campus shaoxing &
```

`--once` 执行一轮登录流程后退出，保留原有的 `ac_id` 自动重试策略。
登录流程和只读检查成功时退出码为 `0`，失败时为非零；已在线状态下的登录结果不能代替离线登录验证。
绍兴校区已实际验证单次登录返回 `login_ok`；长期保活、六小时账号切换及下沙校区回归仍需进一步验证。

### 断网后自动认证（Windows / Linux / macOS）

新增独立脚本 `reconnect.py`，复用原登录程序，不修改原定时任务。
将 `auth.json` 放在脚本所在目录，安装原项目依赖后运行：

```bash
python reconnect.py --campus shaoxing
```

默认每 15 秒检测一次公网连通性，连续失败 3 次后查询校园网在线状态；
仅当门户明确返回 `not_online_error` 时才执行登录，不主动注销。
门户仍报告在线、无法访问门户或返回其他错误时，不执行登录。
每轮认证最长 60 秒，结束后至少等待 60 秒再尝试下一轮。
日志写入脚本目录的 `reconnect.log`，原登录日志仍写入 `srun_login.log`。

```bash
python reconnect.py --campus shaoxing --interval 15 --failures 3 --cooldown 60 --login-timeout 60
```

Windows 任务计划程序可使用虚拟环境的 `python.exe` 启动 `reconnect.py --campus shaoxing`。
不要同时运行原 `login.py` 的常驻任务，避免其六小时刷新主动注销或并发认证。
公网检测使用微软连通性检测地址，并校验响应正文；该检测不使用环境代理。
单个检测地址不可达不一定代表整体断网，因此会进一步核对门户状态。
本脚本只能恢复校园网认证，不能重新连接已断开的 Wi-Fi 或修复物理网络故障。
检测和认证均失败时继续等待，不会退出；按 Ctrl+C 停止。
Docker 使用时需自行将 `reconnect.py` 加入镜像，现有 Dockerfile 不包含该脚本。

### Docker 运行

```bash
# 创建并编辑 auth.json
cat<<EOF>auth.json
[
  {"username": "username1", "password": "password1"},
  {"username": "username2", "password": "password2"},
  {"username": "username3", "password": "password3"}
]
EOF

# 使用 GHCR 镜像运行
docker run -d \
  --name srun-login \
  --restart unless-stopped \
  --network host \
  -v "$(pwd)/auth.json:/app/auth.json:ro" \
  ghcr.io/jbnrz/srun-login:latest
```

如果需要查看日志：

```bash
docker logs -f srun-login
```

测试本地修改的绍兴版本时，先构建镜像，再以只读模式查询状态：

```bash
docker build -t srun-login:local .
docker run --rm --network host \
  srun-login:local python login.py --campus shaoxing --check
```

长期运行时挂载 `auth.json`，并将容器命令设为 `python login.py --campus shaoxing`。
上述 host 网络用法应在支持主机网络且连接校园网的环境中使用；远程 GHCR 镜像不会自动包含本地修改。

如果需要使用日期版本镜像，将 `latest` 替换为对应日期 tag，例如：

```bash
docker pull ghcr.io/jbnrz/srun-login:20260630
```

## 配置开机自启

```yaml
[Unit]
Description=srun login

[Service]
Type=simple
User=root
ExecStart=python3 /path/to/your/file.py
WorkingDirectory=/path/to/your/dir

[Install]
WantedBy=multi-user.target
```

## License

MIT License
