import time
import signal
import sys


running = True


def shutdown(sig, frame):
    global running
    print("Scheduler stopping...")
    running = False



signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)



def main():

    print("NovelHub Scheduler started")


    while running:

        print("Scheduler heartbeat")


        # Stage 0 占位
        # Stage 1 接入 Celery Beat


        time.sleep(60)



    print("Scheduler stopped")



if __name__ == "__main__":

    main()
