# zhihua-service

知画部署在用户自有优云智算实例上的远端配套服务。它向知画桌面端提供稳定、版本化的 API，并通过本机 HTTP/WebSocket 连接 ComfyUI。

## 当前功能

- 服务健康、版本和能力查询
- Python、Torch、CUDA 和 ComfyUI 目录状态检查
- ComfyUI 连接与队列状态检查
- 按受信任 workflow_id 加载 API-format 工作流并推进 SQLite 任务队列
- 提交 /prompt、轮询 /history，记录进度、错误与结果清单
- 经鉴权上传参考图片/视频，并按结果清单下载成品（支持 Range 续传）
- 内置 9 个由实测流程固化的 H3/SeedVR2 API 工作流模板
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

新镜像还需要安装固定版本的 SeedVR2 自定义节点，并把公共模型库中的权重接入 ComfyUI：

```bash
bash deploy/install-comfyui-runtime.sh
```

脚本为 SeedVR2 3B FP8、VAE、Qwen Image/Edit 和 H3 Turbo LoRA 创建指向只读 `/model` 公共模型库的符号链接，不复制或重新下载模型权重。它还会探测镜像中 xformers 与 Diffusers/PyTorch 的实际 ABI 兼容性，只在不兼容时卸载 xformers。节点提交版本和公共模型路径记录在 `deploy/comfyui-runtime.lock`。脚本完成后重启 ComfyUI。

启动开发服务：

```bash
cd /root/zhihua-service
cp .env.example .env
.venv/bin/python -m uvicorn zhihua_service.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

官方 ComfyUI 镜像内置 Supervisor。安装随仓库提供的服务配置后，知画服务会在实例无卡/GPU 模式切换或进程异常退出后自动恢复：

```bash
sudo bash deploy/install-supervisor.sh /root/zhihua-service
```

该配置仍只监听 `127.0.0.1:8000`，由桌面端的 SSH 隧道访问，不直接暴露公网端口。

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
- `POST /api/v1/inputs?filename=...`
- `DELETE /api/v1/inputs/{input_id}`
- `GET /docs`

`/health` 只检查知画服务自身；`/ready` 还要求 ComfyUI 已就绪。无卡模式下服务健康但 ComfyUI 未就绪是预期状态。

## 测试

```bash
cd /root/zhihua-service
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

API 密钥、GitHub 凭据、SSH 私钥、实例密码、用户素材和生成结果不得提交到仓库或写入发布镜像。


## Secure job protocol (v0.3)

Health, version, capabilities, environment status, and ComfyUI readiness stay read-only.
The handshake and every job endpoint require a deployment-specific bearer token. Job calls
also require `X-Zhihua-API-Version: v1` and `X-Zhihua-Client-Version`. The token must be
generated during deployment, stored by the desktop client in the operating-system credential
store, and supplied to the remote process through its environment. It must never be committed.

The server accepts workflow identifiers from the configured allowlist only. It does not accept
arbitrary ComfyUI workflow JSON. `client_request_id` is an idempotency key, so reconnecting
clients can recover the original job without silently submitting a duplicate.

Authenticated endpoints:

- `POST /api/v1/handshake`
- `POST /api/v1/jobs`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/queue`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `GET /api/v1/jobs/{job_id}/result`
- `GET /api/v1/jobs/{job_id}/artifacts/{artifact_id}`

Jobs are persisted in SQLite. The background worker loads trusted API-format prompt templates
from ZHIHUA_WORKFLOW_DIRECTORY, submits them to ComfyUI, polls history, and stores progress,
errors, and result manifest schema v1. If ComfyUI or a workflow template is unavailable, the
job remains queued with an explanatory status instead of terminating the service. Artifact
paths are stored server-side and a client can only download files listed in that job's manifest.
The cloud-provider API private key is not used or stored by this service.


## Workflow templates

Each allowed workflow uses a workflow-id JSON file in ZHIHUA_WORKFLOW_DIRECTORY. Export the
workflow from ComfyUI in API format. A plain API prompt is accepted as-is. To inject validated
job parameters, wrap it in an object with prompt and bindings fields. Each binding key names a
job parameter and its value is a list of JSON paths into the prompt.

The service chooses the file from its configured workflow allowlist; clients cannot submit
arbitrary node graphs. The repository templates come from the 2026-09-08 5090 validation run and
reference the public-model mount filenames. A clean release image must still run the environment
probe and one GPU smoke test before publication.


## MVP workflow identifiers

The accepted H3 identifiers are t2v, i2v, flf2v, and ref2va, each with turbo-v1 and
high-v1 variants. seedvr2-1080p-v1 covers optional temporal enhancement. Legacy workflow
identifiers are not retained during initial development.

The capabilities response keeps workflows as the accepted identifier list and adds
available_workflows for templates that are installed and structurally readable on this instance.
Templates can also declare requiredNodeTypes. When ComfyUI is reachable, those node types must
be loaded before the workflow is reported as available. A client should enable generation from
available_workflows rather than assuming every accepted identifier is installed.
