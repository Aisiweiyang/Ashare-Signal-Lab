#!/usr/bin/env bash
# AshareSignal Lab 一键演示（保留已有数据；--fresh-demo 创建独立新库）
set -euo pipefail
cd "$(dirname "$0")/.."

fresh_demo=false
skip_sync=false
no_serve=false
for arg in "$@"; do
  case "$arg" in
    --fresh-demo) fresh_demo=true ;;
    --skip-sync) skip_sync=true ;;
    --no-serve) no_serve=true ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

echo "[1/4] 检查项目环境..."
if ! "$skip_sync"; then
  uv --cache-dir .uv-cache sync --frozen
fi
python=.venv/bin/python
"$python" -c "import asg_api.main, asg_pipeline; print('项目模块导入正常')"

echo "[2/4] 构建前端工作台..."
(
  cd apps/web
  if [ ! -f node_modules/typescript/bin/tsc ] || [ ! -f node_modules/vite/bin/vite.js ]; then
    npm ci
  fi
  node node_modules/typescript/bin/tsc -b
  node node_modules/vite/bin/vite.js build
)

if "$fresh_demo"; then
  demo_id=$("$python" -c "import uuid; print(uuid.uuid4().hex)")
  export ASG_DATABASE_URL="sqlite:///./data/demo/session-$demo_id.db"
else
  export ASG_DATABASE_URL="${ASG_DATABASE_URL:-sqlite:///./data/demo/ashare_lab.db}"
fi
echo "[3/4] 准备合成演示数据（复用已绑定库，不删除旧库）..."
"$python" scripts/prepare_demo.py
if "$no_serve"; then
  echo "准备完成；未启动服务。"
  exit 0
fi

echo "[4/4] 启动 API + 工作台: http://127.0.0.1:8300"
echo "      演示账号: admin / researcher / annotator_a / viewer，密码 Demo12345!"
exec "$python" -m uvicorn asg_api.main:app --host 127.0.0.1 --port 8300
