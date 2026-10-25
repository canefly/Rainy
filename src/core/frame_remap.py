import av
import sys
import time
from bisect import bisect_left


class VideoLookup:
    def __init__(self, video_path):
        self.video_path = video_path
        self.frame_pts = []
        self.time_base = None
        self.nominal_fps = None

    @staticmethod
    def parse_timestamp(timestamp):
        parts = timestamp.split(":")

        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])

            return minutes * 60 + seconds

        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])

            return hours * 3600 + minutes * 60 + seconds

        raise ValueError("Invalid timestamp format")

    def build_frame_map(self):
        video = av.open(self.video_path)
        stream = video.streams.video[0]

        stream.thread_type = "AUTO"

        self.time_base = stream.time_base

        average_fps = float(stream.average_rate)

        if abs(average_fps - 23.976) < 0.1:
            self.nominal_fps = 24
        elif abs(average_fps - 29.97) < 0.1:
            self.nominal_fps = 30
        elif abs(average_fps - 59.94) < 0.1:
            self.nominal_fps = 60
        else:
            self.nominal_fps = round(average_fps)

        print("Codec:", stream.codec_context.name)
        print("Resolution:", stream.width, "x", stream.height)
        print("Average FPS:", average_fps)
        print("Time Base:", self.time_base)
        print("Indexing frames...")

        start = time.perf_counter()

        for i, frame in enumerate(video.decode(stream)):
            if frame.pts is None:
                continue

            self.frame_pts.append(frame.pts)

            if i % 1000 == 0:
                elapsed = time.perf_counter() - start
                print("Frames indexed:", i, "| Elapsed:", round(elapsed, 2), "sec")

        video.close()

        elapsed = time.perf_counter() - start

        print("Frames indexed:", len(self.frame_pts))
        print("Nominal FPS:", self.nominal_fps)
        print("Indexing completed in:", round(elapsed, 2), "sec")

    def find_frame(self, timestamp):
        target_pts = round(timestamp / float(self.time_base))

        position = bisect_left(self.frame_pts, target_pts)

        if position == 0:
            return 0

        if position >= len(self.frame_pts):
            return len(self.frame_pts) - 1

        before = self.frame_pts[position - 1]
        after = self.frame_pts[position]

        if target_pts - before <= after - target_pts:
            return position - 1

        return position

    def frame_to_timecode(self, frame_number):
        fps = self.nominal_fps

        frames = frame_number % fps
        total_seconds = frame_number // fps

        seconds = total_seconds % 60
        minutes = (total_seconds // 60) % 60
        hours = total_seconds // 3600

        return f"{hours:02}:{minutes:02}:{seconds:02}:{frames:02}"

    def remap(self, timestamp):
        frame_number = self.find_frame(timestamp)

        actual_pts = self.frame_pts[frame_number]
        actual_time = float(actual_pts * self.time_base)

        timecode = self.frame_to_timecode(frame_number)

        print("Input timestamp:", timestamp)
        print("Actual PTS:", actual_pts)
        print("Actual time:", actual_time)
        print("Frame number:", frame_number)
        print("Premiere timecode:", timecode)

        return timecode


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python frame_remap.py video.mp4 MM:SS")
        sys.exit(1)

    video_path = sys.argv[1]

    try:
        timestamp = VideoLookup.parse_timestamp(sys.argv[2])
    except ValueError:
        print("Invalid timestamp. Use MM:SS or HH:MM:SS")
        sys.exit(1)

    vl = VideoLookup(video_path)

    vl.build_frame_map()
    vl.remap(timestamp)