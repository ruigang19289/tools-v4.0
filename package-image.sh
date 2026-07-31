#!/bin/sh
# 构建 tools-app 镜像，导出镜像 tar，生成 create-container.sh，并打包为 tar.gz。

set -eu

IMAGE_NAME="tools-app"
CONTAINER_NAME="sds-tools"
HOST_HTTP_PORT="6000"
HOST_WEB_PORT="6500"
CONTAINER_HTTP_PORT="6000"
CONTAINER_WEB_PORT="6500"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp}"
OUTPUT_DIR="${OUTPUT_DIR%%/}"

usage() {
    printf '用法: %s <版本号> [输出目录]\n' "$0"
    printf '\n'
    printf '参数说明:\n'
    printf '  版本号    镜像版本，例如 v1.3 或 1.3；不带 v 时会自动补成 v1.3\n'
    printf '  输出目录  可选，压缩包输出目录，默认: /tmp\n'
    printf '\n'
    printf '示例:\n'
    printf '  %s v1.3\n' "$0"
    printf '  %s 1.3 /tmp\n' "$0"
}

case "${1:-}" in
    ""|-h|--help)
        usage
        exit 0
        ;;
esac

VERSION="$1"
case "${VERSION}" in
    -*|*/*|*:*|*' '*|*'\t'*)
        printf '错误: 版本号不合法: %s\n' "${VERSION}" >&2
        usage
        exit 1
        ;;
    v[0-9]*|[0-9]*) ;;
    *)
        printf '错误: 版本号格式应类似 v1.3 或 1.3，当前输入: %s\n' "${VERSION}" >&2
        usage
        exit 1
        ;;
esac

case "${VERSION}" in
    v*) IMAGE_TAG="${VERSION}" ;;
    *) IMAGE_TAG="v${VERSION}" ;;
esac
if [ "${2:-}" != "" ]; then
    OUTPUT_DIR="$2"
fi

if [ ! -f "Dockerfile" ]; then
    printf '错误: 未找到 Dockerfile，请在项目根目录执行该脚本。\n' >&2
    exit 1
fi

mkdir -p "${OUTPUT_DIR}"

IMAGE_REF="${IMAGE_NAME}:${IMAGE_TAG}"
IMAGE_TAR="${IMAGE_NAME}_${IMAGE_TAG}.tar"
PACKAGE_NAME="${IMAGE_NAME}_${IMAGE_TAG}.tar.gz"
PACKAGE_DIR="${OUTPUT_DIR}/${IMAGE_NAME}_${IMAGE_TAG}_package"
IMAGE_TAR_PATH="${PACKAGE_DIR}/${IMAGE_TAR}"
CREATE_SCRIPT_PATH="${PACKAGE_DIR}/create-container.sh"
PACKAGE_PATH="${OUTPUT_DIR}/${PACKAGE_NAME}"

printf '=== 构建镜像: %s ===\n' "${IMAGE_REF}"
docker build -t "${IMAGE_REF}" .

rm -rf "${PACKAGE_DIR}"
mkdir -p "${PACKAGE_DIR}"

printf '=== 导出镜像: %s ===\n' "${IMAGE_TAR_PATH}"
docker save -o "${IMAGE_TAR_PATH}" "${IMAGE_REF}"

printf '=== 生成 create-container.sh ===\n'
cat > "${CREATE_SCRIPT_PATH}" <<EOF
#!/bin/bash

set -e

TAR_FILE="${IMAGE_TAR}"
IMAGE_NAME="${IMAGE_NAME}"
IMAGE_TAG="${IMAGE_TAG}"
CONTAINER_NAME="${CONTAINER_NAME}"
HOST_HTTP_PORT="${HOST_HTTP_PORT}"
HOST_WEB_PORT="${HOST_WEB_PORT}"
CONTAINER_HTTP_PORT="${CONTAINER_HTTP_PORT}"
CONTAINER_WEB_PORT="${CONTAINER_WEB_PORT}"

cd "\$(dirname "\$0")"

if [ ! -f "\$TAR_FILE" ]; then
    echo "ERROR: image tar not found: \$TAR_FILE"
    exit 1
fi

echo "=== Load image ==="
docker load -i "\$TAR_FILE"

echo "=== Remove old container if exists ==="
if docker ps -a --format '{{.Names}}' | grep -qx "\$CONTAINER_NAME"; then
    docker rm -f "\$CONTAINER_NAME"
fi

echo "=== Prepare VDBench result directory ==="
if [ -d /root/vdbench50407 ]; then
    mkdir -p /root/vdbench50407/output
else
    mkdir -p /root/vdbench50407/output
fi

VDBENCH_MOUNT="/root/vdbench50407/output:/app/data/vdbench-result"
# Prefer the standard Ed25519 key; use the local RSA key automatically on
# hosts that were provisioned before Ed25519 became the default.
SSH_PRIVATE_KEY_PATH="/root/.ssh/id_ed25519"
if [ ! -f "\$SSH_PRIVATE_KEY_PATH" ] && [ -f "/root/.ssh/id_rsa" ]; then
    SSH_PRIVATE_KEY_PATH="/root/.ssh/id_rsa"
fi
CONTAINER_SSH_PRIVATE_KEY_PATH="/root/.ssh/id_ed25519"

if [ ! -f "\$SSH_PRIVATE_KEY_PATH" ]; then
    echo "ERROR: SSH private key not found: /root/.ssh/id_ed25519 or /root/.ssh/id_rsa"
    exit 1
fi

echo "Using SSH private key: \$SSH_PRIVATE_KEY_PATH"

echo "=== Start container ==="
docker run -d \\
    --name "\$CONTAINER_NAME" \\
    --restart unless-stopped \\
    --network host \\
    -v "\$VDBENCH_MOUNT" \\
    -v "\$SSH_PRIVATE_KEY_PATH:\$CONTAINER_SSH_PRIVATE_KEY_PATH:ro" \\
    -e "TOOLS_ANSIBLE_PRIVATE_KEY_PATH=\$CONTAINER_SSH_PRIVATE_KEY_PATH" \\
    "\$IMAGE_NAME:\$IMAGE_TAG"

echo "=== Container status ==="
docker ps --filter "name=\$CONTAINER_NAME"

echo "=== Done ==="
echo "Image: \$IMAGE_NAME:\$IMAGE_TAG"
echo "Container: \$CONTAINER_NAME"
echo "Logs: docker logs -f \$CONTAINER_NAME"
EOF
chmod +x "${CREATE_SCRIPT_PATH}"

printf '=== 生成压缩包: %s ===\n' "${PACKAGE_PATH}"
rm -f "${PACKAGE_PATH}"
(
    cd "${PACKAGE_DIR}"
    tar -zcf "${PACKAGE_PATH}" "create-container.sh" "${IMAGE_TAR}"
)

printf '\n打包完成:\n'
printf '  %s\n' "${PACKAGE_PATH}"
printf '\n目标主机部署命令:\n'
printf '  tar -zxf %s\n' "${PACKAGE_NAME}"
printf '  ./create-container.sh\n'
