from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
from pathlib import Path

import edge_tts


VOICE = "en-US-AndrewMultilingualNeural"
RATE = "+4%"
PITCH = "-2Hz"


def read_scenes(script_path: Path) -> list[str]:
    source = script_path.read_text(encoding="utf-8")
    blocks = re.split(r"^## \d+ — .+$", source, flags=re.MULTILINE)[1:]
    scenes = [" ".join(block.strip().split()) for block in blocks]
    if len(scenes) != 8:
        raise RuntimeError(f"Expected 8 narration scenes, found {len(scenes)}")
    return scenes


async def synthesize(
    scenes: list[str], build_dir: Path, reuse_audio: bool
) -> list[Path]:
    outputs: list[Path] = []
    for index, narration in enumerate(scenes, start=1):
        output = build_dir / f"neural-{index:02d}.mp3"
        if reuse_audio and output.exists():
            outputs.append(output)
            print(f"reused={index}/{len(scenes)}")
            continue
        communicator = edge_tts.Communicate(
            narration,
            VOICE,
            rate=RATE,
            pitch=PITCH,
        )
        await communicator.save(str(output))
        outputs.append(output)
        print(f"narrated={index}/{len(scenes)}")
    return outputs


def media_duration(ffmpeg: Path, media: Path) -> float:
    probe = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(media)],
        capture_output=True,
        text=True,
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr)
    if not match:
        raise RuntimeError(f"Unable to determine duration for {media}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def render(ffmpeg: Path, docs_dir: Path, audio_files: list[Path]) -> Path:
    build_dir = docs_dir / "video-build"
    images = [
        docs_dir / "Volition_Hackathon_Cover.png",
        docs_dir / "video-assets" / "01-overview.png",
        docs_dir / "video-assets" / "02-market-pulse.png",
        docs_dir / "video-assets" / "03-strategy-lab.png",
        docs_dir / "video-assets" / "04-intelligence.png",
        docs_dir / "video-assets" / "05-decision-journal.png",
        docs_dir / "video-assets" / "06-decision-passport.png",
        docs_dir / "Volition_Hackathon_Cover.png",
    ]
    segments: list[Path] = []
    video_filter = (
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=#f4f0e5,"
        "format=yuv420p"
    )

    for index, (image, audio) in enumerate(zip(images, audio_files), start=1):
        if not image.exists():
            raise FileNotFoundError(image)
        segment = build_dir / f"neural-{index:02d}.mp4"
        duration = media_duration(ffmpeg, audio) + 0.4
        run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-loop",
                "1",
                "-framerate",
                "1",
                "-i",
                str(image),
                "-i",
                str(audio),
                "-vf",
                video_filter,
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=11,apad=pad_dur=0.4",
                "-t",
                f"{duration:.3f}",
                "-r",
                "1",
                "-c:v",
                "libx264",
                "-preset",
                "superfast",
                "-tune",
                "stillimage",
                "-crf",
                "28",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(segment),
            ]
        )
        segments.append(segment)
        print(f"rendered={index}/{len(images)}")

    concat_path = build_dir / "neural-concat.txt"
    concat_path.write_text(
        "\n".join(f"file '{segment.as_posix()}'" for segment in segments) + "\n",
        encoding="utf-8",
    )
    output = docs_dir / "Volition_Demo_Walkthrough.mp4"
    run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return output


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--reuse-audio", action="store_true")
    args = parser.parse_args()

    docs_dir = Path(__file__).resolve().parent
    build_dir = docs_dir / "video-build"
    build_dir.mkdir(parents=True, exist_ok=True)
    scenes = read_scenes(docs_dir / "VIDEO_WALKTHROUGH_SCRIPT.md")
    audio_files = await synthesize(scenes, build_dir, args.reuse_audio)
    output = render(args.ffmpeg.resolve(), docs_dir, audio_files)
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
