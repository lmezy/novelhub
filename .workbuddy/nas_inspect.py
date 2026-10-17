"""One-off: inspect the novelhub deployment on the NAS via SSH (password auth)."""
import sys
import paramiko

HOST = "nas.19961113.xyz"
PORT = 10022
USER = "894654222"
PASSWORD = "Lzy.lonezy.l23"

BASE = "/volume1/docker/NovelHub/novelhub"

CMDS = [
    ("env_keys", f"sed 's/=.*/=***/' {BASE}/.env"),
    ("nginx_conf", f"cat {BASE}/nginx/default.conf"),
    ("dir_listing", f"ls -la {BASE}/"),
    ("images", "docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedSince}}' | grep -i novelhub"),
    ("git_state", f"cd {BASE} && git log --oneline -3 2>/dev/null; git status -s 2>/dev/null | head -10"),
]


def main() -> None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=20)
    try:
        for label, cmd in CMDS:
            print(f"===== {label} =====")
            _, out, err = client.exec_command(cmd, timeout=45)
            print(out.read().decode("utf-8", "replace"))
            e = err.read().decode("utf-8", "replace").strip()
            if e:
                print("[stderr]", e[:300])
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
