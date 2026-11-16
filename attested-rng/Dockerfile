FROM python:3.12-slim
WORKDIR /app
COPY server.py index.html ./
# /app/data is the encrypted-storage mount target ([storage] paths in
# enclava.toml). The workload rootfs is read-only, so the path must be a
# declared VOLUME for enclava-init to bind into — without it the deploy fails
# with tee_error "Read-only file system (os error 30)".
VOLUME ["/app/data"]
EXPOSE 8080
CMD ["python3", "/app/server.py"]
