import time
import signal
import sys

running = True


def shutdown(sig, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


def main():
    print("NovelHub Scheduler started")
    while running:
        print("Scheduler heartbeat")
        time.sleep(60)
    print("Scheduler stopped")


if __name__ == "__main__":
    main()
