# ---- Update .env.example ----
with open(r"D:\git\novelhub\.env.example", "r", encoding="utf-8") as f:
    env = f.read()

env += "\n# =====================\n# Storage Backend\n# =====================\n\n"
env += "# local | s3 | webdav\nSTORAGE_BACKEND=local\n\n"
env += "# S3/MinIO settings (when STORAGE_BACKEND=s3)\n"
env += "# S3_ENDPOINT=\n# S3_BUCKET=novelhub-books\n# S3_ACCESS_KEY=\n# S3_SECRET_KEY=\n\n"
env += "# WebDAV settings (when STORAGE_BACKEND=webdav)\n"
env += "# WEBDAV_URL=\n# WEBDAV_USER=\n# WEBDAV_PASS=\n"

with open(r"D:\git\novelhub\.env.example", "w", encoding="utf-8") as f:
    f.write(env)

print("1. .env.example updated")

# ---- Lock package.json versions ----
with open(r"D:\git\novelhub\frontend\package.json", "r", encoding="utf-8") as f:
    pkg = f.read()

pkg = pkg.replace('"vue":"latest"', '"vue":"^3.5"')
pkg = pkg.replace('"pinia":"latest"', '"pinia":"^2.2"')
pkg = pkg.replace('"vue-router":"latest"', '"vue-router":"^4.4"')
pkg = pkg.replace('"vite":"latest"', '"vite":"^6.0"')
pkg = pkg.replace('"typescript":"latest"', '"typescript":"^5.6"')
pkg = pkg.replace('"@vitejs/plugin-vue":"latest"', '"@vitejs/plugin-vue":"^5.2"')
pkg = pkg.replace('"tailwindcss":"^3.4"', '"tailwindcss":"^3.4"')

with open(r"D:\git\novelhub\frontend\package.json", "w", encoding="utf-8") as f:
    f.write(pkg)

print("2. package.json versions locked")

# ---- Clean temp files ----
import os
for f in ["_gen_backends.py", "_build_storage.py", "_b64_storage.txt"]:
    path = r"D:\git\novelhub\\" + f
    if os.path.exists(path):
        os.remove(path)

print("3. Temp files cleaned")

# ---- Update README ----
with open(r"D:\git\novelhub\README.md", "r", encoding="utf-8") as f:
    rm = f.read()

old = "- **NAS optimization** -- done (PostgreSQL tuned for UGREEN DX4600, resource limits, log rotation)"
new = "- **NAS optimization** -- done (PostgreSQL tuned for UGREEN DX4600, resource limits, log rotation)\n- **Storage abstraction** -- done (local filesystem, S3/MinIO, WebDAV; switch via STORAGE_BACKEND env)"
rm = rm.replace(old, new)

with open(r"D:\git\novelhub\README.md", "w", encoding="utf-8") as f:
    f.write(rm)

print("4. README updated")
