# 基于 Alpine 镜像构建
FROM alpine:latest

# 安装基础依赖
RUN sed -i 's#https://dl-cdn.alpinelinux.org#https://mirrors.aliyun.com#g' /etc/apk/repositories \
&& apk update \
&& apk add --no-cache \
    python3 \
    py3-pip \
    nodejs \
    npm \
    bash \
    tzdata \
    openssh-client \
    curl \
    sshpass \
    nginx

# 设置时区和 Ansible 默认配置
ENV TZ=Asia/Shanghai
ENV ANSIBLE_HOST_KEY_CHECKING=False
ENV TOOLS_ANSIBLE_PRIVATE_KEY_PATH=/root/.ssh/id_ed25519
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && mkdir -p /etc/ansible \
    && printf '%s\n' \
        '[defaults]' \
        'interpreter_python = auto_legacy_silent' \
        'host_key_checking = False' \
        'deprecation_warnings = False' \
        'forks = 50' \
        'timeout = 20' \
        'gathering = explicit' \
        'retry_files_enabled = False' \
        '' \
        '[ssh_connection]' \
        'pipelining = True' \
        'ssh_args = -o ControlMaster=auto -o ControlPersist=300s -o ControlPath=/tmp/ssh_mux_%h_%p_%r -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o Compression=no' \
        > /etc/ansible/ansible.cfg

WORKDIR /app

# 复制项目文件
COPY . /app/

# 安装 Python 依赖
ENV AUTOBAHN_USE_NVX=0
RUN pip3 install --no-cache-dir --break-system-packages \
    -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
    ansible-core==2.16.14 \
    django \
    djangorestframework \
    django-cors-headers \
    channels \
    daphne \
    paramiko

# 安装前端依赖并构建
WORKDIR /app/frontend
RUN npm install --registry=https://registry.npmmirror.com && \
    npm run build && \
    rm -rf node_modules

WORKDIR /app

# 暴露端口
EXPOSE 6000 6500

# 启动脚本
RUN chmod +x /app/start-docker.sh

CMD ["/app/start-docker.sh"]
