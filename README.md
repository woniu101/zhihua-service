# zhihua-service

知画部署在用户自有优云智算实例上的远端配套服务。它向知画桌面端提供稳定、版本化的 API，并通过本机 HTTP/WebSocket 连接 ComfyUI。

## 当前功能

- 服务健康、版本和能力查询
- Python、Torch、CUDA 和 ComfyUI 目录状态检查
- ComfyUI 连接与队列状态检查
- 在无卡环境中正常运行服务与离线测试
- OpenAPI 文档与模拟测试

服务不会导入或修改 ComfyUI 内部模块。两者作为独立进程运行，知画服务使用独立的 Python 虚拟环境，避免改变官方 ComfyUI 镜像的依赖。

## 优云智算开发环境

实例使用官方 ComfyUI 容器镜像。服务端代码位于：

```text
/root/zhihua-service
```

首次安装：

```bash
cd /root/zhihua-service
/root/miniconda3/bin/python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

启动开发服务：

```bash
cd /root/zhihua-service
cp .env.example .env
.venv/bin/python -m uvicorn zhihua_service.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

服务只监听实例内的 `127.0.0.1:8000`。桌面端通过 SSH 隧道连接：

```bash
ssh -N -L 18000:127.0.0.1:8000 -p <port> root@<host>
```

客户端随后访问 `http://127.0.0.1:18000`。不要将无鉴权的开发接口直接暴露到公网。

日常 API、Git、测试和部署脚本在优云智算无卡模式运行。只有 ComfyUI 推理和 GPU 集成测试需要启动 5090。

## API

- `GET /api/v1/health`
- `GET /api/v1/version`
- `GET /api/v1/capabilities`
- `GET /api/v1/environment/status`
- `GET /api/v1/comfyui/status`
- `GET /api/v1/ready`
- `GET /docs`

`/health` 只检查知画服务自身；`/ready` 还要求 ComfyUI 已就绪。无卡模式下服务健康但 ComfyUI 未就绪是预期状态。

## 测试

```bash
cd /root/zhihua-service
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

API 密钥、GitHub 凭据、SSH 私钥、实例密码、用户素材和生成结果不得提交到仓库或写入发布镜像。
