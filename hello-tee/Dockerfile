FROM python:3.12-slim
WORKDIR /app
COPY server.py index.html ./
# Stateless example — no VOLUME here. If you add [storage] paths to
# enclava.toml, each path must be declared VOLUME in this image (the workload
# rootfs is read-only; enclava-init bind-mounts into declared volumes only).
EXPOSE 8080
CMD ["python3", "/app/server.py"]
