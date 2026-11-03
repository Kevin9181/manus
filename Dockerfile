# ---- 构建阶段：编译工具链和 Python 依赖只存在于这一层 ----
FROM python:3.11-slim AS builder

WORKDIR /build

# gcc / python3-dev 仅用于编译依赖，不会进入最终镜像
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---- 运行阶段：只保留运行期需要的东西 ----
FROM python:3.11-slim

WORKDIR /app

# 时区数据是运行期需要的
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
    && ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY . .

RUN mkdir -p /app/temp

ENV PYTHONUNBUFFERED=1 \
    DOCKER_LOG_MAX_SIZE=10m \
    DOCKER_LOG_MAX_FILE=3

CMD ["python", "main.py"]
