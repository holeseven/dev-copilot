# @author lxy
# Dev Copilot 引擎镜像：python:3.12-slim + uv 安装依赖 + uvicorn 启动
FROM python:3.12-slim

# 避免生成 .pyc、日志实时输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 安装 uv（极速 Python 包管理器）
RUN pip install --no-cache-dir uv

# 先拷贝依赖声明，利用 Docker 层缓存（依赖不变则不重装）
COPY pyproject.toml ./
# 用 uv 将依赖安装到系统环境（--system 表示不建虚拟环境）
RUN uv pip install --system --no-cache -r pyproject.toml

# 拷贝源码（引擎代码放在镜像内的 engine 包路径下）
COPY . ./engine

EXPOSE 8000

# 以模块方式启动 FastAPI 应用
CMD ["uvicorn", "engine.main:app", "--host", "0.0.0.0", "--port", "8000"]
