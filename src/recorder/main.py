import time
from datetime import datetime
from pathlib import Path

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput

CONFIG = {
    "recordings_dir": Path.home() / "Desktop" / "Recordings",
    "video_size": (1920, 1080),
    "bitrate": 200000,
    "rotation_hours": 1,
}


def create_filename():
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    daily_folder = CONFIG["recordings_dir"] / date_str
    daily_folder.mkdir(parents=True, exist_ok=True)

    return daily_folder / f"recording_{timestamp}.h264"


def main():
    CONFIG["recordings_dir"].mkdir(parents=True, exist_ok=True)

    picam2 = Picamera2()
    video_config = picam2.create_video_configuration(
        main={"size": CONFIG["video_size"]}
    )
    picam2.configure(video_config)

    encoder = H264Encoder(bitrate=CONFIG["bitrate"])

    picam2.start()
    try:
        while True:
            filename = create_filename()
            output = FileOutput(str(filename))

            print(f"Starting recording: {filename}")
            picam2.start_encoder(encoder, output)

            time.sleep(CONFIG["rotation_hours"] * 3600)
            picam2.stop_encoder()

            print(f"Recording stopped: {filename}")
    except KeyboardInterrupt:
        print("Recording stopped by user")
    finally:
        picam2.stop_encoder()
        picam2.stop()


if __name__ == "__main__":
    main()
